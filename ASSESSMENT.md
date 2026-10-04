# DabarStream — System Assessment, Defect Report & Production-Readiness Review

**Prepared for:** operator + external AI reviewers (Gemini, Claude, GPT)
**Repository:** `C:\Users\Jabs\Documents\GitHub\DabarStream`
**Deployment:** Oracle Linux 10 VPS `193.123.179.93:5000`, systemd unit `dabarstream`
**Date of review:** 2026-10-03

> **Reader warning — how this report was produced**
> The reviewing agent's shell integration was **non-functional** for this session
> (every `run_commands` invocation returned a stale terminal buffer and created no
> output file). Therefore:
> * every claim below is from **reading the real source files**, not from execution;
> * no test suite was executed by the reviewer;
> * items marked **[UNVERIFIED]** need a human to run the command in §8.
> Treat "fixed" as "the source now contains the fix, pending a test run".

---

## 1. What the system is

Church scripture-overlay tooling. A Windows PC listens to the preacher's
microphone, transcribes locally, detects Bible references in speech, and pushes
them to browser overlays rendered inside OBS.

```
[Windows PC]                         [Oracle Linux 10 VPS]                [OBS / browsers]
client.py                            server.py
 mic ─▶ faster-whisper (base.en,int8) ─┐
        parse_verse_reference()        │  socket.io  ┌─▶ GET /overlay  (lower-third, transparent)
        detect_language_command()      ├────────────▶│   GET /stage    (confidence monitor)
        emits verse_triggered /        │             │   GET /control  (manual panel)
        set_language                   │             └─▶ GET /health, /api/verse, /test
                                       │
                                       └─▶ SQLite bible.db (979,588 rows, 33 translation codes)
```

### Components

| File | Role | Size / state |
|---|---|---|
| `server.py` | Flask + Flask-SocketIO. Aliases, validation, SQLite, broadcast, 3 HTML surfaces, Projects API | ~1,120 lines, single module, no package layout |
| `client.py` | Windows voice client: capture → Whisper → reference parsing → Socket.IO emit | ~510 lines, runs in `.venv` (Python 3.12) |
| `importer.py` | Loads FreeShow JSON / BibleShow text / EasyWorship CSV / Zefania & OpenSong XML into SQLite | ~320 lines |
| `test_language.py` | pytest suite (stubbed audio deps, real logic under test) | 46 tests before this review, **55 expected now** |
| `bible.db` | 979,588 verses; `eng` + 26 `eng_*` + `bem` + `nya` + `nya_*`. **No `ton`** | ~1 GB class, deployed on VPS |
| `run_client.ps1` | Launcher; activates `.venv`, loads the stream key, runs `client.py` | fixed this review |
| `stream_key.txt` | **NEW** — git-ignored one-line secret, read by both client and launcher | created this review |

### Routes

| Route | Auth | Notes |
|---|---|---|
| `GET /overlay` | none | transparent lower-third; `?debug=1`, `?hold=1`, deep-link `?book=&chapter=&verse=&lang=` |
| `GET /stage` | none | opaque confidence monitor, clock + timer |
| `GET /control` | none (writes need key) | manual panel; key persisted in `localStorage` |
| `GET /health` | none | JSON liveness + DB readiness |
| `GET /api/verse` | none | read-only resolver, powers overlay previews |
| `GET /test` | **key** | broadcasts a canned verse |
| `GET /js/socket.io.js` | none | serves vendored `static/socket.io.js`, else 302 to cdnjs |
| `GET/POST /api/projects[/<id>]` | POST needs key | JSON slide collections |
| Socket.IO `verse_triggered` / `set_language` / `clear_overlay` / `show_slide` / `timer_control` | **key** | payload-validated |

### Auth model

One shared secret, `DABARSTREAM_KEY`, compared with `hmac.compare_digest`.
Unset on the server ⇒ auth is **disabled** (dev mode). The VPS sets it in the
systemd unit. The client must send the same value on **every** emit or the
server silently drops it.

---

## 2. Status of each subsystem

