# DabarStream Progress Monitor

## [2026-09-18 BULK BIBLE IMPORT: 33 translations, 30 imported / 3 pending]
- `import_bibles.py` bulk-loads every XML under
  `Bibles/Holy-Bible-XML-Format-master/` into `bible.db`, keyed by
  `translation_code`, with a resumable manifest
  (`Bibles/_import_manifest.json`, git-ignored) so re-runs skip finished files.
- **Imported so far (30 files, ~900k rows):** 6 Zambian files (`bem`, `nya`,
  `bem_chibemba`, `nya_1992`, `nya_2014`, `nya_blydc`) + 24 English (`eng` KJV,
  `eng_asv`, `eng_darby`, `eng_ylt`, `eng_tyndale`, `eng_amp`, `eng_ampc`,
  `eng_niv`, `eng_esv`, `eng_nlt`, `eng_nkjv`, `eng_nasb`, `eng_csb`, `eng_mev`,
  `eng_lsb`, `eng_gw`, `eng_net`, `eng_msg`, `eng_gnb`, `eng_hcsb`, `eng_nivuk`,
  `eng_rnkjv`, `eng_bbe`, `eng_akjv`).
- **Still pending (3):** `eng_kj2000`, `eng_bwe`, `eng_isv` — they were blocked
  by the parser bugs below, now fixed, so the next `import_bibles.py` run
  completes them.
- **Four parser bugs found and fixed** (each has a regression test in
  `test_importer.py`: +9 tests, suite **58 -> 67**):
  1. **`<VERS>` vs `<VERSE>`** — the "other Bibles" files use Zefania's `VERS`
     tag; the parser only matched `VERSE`, so they imported **0 verses**. This is
     why an earlier run printed "(0)" for many codes.
  2. **`<BIBLEBOOK bnumber="1">` with NO `bname`** (King James 2000) made
     `bname` `None` -> `AttributeError: 'NoneType' object has no attribute
     'split'`, which aborted the entire run mid-list. The canonical English name
     is now derived from the book number.
  3. **Localised `bname` leaked into the shared key** — ISV literally ships
     German (`bname="Matthäus"`), which would have poisoned the
     `book_normalized` column. Book names are now *always* the canonical English
     66; local-language names are matched via `importer.BOOK_LOCAL_TO_ENGLISH`
     and `server.BOOK_ALIASES` instead.
  4. **Inline markup truncated verses** — KJ2000 wraps red-letter text in
     `<STYLE css=...>`, and `v.text` stops at the first child element, so
     *"And God said, Let there be light: and there was light."* was stored as
     just *"And God said, "* (a real data-loss bug, not just cosmetics). All
     three parsers now use `itertext()`.
- **`import_file()` is now fault-isolated:** one bad file prints
  `ERROR importing ...` and the run **continues** to the remaining translations;
  a file that parses 0 verses is reported and deliberately **not** recorded in
  the manifest so a re-run retries it; junk rows/chapters are skipped instead of
  raising (`_int()` None-safe coercion).
- **`sys.exit(1)` on missing Bible files moved out of module scope into
  `main()`** (`missing_files()`), so `import_bibles.py` is importable — and
  therefore unit-testable — on a machine without the `Bibles/` tree.
- Known data note: `nya_blydc` (Chewa BLYDC) contains only **64 books** at
  source (Esther/Daniel/Joel/Nehemiah region absent). English fallback covers
  the gaps; flagged here for the translation inventory.

### Next command (user side)
```powershell
.venv\Scripts\python.exe import_bibles.py      # finishes KJ2000, BWE, ISV
.venv\Scripts\python.exe -m pytest test_importer.py test_language.py test_slides.py   # expect 67
```

## [2026-09-18 SUITE GREEN: 58/58 passed]
- Full suite verified by the user in their terminal (Python 3.12.8, pytest 9.1.1):
  test_importer.py (14) + test_language.py (32) + test_slides.py (12) = **58 passed**.
