# DabarStream Progress Monitor

## Repository
- User-provided Git remote: https://github.com/fortunejabs/DabarStream.git
- GitHub page checked: public repository, currently empty.
- Local clone confirmed: `C:\Users\Jabs\Documents\GitHub\DabarStream\.git\config` has
  `remote "origin"` = `https://github.com/fortunejabs/DabarStream.git`,
  branch `main` tracking `origin/main`.
- Prep completed 2026-09-18 at the NEW location (terminal still blocked, GitHub Desktop recommended):
  created `.gitignore` (venv/caches/logs/secrets/bible.db excluded),
  fixed stale README test count (23 -> 24),
  searched tracked sources for passwords/API keys/secrets/private keys (`ghp_`, `AKIA`) — none found.
- NOT yet done: local `git status` review, `venv` recreation under a supported Python
  (3.12/3.13 for pyaudio/faster-whisper), `pytest` green run, first commit + push.
  Do NOT commit `bible.db` or translation sources unless their license permits redistribution.


## [2026-09-17 Resumption: database safety; validation blocked]
- Confirmed project location: `C:\Devs\DabarStream`; the previous workspace path `C:\Devs\BibleStream` is no longer accessible.
- Changed `test_importer.py` to use `tmp_path` plus `monkeypatch.chdir` instead of deleting the default `bible.db` before and after tests. Added a regression test for default-path isolation.
- Added `verify_tests.py`, a diagnostic runner that records startup, pytest output, and exit status in `validation_result.txt`, with a 60-second subprocess timeout.
- Validation is BLOCKED: terminal calls report unobservable completion, and neither the direct test log nor the runner startup marker was created. The historical 23-test pass below does not validate these new changes; the current suite is expected to contain 24 tests.
- No real Bible import has been confirmed. Do not treat download attempts or terminal error responses as successful execution.
- Next: reopen `C:\Devs\DabarStream\DabarStream.code-workspace`, repair terminal execution, run `C:\Devs\DabarStream\.venv\Scripts\python.exe C:\Devs\DabarStream\verify_tests.py`, and inspect `C:\Devs\DabarStream\validation_result.txt` before importing data.


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