| Subsystem | Status | Evidence |
|---|---|---|
| Overlay rendering | **Working** | `?book=John&chapter=3&verse=16&lang=eng` painted; `?hold=1` stayed on screen |
| Broadcast fan-out | **Working** | journal `emitting event "update_overlay" to all [/]` + same packet to 5+ session ids |
| Socket.IO client delivery | **Working** | `/js/socket.io.js` → `200`; sockets upgrade to websocket |
| `/stage` | **Working** | operator confirmed |
| `/control` | **Was dead, now fixed** | `SyntaxError: '' string literal contains an unescaped line break` killed the whole script; `.split('\n')` had become a real newline. Now `String.fromCharCode(10)` |
| Verse resolution (`resolve_book`) | **Working** | `mafumu`→`kings`, `1 mafumu`→`1 kings`, `one mafumu`→`1 kings`, `first yohane`→`1 john` |
| DB ↔ server alias parity | **Working** | audit: every `bem`/`nya` `book_normalized` equals the server's lookup key |
| Voice client | **Was broken, now fixed** | see D1 |
| Chitonga (`ton`) | **Broken data** | UI + hotkey 4 offer it; no rows exist |
| Tests | **Not run this session** | 46 baseline, 55 expected |

---

## 3. Defects found and FIXED in this review

### D1 — CRITICAL: the voice client sent an empty stream key
`client.py` read `STREAM_KEY = os.environ.get("DABARSTREAM_KEY", "")` and
`run_client.ps1` never set that variable. The VPS has the key configured, so
`is_authorized()` returned `False` and **every** `verse_triggered` was dropped
with `[Auth]: Rejected`. The socket connected perfectly, the journal showed
healthy PING/PONG, and the overlay stayed blank — the most misleading possible
failure mode.

**Fix**
* `client.py`: key resolution is now env var → `stream_key.txt` → `""`, via
  `_load_stream_key()`; `main()` prints a loud warning when empty.
* `run_client.ps1`: exports `$env:DABARSTREAM_KEY` from `stream_key.txt`.
* `stream_key.txt` created with the deployment key; added to `.gitignore`.
* Locked by `test_stream_key_prefers_env_then_falls_back_to_the_key_file`,
  `test_stream_key_is_empty_when_nothing_is_configured`.

### D2 — A sentence naming a language never produced its verse
`process_audio()` did `continue` after a language match, so
*"Let us read John 3:16 in English"* switched language and **discarded the
verse**. `LANG_COMMANDS` matches bare words ("english", "bemba"), so this fires
often in ordinary preaching.

**Fix** — the `continue` is gone; the switch is applied and the verse is still
parsed. A pure language command still emits nothing else, because
`parse_verse_reference("show tonga")` is `None`.
* Locked by `test_process_audio_emits_verse_when_the_sentence_also_names_a_language`.

### D3 — Spoken lead-ins were folded into the book name
The digit regex captured everything before the numbers, so
*"Let us read John 3:16"* produced book `"Let us read John"` → `resolve_book`
found no row → **silent blank overlay, no error logged**.

**Fix** — new `strip_lead_in()` removes leading preamble words only
(`let us`, `read`, `turn to`, `go to`, `open`, `now`, `and`, `the`, `book of`, …)
iteratively, and never strips a name to nothing. Numbered books (`1 John`),
ordinals (`First John`) and multi-word names (`Song of Solomon`) are untouched.
Applied on both the digit and the spoken-word paths.
* Locked by `test_strip_lead_in_removes_spoken_preamble_only`,
  `test_parse_verse_reference_ignores_spoken_lead_in`.

### D4 — English text labelled as a Zambian translation
The overlay printed the local-language label even when the resolver had fallen
back to English, because `ton` (Chitonga) has **no rows**. On air that looks
like a broken translation rather than a correct fallback.

**Fix** — `translation_has_data()` (a cached, single, indexed existence probe
per db+code) and `lang_label_for()`; all three payload builders now use it, so a
missing translation renders as `Chitonga (English shown)`.
* Locked by `test_lang_label_admits_when_english_is_shown_instead`.