- Three bugs found and fixed via repair scripts (my terminal/read channels were
  unreliable this session; user ran commands and I verified via fresh files):
  1. test_slides.py was corrupted (stray `"""` + duplicated test blocks) ->
     rewritten cleanly via _repair_slides.py.
  2. server.py line 612 IndentationError (stray over-indent in api_save_project)
     -> fixed; parse_verse_reference rewritten to run-based spoken-number
     parsing ("Psalms twenty three verse one" -> 23:1; trailing 'chapter'
     stripped from book names).
  3. resolve_and_query_bible froze db_path=DB_PATH at import time ->
     default is now None with call-time `db_path = db_path or DB_PATH`
     (monkeypatched paths in tests and runtime overrides now work).
- Slide line limit lowered 500 -> 100 chars (lower-third friendly).
- Scratch files to delete before committing: _repair3.py, _repair4.py,
  _diag2.py, _diag3.py, _diag4.py, _diag5.py, _diag2-5.txt.
- Ready to commit + push (repo private: github.com/fortunejabs/DabarStream).


> Consolidated 2026-09-18. Earlier entries had accumulated duplicate/contradictory
> blocks across agent sessions; this file is now the single source of truth.

## Repository
- Remote: https://github.com/fortunejabs/DabarStream.git (private)
- Canonical local folder: `C:\Users\Jabs\Documents\GitHub\DabarStream\`
- Branch `main` tracks `origin/main`. User commits/pushes manually (agent has no git write tool).
- `.gitignore` excludes `.venv/`, `__pycache__/`, `.pytest_cache/`, `*.log`, `bible.db`/`*.db`/`*.sqlite*`, `.env`/secrets.
- `.gitattributes` forces LF for `deploy_vps.sh` + `dabarstream.service` (CRLF/BOM breaks systemd + bash).

## Current status
- Environment `.venv` recreated with **Python 3.12.8** (was a non-portable copy of a 3.15 venv).
  `pip install -r requirements.txt` succeeded for every package including `pyaudio`,
  `faster-whisper`, `pynput`.
- Last user-run `pytest` (BEFORE the P0 tests were added): **24 passed**
  (`test_importer.py` 10 + `test_language.py` 14).
- **Agent terminal is blocked in this environment** — commands return the user's stale
  shell buffer. All agent verification is by direct file reads. The user must run
  `pytest` to confirm the current count (it is now higher than 24).

## P0 code fixes (present in source, verified by file reads)
1. **Shared-secret auth** — `server.py`: `is_authorized()` + `DABARSTREAM_KEY` env var,
   timing-safe `hmac.compare_digest`, enforced on all three Socket.IO events
   (`verse_triggered`, `set_language`, `clear_overlay`). Dev mode (key unset) allows
   everything; production requires the key. `client.py`: `STREAM_KEY` attached to both emits.
2. **Payload validation** — `server.py`: `validate_verse_payload()` rejects non-dicts,
   empty/over-long books (>80 chars), non-integer and out-of-range chapter/verse
   (1-150 / 1-176), defaults unknown languages to `eng`. Malformed payloads are ignored
   instead of raising `KeyError`.
3. **`clear_overlay`** — handler in `server.py`, Clear button in the `/control` panel,
   and a `clear_overlay` listener in the OBS overlay.
4. **Number-word support** — `client.py`: `NUMBER_WORDS`, `words_to_number()`,
   `word_verse_pattern`, `parse_verse_reference()` so "chapter three verse sixteen"
   triggers John 3:16 (previously digits were required).
5. **Service hardening** — `dabarstream.service` refuses to start until
   `Environment=DABARSTREAM_KEY=change-me-before-deploy` is replaced with a real secret
   (`ExecStartPre` guard).

## P0 tests added
`test_language.py` now also covers: `is_authorized` accept/reject, `validate_verse_payload`
accept/reject, unauthorized events rejected, malformed payload ignored without broadcast,
client emits carrying the stream key, `parse_verse_reference`, `words_to_number` edges,
spoken words becoming a verse trigger.

## Feature roadmap (2026-09-18 - FreeShow-class feature request)

Phase 1 (DONE, this commit): Slides/Projects seed (`show_slide` event,
`validate_slide_payload`), Timers (`timer_control`, `validate_timer_payload`,
start/stop/clear), Stage display (`/stage` text-only confidence monitor with
clock + verse/slide/timer), null-safe control-panel JS wiring (activates as
soon as slide/timer inputs are added to CONTROL_HTML), 12 new tests in
`test_slides.py` (suite now 54).

Phase 2 (next): full Project list UI (save/load projects as JSON), slide
themes + transitions (CSS classes), preview pane in control panel, remote
controller (phone-friendly panel), multiple outputs (per-output overlay URLs).
Phase 3: media library + overlays (images/videos behind slides), music lyrics
timing, chord entry/display, auto labels from CCLI-style metadata.
Phase 4: integrations - NDI (via OBS+DistroAV first; direct NDI later),
Blackmagic (ATEM via IP), MIDI triggers, YouTube live captions, Planning
Center import, CCLI reporting, calendar, draw annotations, PDF/PPT import,
cloud sync (git-friendly JSON project files), localization (i18n of UI),
cross-platform clients (client.py already pure Python; add Mac/Linux notes).

Architecture note: browser-source outputs mean "multiple outputs / mirror /
stage display" are new served pages subscribing to the same Socket.IO events,
not new video pipelines. Media-heavy phases belong on the streaming PC
(client side), keeping the VPS lean.

## Path sweep (old `C:\Devs\BibleStream`)
No live references remain in source, tests, deploy scripts, docs, or workspace files.
`DabarStream.code-workspace` uses portable `"path": "."`. The only historic mentions are in
this log. (The stale `.venv/pyvenv.cfg` was resolved when the venv was recreated on 3.12.)

## Multilingual feature set (earlier work, still present)
- `importer.py`: FreeShow JSON, BibleShow delimited, EasyWorship CSV, Zefania/OpenSong XML,
  all carrying `translation_code`; canonical English book keys via `BOOK_LOCAL_TO_ENGLISH`.
- `server.py`: translation-bound queries with **English fallback**, multilingual
  `BOOK_ALIASES` (Yohane / Ututendelo / Machingonzi...), `TRANSLATION_LABELS`,
  dual-language overlay.
- Three live language-switch paths: voice phrases (`LANG_COMMANDS`), hotkeys
  (1=eng 2=bem 3=nya 4=ton), and the `/control` panel.

## Known stale docs
- `README.md` now says "67 tests" (58 + the 9 new `import_bibles.py` parser
  tests) and documents the bulk import. If the next `pytest` run returns a
  different count, correct it there and in the bulk-import section above.

## Pending (user side)
1. Run `import_bibles.py` once more to finish `eng_kj2000` / `eng_bwe` /
   `eng_isv`, then run `pytest` and confirm **67 passed**.
2. Bulk translation import is now done via `import_bibles.py` (33 translations).
   `importer.py`'s single-file helpers remain for one-off/FreeShow/EasyWorship
   imports.
3. Extend `BOOK_LOCAL_TO_ENGLISH` / `BOOK_ALIASES` to full 66-book maps per
   language. **The Zambian XMLs carry no book names at all**, so Chewa/Bemba
   spoken references (e.g. "Yohane", "Chiyambi", "Machitidwe") need alias
   entries before voice triggering works in those languages.
4. Switch `MODEL_SIZE` to the multilingual `small` model for spoken
   Bemba/Nyanja/Tonga.
5. Set a real `DABARSTREAM_KEY` BEFORE running `deploy_vps.sh` (same value on VPS service
   file, streaming-PC `$env:DABARSTREAM_KEY`, and the /control password field).
6. Deploy to the VPS; add the OCI VCN Ingress Rule for TCP 5000 (script cannot do this).
7. Verify the overlay in OBS and the `/control` panel.
8. Copy `bible.db` (~900k rows) to the VPS — it is git-ignored, so it must be
   transferred separately (scp/rsync) and regenerated there if the file is
   licenced-material-sensitive.

## Superseded history (older log entries, kept for provenance)
- Path sweep (direct file reads, full tree visible via read_files; search index stale):
  workspace uses portable `"path": "."` (no machine paths anywhere); server/client/
  importer/tests/README contain NO `C:\Devs\BibleStream` references; README banner
  names `C:\Users\Jabs\Documents\GitHub\DabarStream\` as canonical.
  ONLY stale hit: `.venv/pyvenv.cfg` (home=Python315, command=...C:\Devs\BibleStream\.venv)
  — copied venv, must be deleted + recreated with 3.12 (user already did once; re-check
  if venv was re-copied). `verify_tests.py` uses `Path(__file__).parent` (portable).
- P0 code fixes VERIFIED present in files (no new code needed):
  server.py: `is_authorized()` + `DABARSTREAM_KEY` on all 3 events, `validate_verse_payload()`,
  `clear_overlay` handler + control-panel Clear button + overlay listener;
  client.py: `STREAM_KEY` (env) on both emits, `NUMBER_WORDS`/`words_to_number()`/
  `parse_verse_reference()`.
- Tests for P0 PRESENT: `test_is_authorized_*`, `test_validate_verse_payload_accepts_valid`,
  `test_validate_verse_payload_rejects_malformed`, `test_unauthorized_events_are_rejected`,
  `test_malformed_verse_payload_is_ignored_without_broadcast`, `test_client_emits_carry_stream_key`,
  `test_parse_verse_reference_*`, `test_words_to_number_edge_cases`,
  `test_spoken_words_become_verse_trigger`, `test_verse_trigger_carries_stream_key`.
- NEW FIX APPLIED: `dabarstream.service` previously shipped
  `Environment=DABARSTREAM_KEY=change-me-before-deploy`, which would let the VPS start
  with a publicly-known key (= no real auth). Service now has an `ExecStartPre` guard
  that refuses to start until the key is changed to a real secret.
- DEPLOY ORDER WARNING for user: set the real key in the .service file BEFORE first
  `sudo bash deploy_vps.sh` (the script installs+starts the service in one step).
  Same value as streaming-PC `$env:DABARSTREAM_KEY` and the /control password field.
- Canonical development folder is `C:\Users\Jabs\Documents\GitHub\DabarStream\`.
  Confirmed: no live references to the old `C:\Devs\BibleStream` path in tracked
  source, deploy scripts, docs, or workspace files (only historic mentions inside
  this PROGRESS.md log itself and the stale `.venv/pyvenv.cfg`, both now fixed).
  README canonical-path banner corrected: it wrongly repeated the new path twice;
  it now names the new folder as canonical and `C:\Devs\BibleStream` as the old one.
- P0 fixes VERIFIED present in the files (no new edits needed):
  server.py has `is_authorized()` / `DABARSTREAM_KEY` checks on `verse_triggered`,
  `set_language`, `clear_overlay` plus `validate_verse_payload()`; client.py has
  `STREAM_KEY` attached to `set_language` + `verse_triggered` emits, `NUMBER_WORDS`,
  `words_to_number()`, `word_verse_pattern`, `parse_verse_reference()`; overlay +
  control panel both handle `clear_overlay` with a Clear button.
- Search index is stale (queries return "Searched 0 files") so `search_codebase`
  cannot audit the tree; all verification above was done with direct file reads.
- STILL NEEDS (user side — agent terminal is blocked): activate `.venv`, run
  `pytest` (expect 24 currently; consider adding P0 tests below), review diff in
  GitHub Desktop, commit, push.
- Suggested NEW TESTS to lock in the P0 fixes (not yet written — next code task):
  auth accept/reject for `is_authorized` incl. timing-safe compare and unset-key
  dev mode; `validate_verse_payload` accept/reject incl. over-long book,
  out-of-range chapter/verse, unknown lang defaulting; number-word parsing
  (`words_to_number`, `parse_verse_reference` for "chapter three verse sixteen");
  `clear_overlay` handler broadcasting; key present on client emits.

## Repository
- User-provided Git remote: https://github.com/fortunejabs/DabarStream.git
- GitHub repo is now **private** (was public/empty).
- New local clone: `C:\Users\Jabs\Documents\GitHub\DabarStream` — `.git/config` verified:
  `remote "origin"` = `https://github.com/fortunejabs/DabarStream.git`,
  `.git/HEAD` = `ref: refs/heads/main`, branch `main` tracking `origin/main`.