### D5 — `/control` had no slide or timer controls
The panel's JavaScript drove `#slide-btn`, `#slide-title`, `#slide-lines`,
`#timer-minutes`, `#timer-label`, `#timer-start`, `#timer-stop`, `#timer-clear`
— **none of which existed in the HTML**. The handlers were permanently dead
(null-guarded code that could never run), so the Phase 1 slide/timer feature was
unreachable.

**Fix** — the inputs and buttons were added, `textarea` was added to the shared
CSS rule, and the verse form now sends `lang: currentLang` so a manually typed
verse is tagged with the translation the operator selected.
* Locked by `test_control_panel_exposes_slide_and_timer_controls`,
  `test_control_panel_verse_emit_carries_the_active_language`.

### D6 — Project URLs were not URL-encoded
`list_projects()` built `"/api/projects/" + base`, where `base` can contain
spaces (`"Sunday Set"`), producing a self-referencing URL the client cannot
fetch. Now uses `urllib.parse.quote`.
* Locked by `test_project_list_urls_are_url_encoded`.

### Earlier fixes still in force (regression-locked)

| Defect | Root cause | Guard |
|---|---|---|
| Blank OBS overlay | python-engineio 4.x no longer serves `/socket.io/socket.io.js` → HTTP **400** → `io` undefined → script threw | `/js/socket.io.js` route + vendored client |
| `/test` → 500 | `SocketIO.emit()` rejects `broadcast=True` (only the handler-level `emit()` accepts it) | `test_test_broadcast_route_broadcasts_without_error` |
| Verse wiped by language banner | `set_language` re-broadcast even when unchanged | same-language requests are a logged no-op |
| Numbered books lost | book regex excluded digits, so `1 Timothy 3:16` matched at `Timothy` | `test_parse_verse_reference_digit_form_keeps_numbered_books` |
| `kings = mafumu` unreachable | importer's Bemba map had 3 entries vs the server's canonical keys | `test_importer_aliases_resolve_identically_on_the_server` |
| `/control` dead panel | mangled `\n` inside a JS string literal | `test_html_templates_have_no_broken_string_literals` |
| `pyaudio` uninstallable on 3.14 | no cp314 wheel; source build needs MSVC | client prefers `sounddevice` |

---

## 4. Defects and risks that are STILL OPEN

Ordered by how much they hurt a live service.

| # | Sev | Issue | Detail | Suggested fix |
|---|---|---|---|---|
| O1 | **High** | Chitonga has no data | `ton` is offered by the UI, hotkey 4 and `LANG_COMMANDS`, but `bible.db` has no `ton` rows. Now *honestly* labelled, but still not Chitonga. | Import a Tonga module (`reimport.py ton` + `tonga_baibele.xml`), **or** remove `ton` from `TRANSLATION_LABELS`/`HOTKEY_LANGS`/`LANG_COMMANDS` |
| O2 | **High** | Plain HTTP on port 5000 | The stream key travels in cleartext and sits in the URL of `/control?key=…` (browser history + VPS access log). Internet scanners already hit the box (TLS handshakes logged from `165.58.129.200`). | nginx + Let's Encrypt TLS on 443 → proxy to 127.0.0.1:5000; close 5000 in the VCN security list; stop using `?key=` and type it once into `localStorage` |
| O3 | **Med** | `cors_allowed_origins="*"` | Any web page can open a Socket.IO connection. Read-only in practice, but it invites noise and abuse. | Restrict to the deployment origin(s), or set from an env var |
| O4 | **Med** | Loose language matching | `LANG_COMMANDS` matches bare "english", "bemba", "chewa", "baibele" **anywhere** in a sentence. The preacher saying *"the English Bible says…"* switches translation mid-service. | Require an explicit cue ("show X", "switch to X", "in X") for the ambiguous English names; keep the distinctive words (`icibemba`, `chinyanja`, `chitonga`) as bare matches. **Existing tests lock the bare-word forms** — change tests with the behaviour |
| O5 | **Med** | `CURRENT_LANG` is process-global, non-persistent | Restarting the service resets the active translation to `eng`. With >1 worker it would diverge per worker. Single eventlet worker today, so latent. | Persist to a small file/DB row, and/or document "run exactly one worker" |
| O6 | **Med** | 15-second auto-hide | A live verse fades after 15 s unless the URL has `?hold=1`. Operators have been surprised by the "flashing" overlay. | Make `hold` the default for `/overlay`, or add a duration control on `/control` |
| O7 | **Low** | eventlet is deprecated | journal carries `EventletDeprecationWarning`; upstream recommends migrating. | Migrate to `gevent`, or drop to `threading` + `simple-websocket`. No application code change required |
| O8 | **Low** | `/overlay?book=…` deep-link is unauthenticated | Read-only, but it lets anyone render any verse in the overlay if the URL is known. | Gate behind a query token, or accept as-is (low impact for a church stream) |
| O9 | **Low** | No favicon / 404 noise | Minor log noise. | Add a tiny `favicon.ico` route |
| O10 | **Low** | Repo clutter + no CI | `server_new.py`, `server_copy.py`, `debug_read.py`, `debug_out.txt`, `syntax_check.txt`, `pytest_result.txt`, `check_vps.sh`, `check_from_windows.ps1` | Delete the dead ones; add a GitHub Actions job running `pytest` |
| O11 | **Low** | `bible.db` is not in git (correct) but has no versioning | No reproducible way to rebuild the DB from a clean clone without the source XMLs (which are also git-ignored for licensing). | Document the exact import commands + expected row counts in the README |

---

## 5. Production-readiness scorecard

Scores are 0–10, judged for a **single-church, single-operator live stream**.

| Dimension | Score | Rationale |
|---|---|---|
| Core function (voice → overlay) | **6/10** | Architecture is sound and the pipeline is proven end-to-end. D1 meant the primary path was **silently dead**; now fixed but unverified by a test run. |
| Reliability / failure visibility | **5/10** | Failures are quiet: an empty key, a missed alias, and lead-in contamination all produced a *blank overlay with no error*. `?debug=1`, `/health`, and the `/test` route help; there is no alerting and no "last verse" state. |
| Input robustness | **7/10** | Alias tables are large (178 server aliases), digit + spoken-number paths exist, ordinals/cardinals handled, lead-ins now stripped. Only the **trainable** ASR vocabulary is absent. |
| Security | **3/10** | Plain HTTP, secret in URLs, `cors=*`, no rate limiting, `/health` discloses DB internals. Auth itself is correct (`hmac.compare_digest`, fails closed when configured). |
| Observability | **4/10** | Structured-ish print logging into the journal; `/health` exists. No metrics, no log levels, no correlation of a trigger to a broadcast. |
| Test coverage | **7/10** | 46 tests before, 55 after — unusually good for a project of this size, and they encode real incident history. Missing: an end-to-end Socket.IO test client, and any test that runs the real server process. |
| Operability / deploy | **6/10** | `scp` + `systemctl restart` is simple and works. No migrations, no staging, no rollback beyond `git`. `bible.db` has a `.bak`. |
| Code quality / structure | **7/10** | Clear names, strong docstrings, honest comments that explain *why*. A 1,100-line single module and 3 inline HTML templates are the main ceiling. |
| Documentation | **6/10** | Excellent inline rationale; no README-level runbook until this document. |

**Overall: 5.7 / 10 — "works in a controlled service, not yet hardened."**
It is good enough to run this Sunday **if** the operator watches `/stage` and
the journal. It is not ready to be exposed, unsupervised, to the public internet.

---

## 6. Suggested improvements, in priority order

### Phase A — before the next live service (minutes of work)
1. **Run the test suite** and get `55 passed` (§8.1). This validates every fix in §3.
2. **Deploy the updated `server.py` + `client.py` + `run_client.ps1`** and restart the unit.
3. **Confirm the voice path end-to-end**: speak *"John chapter three verse sixteen"*
   and watch for `[Cloud Event Received]` **and** `[Broadcasting to OBS Web client]`
   in the journal, then see it on `/overlay`. This is the most important check,
   because D1/D2/D3 were all *silent* failures.