- Prep verified 2026-09-18 at the NEW location (file reads only; terminal still blocked):
  `.gitignore` covers `.venv/`, `__pycache__/`, `.pytest_cache/`, `*.log`,
  `bible.db`/`*.db`/`*.sqlite*`, `.env`/secrets, OS/editor noise.
  Searched tracked sources for passwords/API keys/secrets/private keys (`ghp_`, `AKIA`) — none found.
  `DabarStream.code-workspace` uses `"path": "."` so it is portable to the new folder.

## [2026-09-18 Resumption: moved to GitHub folder, venv is stale]
- Files copied to `C:\Users\Jabs\Documents\GitHub\DabarStream`.
- PROBLEM FOUND: `.venv/pyvenv.cfg` still points at the OLD interpreter:
  `home = C:\Users\Jabs\AppData\Local\Programs\Python\Python315` and
  `command = ... -m venv C:\Devs\BibleStream\.venv`, version 3.15.0.
  A copied venv is NOT portable — delete `.venv` in the new folder and recreate it.
  Python 3.15 beta also has no wheels for `pyaudio`/`faster-whisper`; use Python 3.12/3.13
  for the venv if available (server/tests run on 3.15, but `client.py` audio needs 3.12/3.13).
- Re-verified 2026-09-18: agent terminal STILL blocked (even bare `echo TERMINAL_OK`
  returns unobservable completion). File reads work. All fixable work below is file-based
  or manual on the user's side. NO terminal validation has been possible in this session.