4. **Decide Chitonga (O1)**: import it, or remove it from the UI, buttons and hotkeys.
5. **Bookmark `/overlay?hold=1`** for OBS so verses do not fade (O6).

### Phase B — hardening (about a day)
6. **TLS + reverse proxy** (O2) and close port 5000 to the world.
7. **Restrict CORS** to the site origin (O3).
8. **Tighten language matching** (O4) and update the test that locks the bare form.
9. **`README.md` runbook**: deploy, import, verify, rollback, key rotation (O11).
10. **Delete dead files** and add a small CI workflow running `pytest` (O10).

### Phase C — quality of life (about a week)
11. **Custom Whisper vocabulary** — seed `initial_prompt` with the local book
    names (`Yohane`, `Mafumu`, `Salimo`, `Chibandakazi`, …) and the phrase
    "chapter … verse …". Biggest available accuracy win.
12. **A "last verse" panel state** so `/stage` and `/control` always show what is
    on air, and a reconnecting overlay repaints instead of going blank.
13. **Persist `CURRENT_LANG`** across restarts (O5).
14. **Split `server.py`**: `app.py`, `resolver.py`, `templates/`, `static/`, with
    the HTML in real Jinja templates — this removes the escape-mangling hazard
    that caused the `/control` `SyntaxError`.
15. **Rate-limit Socket.IO emits** per session (cheap DoS protection).
16. **Structured logging** (`logging` module, JSON) so the journal can be filtered
    by level and event.

---

## 7. Files changed in this review

| File | Change |
|---|---|
| `client.py` | `_load_stream_key()` (env → `stream_key.txt` → `""`); `main()` warns on an empty key; removed the `continue` that discarded verses; new `_LEAD_IN_RE` + `strip_lead_in()`, applied to both parse paths |
| `server.py` | new `translation_has_data()` + `lang_label_for()`; all 3 payload builders use the honest label; `quote()` for project URLs; `/control` gained slide + timer controls, `textarea` CSS, and `lang: currentLang` on the verse emit |
| `run_client.ps1` | loads `DABARSTREAM_KEY` from the environment or `stream_key.txt`, exports it, warns when absent |
| `test_language.py` | **+9 tests** (55 total expected) covering D1–D6 |
| `.gitignore` | ignores `stream_key.txt` |
| `stream_key.txt` | **NEW** — one-line shared secret (git-ignored) |
| `ASSESSMENT.md` | **NEW** — this document |

---

## 8. Verification commands

### 8.1 Run the tests (expect `55 passed`) **[UNVERIFIED — must be run]**
```powershell
cd C:\Users\Jabs\Documents\GitHub\DabarStream
.\.venv\Scripts\python.exe -m pytest test_language.py -q
```

### 8.2 Deploy the fixes
```powershell
$K = "C:\Users\Jabs\Downloads\ssh-key-2026-08-19.key"
scp -i $K server.py opc@193.123.179.93:~/
ssh -i $K opc@193.123.179.93 "sudo cp ~/server.py /opt/dabarstream/ && sudo chown dabarstream:dabarstream /opt/dabarstream/server.py && sudo systemctl restart dabarstream && sleep 3 && systemctl is-active dabarstream"
```

### 8.3 Prove the control panel is alive (no `SyntaxError`)
```powershell
ssh -i $K opc@193.123.179.93 "curl -s http://127.0.0.1:5000/control | grep -c 'fromCharCode(10)'; curl -s http://127.0.0.1:5000/control | grep -c 'slide-btn'"
```
Then in the browser: **Ctrl+F5** on `/control`, open **F12 → Console**, confirm
no `SyntaxError`. Click a language button; the status line must change.