- NOT yet done: delete + recreate `.venv`, `pytest` green run, GitHub Desktop review,
  first commit + push. Keep `bible.db`/translation sources out unless licensed for
  redistribution (repo is private now, but keep the rule anyway).
- Manual first-push checklist (run these yourself — agent terminal is blocked):
  1. Delete `C:\Users\Jabs\Documents\GitHub\DabarStream\.venv` (stale copy).
  2. New PowerShell here: `py -3.12 -m venv .venv` (or 3.13; avoid 3.15 beta for audio wheels).
  3. `.\.venv\Scripts\Activate.ps1; pip install -r requirements.txt; pytest` (expect 24 passed).
  4. GitHub Desktop: review Changes, ensure `.venv/`, `__pycache__/`, `*.db`, `*.log` are absent.
  5. Commit `Initial DabarStream commit` on `main`, then Push origin.
## [2026-09-18 VERIFIED: 24/24 tests pass on Python 3.12]
- User ran the suite manually in a fresh `.venv` (Python 3.12.8, pytest 9.1.1):
  `test_importer.py ..........` (10 passed) + `test_language.py ..............` (14 passed)
  = **24 passed in 0.99s**. Full `pip install -r requirements.txt` also confirmed —
  flask, flask-socketio, python-socketio, pytest, numpy, pyaudio, faster-whisper, pynput
  all installed. pip upgraded 24.3.1 -> 26.1.2.
- No code changes were needed: the suite is green. Terminal from the agent side is still
  blocked, so this result comes from the user's own PowerShell run.
- User has started manual commit/push via GitHub Desktop. NOTE: agent has no git-write
  tool in this environment — commits/pushes must happen in the user's terminal or
  GitHub Desktop. Agent keeps every change small, tested, and documented (commit-ready).
- Production-readiness review delivered 2026-09-18 (P0: real bible.db data, VPS deploy,
  Socket.IO shared-secret auth; P1: reconnect handling, STT worker thread, number-word
  regex, verse ranges; P2: SQLite WAL, logging, clear_overlay, config file, full
  66-book alias maps). Offered: shared-secret auth + clear_overlay + number-word support.
- 2026-09-18 auto-commit/push request: NOT possible from agent (no git-write tool;
  agent-side terminal unobservable — verified again this session). You commit/push via
  your own PowerShell or GitHub Desktop; I keep changes small and commit-ready.


## [2026-09-17 Resumption: database safety; validation blocked]
- Confirmed project location: `C:\Users\Jabs\Documents\GitHub\DabarStream`; the previous workspace path `C:\Devs\BibleStream` is no longer accessible.
- Changed `test_importer.py` to use `tmp_path` plus `monkeypatch.chdir` instead of deleting the default `bible.db` before and after tests. Added a regression test for default-path isolation.
- Added `verify_tests.py`, a diagnostic runner that records startup, pytest output, and exit status in `validation_result.txt`, with a 60-second subprocess timeout.
- Validation is BLOCKED: terminal calls report unobservable completion, and neither the direct test log nor the runner startup marker was created. The historical 23-test pass below does not validate these new changes; the current suite is expected to contain 24 tests.
- No real Bible import has been confirmed. Do not treat download attempts or terminal error responses as successful execution.
- Next: reopen `C:\Users\Jabs\Documents\GitHub\DabarStream\DabarStream.code-workspace`, repair terminal execution, run `C:\Users\Jabs\Documents\GitHub\DabarStream\.venv\Scripts\python.exe C:\Users\Jabs\Documents\GitHub\DabarStream\verify_tests.py`, and inspect `C:\Users\Jabs\Documents\GitHub\DabarStream\validation_result.txt` before importing data.