### 8.4 Prove the voice path (the fix for D1/D2/D3)
```powershell
# On the Windows PC, in the project folder, with stream_key.txt present:
.\run_client.ps1
```
Say *"Let us read John chapter three verse sixteen"*, then:
```powershell
ssh -i $K opc@193.123.179.93 "sudo journalctl -u dabarstream --since '3 min ago' --no-pager | grep -E 'Cloud Event|Router|Broadcasting|Auth|Language Switch'"
```
* `[Cloud Event Received]` ⇒ the key now works (D1 fixed).
* `[Broadcasting to OBS Web client]` ⇒ the verse resolved (D2/D3 fixed).
* `[Auth]: Rejected` ⇒ the client key still does not match the VPS key.

### 8.5 Sanity-check the resolver and DB
```powershell
ssh -i $K opc@193.123.179.93 "curl -s 'http://127.0.0.1:5000/api/verse?book=1%20mafumu&chapter=5&verse=14&lang=bem'"
ssh -i $K opc@193.123.179.93 "curl -s 'http://127.0.0.1:5000/health'"
```
Expected: Bemba text + English subtitle; `{"status":"ok", ..., "translations":33}`.

---

## 9. Open questions for the external AI reviewers

1. **O4 trade-off**: is restricting `LANG_COMMANDS` to explicit cues right, or is
   it better to *"switch only if no verse reference is found in the same
   segment"*? The latter preserves bare-word switching but loses the
   "switch and show" combination in one sentence (which D2 just enabled).
2. **`strip_lead_in` stop-list**: is a heuristic word list the right approach, or
   should book detection be inverted — match the **longest known alias substring
   anywhere** in the utterance rather than capturing a leading word run? The
   inverted approach would eliminate the entire class of D3 bugs.
3. **`translation_has_data`**: is a per-code DB probe acceptable, or should the
   available-translation set be loaded once at startup into a module constant?
4. **Single module vs package split**: worth the churn now, or after TLS?
5. **eventlet → gevent**: any behavioural difference to expect with
   Flask-SocketIO 5.6.1 on Python 3.12?
6. **Chitonga decision**: import, or remove from the UI? The church's actual
   desire determines which.
7. **Auto-hide default (O6)**: should `/overlay` hold by default, with an opt-out
   `?fade=1`, rather than the current opt-in `?hold=1`?

---

## 10. Housekeeping / environment notes

* The reviewing agent's shell integration was broken for the whole session: every
  command returned a stale buffer and wrote no output file. **All §8 commands are
  for a human to run.**
* The editor tool has, in this project's history, **mangled `\n` escapes inside
  the inline HTML templates**, which is exactly what produced the `/control`
  `SyntaxError`. Until the templates move to real files, prefer escape-free
  constructs (`String.fromCharCode(10)`) and rely on
  `test_html_templates_have_no_broken_string_literals`.
* `server_new.py` is a **stale earlier rewrite** — do not copy it back over
  `server.py`.
* Back up the database before any re-import:
  `sudo cp /opt/dabarstream/bible.db /opt/dabarstream/bible.db.bak`
* Harmless browser-console noise, do **not** chase: `Source map error …
  socket.io.min.js.map` (404, dev-tools only), `Password fields present on an
  insecure (http://) page` (true — see O2), `Ignoring unsupported entryTypes:
  longtask`, `Promised response from onMessage listener went out of scope`,
  `Security Error: … file:///` (all extension/browser noise).

---

## 11. One-paragraph summary for an AI reviewer

DabarStream is a Flask/Flask-SocketIO + SQLite scripture-overlay system with a
local Whisper voice client. The core pipeline is proven. This review found and
fixed six defects, three of them critical and all three *silent*: the voice
client sent an empty stream key so the VPS rejected every emit; a language
mention in a sentence discarded the verse in that same sentence; and spoken
lead-ins ("let us read …") were folded into the book name so no row matched.
It also made missing-translation labelling honest, added the slide/timer controls
the control panel's JavaScript already expected, and URL-encoded project links.
Nine regression tests were added (55 expected). The principal remaining risks are
operational rather than functional: no TLS, the stream key in URLs, wildcard
CORS, loose speech-triggered language switching, and Chitonga being offered with
no data behind it.

**Highest-value next actions:** run the suite (§8.1), deploy (§8.2), and prove the
voice path end-to-end (§8.4).