## [2026-09-17 Rename + Fix]
- **Project renamed:** BibleStream -> **DabarStream** (Dabar = Hebrew "the active, event-creating word"; Stream = real-time routing of spoken scripture to screens).
  - Renamed: `biblestream.service` -> `dabarstream.service`, `BibleStream.code-workspace` -> `DabarStream.code-workspace`; service user/group/dir now `dabarstream` / `/opt/dabarstream`.
  - `deploy_vps.sh` refactored to use `SERVICE_NAME`/`APP_DIR`/`SERVICE_USER` variables instead of hardcoded names.
  - Branding updated in `README.md` (title + Dabar etymology note), `PROGRESS.md`, headers of `importer.py` / `server.py` / `client.py`, and the `/control` panel heading.
  - Verified: 0 remaining `biblestream` references across all source/doc/deploy files.
- **BUG FOUND & FIXED during rename:** an editor edit to `deploy_vps.sh` had left the script body **duplicated** (new lines 1-47 followed by a stale copy at lines 50-85 carrying the old `biblestream` service names). Running it would have executed the whole deploy body twice and installed the wrong service name. Truncated back to the correct 47 lines.
- **Line-ending hygiene fixed:** the repair pass had written `deploy_vps.sh` / `dabarstream.service` with a UTF-8 BOM and CRLF, which breaks bash (`\r` becomes part of every value, BOM breaks the shebang) and systemd. Both files normalized to **LF, no BOM**; Python/Markdown files remain CRLF to match the repo.
- Full suite re-run after all changes: **23/23 PASSED** (test_importer.py 9 + test_language.py 14).

## [2026-09-16 Status Update]
- **Target VPS Anchor:** 193.123.179.93 (port 5000)
- **Completed Components:**
  - Architected cloud-split infrastructure (local Windows capture client -> OCI VPS engine -> OBS browser-source overlay).
  - `importer.py`: **multilingual** database mapping engine — FreeShow JSON, BibleShow delimited text, EasyWorship CSV, **and Zefania/OpenSong XML** (African-language modules), all with `translation_code` (default 'eng'); canonical English book keys via `BOOK_LOCAL_TO_ENGLISH` (Nyanja/Chewa, Bemba, Tonga seeds) so any language's display name stays intact while remaining searchable.
  - `server.py`: **multilingual** resolver — `translation_code`-bound queries, English fallback when a language lacks a verse, multilingual BOOK_ALIASES (Yohane/Ututendelo/Machingonzi...), `TRANSLATION_LABELS`, and **dual-language OBS overlay** (local language primary + English secondary line).
  - `client.py`: emits `lang` field (`DEFAULT_LANG` config, e.g. 'bem', 'nya', 'ton').
  - **Live language switching wired in (3 paths):**
    - Voice: `LANG_COMMANDS` phrase map + `detect_language_command()` (longest phrase wins); switches take priority over verse detection and apply to all following verse triggers via module state `ACTIVE_LANG`.
    - Hotkeys: `HOTKEY_LANGS` (1=eng 2=bem 3=nya 4=ton) via optional `pynput` global listener, started from `main()`; degrades silently if pynput is absent.
    - Control panel: `server.py` serves `/control` — clickable language buttons + manual book/chapter/verse sender.
    - Server side: pure `normalize_lang()` validator (rejects unknown/traversal codes) guards `set_language`; `handle_set_language` broadcasts `language_changed`; overlay shows a 3s banner announcing the new translation.
  - `test_language.py`: **14 new tests** covering voice command parsing, longest-phrase precedence, state/emit on switch, switch-then-tag verse flow, command-not-a-verse isolation, `normalize_lang` accept/reject, label coverage of all hotkey languages, and an end-to-end 'nya' vs 'eng' lookup. Audio deps are stubbed into `sys.modules` so the real `client.py` logic is exercised without pyaudio/faster-whisper.
  - Full suite: `test_importer.py` (9: schema init, 3 import formats, alias resolution, e2e query, multilingual canonical keys, translation coexistence, language fallback) + `test_language.py` (14) = **23/23 PASSED**.

  - `.venv` created on Windows (Python 3.15.0b3) with pytest, flask, flask-socketio, python-socketio installed and verified.
- **Active Operations:**
  - None — environment validated and green (all features wired and tested).
- **Current Roadblocks:**
  - Local interpreter is Python 3.15.0b3 (beta) — `pyaudio` / `faster-whisper` / `pynput` do not ship wheels for it yet. To run `client.py`, install Python 3.12/3.13, recreate the venv with it, and `pip install pyaudio numpy faster-whisper pynput`.
- **Pending Tasks:**
  - Import a real public-domain Bible translation into `bible.db` (uncomment a call in `importer.py` and run it).
  - Extend `BOOK_LOCAL_TO_ENGLISH` (importer.py) and `BOOK_ALIASES` (server.py) to the full 66-book maps for each Zambian language as modules are imported — current entries are verified seeds, not exhaustive.
  - Switch `MODEL_SIZE` in `client.py` to the multilingual `small` model for spoken Bemba/Nyanja/Tonga recognition (`base.en` is English-only).
  - Copy `server.py`, `importer.py`, `dabarstream.service`, `deploy_vps.sh` + `bible.db` to the VPS and run `sudo bash deploy_vps.sh` (handles deps, `dabarstream` user, firewalld port 5000, and systemd enable).
  - REMINDER: also add the OCI VCN Ingress Rule for TCP 5000 in the Cloud Console (deploy script cannot do this).
  - Verify overlay in OBS Browser Source (http://193.123.179.93:5000/overlay, 1920x1080).
  - Verify the control panel at http://193.123.179.93:5000/control.
  - Optional: DistroAV (obs-ndi) for NDI output from OBS to vMix/Wirecast/etc.
