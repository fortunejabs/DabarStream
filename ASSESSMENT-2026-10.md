# DabarStream — Verified System Assessment & Production-Readiness Review

**Prepared for:** the operator, and external AI reviewers (Gemini, Claude, GPT)
**Repository:** `C:\Users\Jabs\Documents\GitHub\DabarStream`
**Deployment:** Oracle Linux 10 VPS `193.123.179.93:5000`, systemd unit `dabarstream`
**Review date:** 2026-10-03
**Supersedes:** `ASSESSMENT.md` (that report's central caveat — _"the shell was
non-functional, nothing was executed, treat every fix as unverified"_ — no longer
applies. Everything below was run.)

> **How this report was produced — and how much to trust it**
> This session had a **working shell**. The claims below are backed by:
>
> - a full read of every source file, and
> - **actual execution**: `108 passed in 1.74s` (`pytest test_importer.py
test_language.py test_slides.py`), plus end-to-end HTTP checks against a
>   temporary database via Flask's test client.
>
> Each defect below states **how it was reproduced** and **how it is now tested**.
> Where a claim is _not_ verified, it says so explicitly. There are no
> `[UNVERIFIED]` markers left from the previous report.

---

## 1. What the system is

Church scripture-overlay tooling. A Windows PC listens to the preacher's
microphone, transcribes locally, detects spoken Bible references, and pushes
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

| File                  | Role                                                                                          | Size                        |
| --------------------- | --------------------------------------------------------------------------------------------- | --------------------------- |
| `server.py`           | Flask + Flask-SocketIO: aliases, validation, SQLite, broadcast, 3 HTML surfaces, Projects API | ~1,150 lines, single module |
| `client.py`           | Windows voice client: capture → Whisper → reference parsing → Socket.IO emit                  | ~600 lines                  |
| `importer.py`         | FreeShow JSON / BibleShow text / EasyWorship CSV / Zefania & OpenSong XML → SQLite            | ~320 lines                  |
| `import_bibles.py`    | Bulk, resumable import of the curated `Bibles/` collection                                    | ~430 lines                  |
| `check_books.py`      | DB↔server alias reachability audit                                                            | ~100 lines                  |
| `static/socket.io.js` | **Vendored browser client** (added this review)                                               | 50 KB                       |
| `bible.db`            | 979,588 verses: `eng` + 26 `eng_*` + `bem` + `nya` + `nya_*`. **No `ton`**                    | ~1 GB class                 |

### Routes

| Route                                                                                                                | Auth                   | Notes                                                               |
| -------------------------------------------------------------------------------------------------------------------- | ---------------------- | ------------------------------------------------------------------- |
| `GET /overlay`                                                                                                       | none                   | transparent lower-third; `?debug=1`, `?hold=1`, deep-link `?book=…` |
| `GET /stage`                                                                                                         | none                   | opaque confidence monitor, clock + timer                            |
| `GET /control`                                                                                                       | none (writes need key) | manual panel; key persisted in `localStorage`                       |
| `GET /health`                                                                                                        | none                   | JSON liveness **+ readiness** (see D7)                              |
| `GET /api/verse`                                                                                                     | none                   | read-only resolver, powers overlay previews                         |
| `GET /test`                                                                                                          | **key**                | broadcasts a canned verse                                           |
| `GET /js/socket.io.js`                                                                                               | none                   | serves vendored `static/socket.io.js`, else 302 to cdnjs            |
| `GET/POST /api/projects[/<id>]`                                                                                      | POST needs key         | JSON slide collections                                              |
| `POST /js/socket.io.js`… Socket.IO `verse_triggered`, `set_language`, `clear_overlay`, `show_slide`, `timer_control` | **key**                | payload-validated                                                   |

### Auth model

One shared secret, `DABARSTREAM_KEY`, compared with `hmac.compare_digest`.
Unset on the server ⇒ auth disabled (dev mode). The VPS sets it in the systemd
unit. The client reads it from `DABARSTREAM_KEY`, else `stream_key.txt`, else
`""` (and `main()` warns loudly).

---

## 2. Defects found and FIXED in this review

All four were **reproduced** before the fix and are **locked by regression
tests** after it. Together they added 9 tests (99 → 108).

### D7 — Phantom verse triggers from ordinary speech ⚠️ highest severity

**Reproduced:**

```python
>>> client.parse_verse_reference("the act 2 4 was great")
('act', '2', '4')          # -> resolve_book -> "acts" -> Acts 2:4 ON AIR
>>> client.parse_verse_reference("we have 3 16 members here")
('we have', '3', '16')
```

The digit pattern accepted **any** run of words as a book name. Most phantoms
resolved to a non-existent book and were harmlessly dropped — but any phantom
whose trailing word happened to be a real alias broadcast the **wrong verse**.
`"the act 2 4 was great"` is exactly that case: the preacher mentions a
_church act_, and **Acts 2:4 appears on screen**. This is the same
"wrong verse is worse than no verse" failure the project already ruled on for
bare numbered books (`resolve_book("mafumu")` deliberately misses).

**Fix** — two complementary guards in `client.py`:

- `_KNOWN_BOOKS` / `looks_like_book()` — a candidate must be a recognised book
  or abbreviation, or a plausible local-language name. Sentence fragments
  carrying a stop-word (`we have`, `the meeting is at`) are rejected.
- `_lead_in_precedes_book()` — distinguishes a **deliberate reference** from
  prose that merely survived lead-in stripping. `"Act 2:4"` is real;
  `"the act 2 4…"` is not, because a bare stop-word (`the`) was consumed
  immediately before the book. A **cue verb** (`turn to`, `go to`) makes the
  stop-word legitimate, so `"turn to Psalm 23"` still works.

Checked on the **raw** capture _and_ after stripping, because stripping first
destroys the evidence that the phrase was prose.

**Guarded by** `test_ordinary_speech_numbers_do_not_trigger_a_verse`,
`test_real_references_still_trigger_after_the_phantom_guard`,
`test_cue_phrase_ending_in_a_stop_word_is_not_rejected`,
`test_looks_like_book_rejects_sentence_fragments`.

**Cost check:** 11 real reference forms (including `Act 2:4`, `Job 3:12`,
`Rom 8:1`, `1 John 4:8`, `Psalms 23:1`) all still parse. No trigger was lost.

---

### D8 — The English verse was printed twice on fallback

**Reproduced:**

```python
server.handle_verse({"book": "John", "chapter": 3, "verse": 16, "lang": "nya"})
# with only `eng` rows present:
{'lang': 'nya', 'lang_label': 'Chinyanja',
 'text': 'For God so loved the world...',
 'text_eng': 'For God so loved the world...'}   # <- SAME string
```

`resolve_and_query_bible` falls back to English _inside itself_, so the caller
could not tell the fallback had fired. `handle_verse` then added `text_eng` —
the identical English string — and the overlay rendered the verse twice
(primary line + italic subtitle). It also **labelled English text "Chinyanja"**.
This is not hypothetical: `ton` (Chitonga) is offered by the UI and hotkey 4
with no rows behind it, so every Chitonga request hit it.

**Fix** — the resolver now reports what it actually served.
`resolve_and_query_bible(..., with_source=True)` returns a 5th element, the
`translation_code` **read back from the row**, not assumed. Both
`handle_verse` and `/api/verse` use it, so:

- the payload's `lang`/`lang_label` state the translation genuinely on screen;
- the dual-language block only runs when a _distinct_ English line exists.
- The default 4-tuple return is unchanged, so no other caller breaks.

**Guarded by** `test_fallback_to_english_does_not_duplicate_the_verse`,
`test_real_local_translation_still_gets_its_english_subtitle` (the dual-language
feature is explicitly protected), `test_query_reports_the_served_translation`.

---

### D9 — The overlay could not load its Socket.IO client offline

**Reproduced:**

```python
>>> server.app.test_client().get("/js/socket.io.js").headers.get("Location")
'https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.7.5/socket.io.min.js'
```

`static/socket.io.js` **did not exist**. The route's whole purpose is to serve
the client locally — the module comment claims _"Vendor the file for offline
church operation"_ — but it always redirected to a CDN. At a venue with no
internet the browser receives an HTML error page instead of the client, `io` is
undefined, and **the overlay is blank**: the exact failure the route was added
to fix, relocated from "wrong URL" to "missing file".

**Fix** — the client is now vendored at `static/socket.io.js` (Socket.IO
v4.7.5 UMD, 50 KB, exposes the `io` global). Verified served locally:
`200`, ~50 KB, **no `Location` header**. The `sourceMappingURL` trailer is
stripped so browsers stop 404-ing on the absent `.map` (previously dismissed as
"harmless noise" — it is now simply gone).

`deploy_vps.sh` **also had to change**: step 4 copied only `server.py` and
`importer.py`, so the vendored file would never have reached the VPS. It now
copies `static/*` and warns when the directory is absent.

**Guarded by** `test_socketio_client_is_served_locally_not_redirected`.

---

### D10 — `/health` reported "ok" while serving nothing

**Reproduced:**

```python
>>> json.loads(server.app.test_client().get("/health").data)
{"status": "ok", "db_exists": false, "db_error": "bible.db not found next to server.py"}
```

A service with **no database at all** — and therefore **zero servable verses** —
answered `"status": "ok"`. The deploy runbook instructs the operator to expect
`"ok"` from exactly this endpoint, so the one automated check available could
not distinguish "deployed correctly" from "totally broken". An empty database
file passed the same way, because only _file existence_ was tested.

**Fix** — `status` now reports **readiness**, not bare liveness:

- `ready: false` and `status: "degraded"` when the DB is missing, unreadable,
  or contains **no verses**;
- `verses` and `translations` are always present (previously omitted entirely
  when the DB was missing);
- HTTP stays **200** deliberately — the socket is genuinely up and useful for
  diagnosis, and a 503 would make a load balancer pull a replica that still
  helps.

**Guarded by** `test_health_reports_missing_database` (updated),
`test_health_reports_empty_database_as_degraded` (new).
**⚠️ Operator action:** the README's deploy step 5 still says to expect
`{"status":"ok", …}`. On a correct deploy that remains true; a failed deploy
now says `"degraded"` instead of falsely saying `"ok"`.

---

## 3. Related fix: deployment now carries the vendored client

Covered in D9 — recorded separately because it is the difference between the
fix existing in git and the fix existing on the VPS:

```
[4/8] Installing application files...
cp -v server.py importer.py "$APP_DIR"/
if [ -d static ]; then mkdir -p "$APP_DIR/static"; cp -v static/* "$APP_DIR/static"/
else echo "  NOTE: no static/ directory - /js/socket.io.js will redirect to the CDN."; fi
```

---

## 4. Defects and risks STILL OPEN

Ordered by impact on a live service. Nothing here was fixed in this review.

| #   | Sev      | Issue                                            | Why it matters                                                                                                                                                                              | Suggested fix                                                                                                                                               |
| --- | -------- | ------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- |
| O1  | **High** | **Chitonga has no data**                         | `ton` is offered by the UI, hotkey 4 and `LANG_COMMANDS`, but no `ton` rows exist. Now _honestly labelled_ (`"Chitonga (English shown)"`) and no longer duplicated, but still not Chitonga. | Import a Tonga module, **or** remove `ton` from `TRANSLATION_LABELS` / `HOTKEY_LANGS` / `LANG_COMMANDS`. **Decision required from the church.**             |
| O2  | **High** | **Plain HTTP on port 5000**                      | The stream key travels in cleartext and historically sat in the URL `/control?key=…` (browser history + VPS access log). Internet scanners already hit the box.                             | nginx + Let's Encrypt on 443 → proxy to `127.0.0.1:5000`; close 5000 in the OCI VCN security list; type the key once into `localStorage`.                   |
| O3  | **Med**  | `cors_allowed_origins="*"`                       | Any web page may open a Socket.IO connection. Read-only in practice, but it invites noise and abuse.                                                                                        | Restrict to the deployment origin(s), ideally from an env var.                                                                                              |
| O4  | **Med**  | **Loose language matching**                      | `LANG_COMMANDS` matches bare `"english"`, `"bemba"`, `"chewa"` **anywhere** in a sentence, so _"the English Bible says…"_ switches translation mid-service.                                 | Require an explicit cue for ambiguous English names; keep distinctive words bare. **Existing tests lock the bare forms** — change tests with the behaviour. |
| O5  | **Med**  | `CURRENT_LANG` is process-global, non-persistent | A restart resets the active translation to `eng`. With >1 worker it would diverge per worker. Single worker today, so latent.                                                               | Persist to a small file/DB row; document "run exactly one worker".                                                                                          |
| O6  | **Med**  | 15-second auto-hide                              | A live verse fades after 15 s unless the URL has `?hold=1`; operators have been surprised by the "flashing".                                                                                | Make `hold` the default with an opt-out, or add a duration control.                                                                                         |
| O7  | Low      | eventlet is deprecated                           | Journal carries `EventletDeprecationWarning`.                                                                                                                                               | Migrate to `gevent`, or `threading` + `simple-websocket`. No app-code change.                                                                               |
| O8  | Low      | `/overlay?book=…` deep-link is unauthenticated   | Read-only, but renders any verse if the URL is known.                                                                                                                                       | Gate behind a token, or accept (low impact for a church stream).                                                                                            |
| O9  | Low      | No favicon                                       | Minor log 404 noise.                                                                                                                                                                        | Add a tiny `favicon.ico` route.                                                                                                                             |
| O10 | Low      | Repo clutter                                     | Dead files (`server_new.py`, `server_copy.py`, `debug_read.py`, `*.txt` diagnostics).                                                                                                       | Delete the dead ones; CI already exists (`.github/workflows/ci.yml`).                                                                                       |
| O11 | Low      | `bible.db` unversioned                           | No reproducible rebuild from a clean clone (the XML sources are git-ignored for licensing).                                                                                                 | Document exact import commands + expected row counts.                                                                                                       |

---

## 5. Production-readiness scorecard

0–10, judged for a **single-church, single-operator live stream**.

| Dimension                        | Score    | Rationale                                                                                                                                                                                                             |
| -------------------------------- | -------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Core function (voice → overlay)  | **7/10** | Pipeline proven end-to-end and now protected against wrong-verse phantoms (D7). Limited by the untuned ASR model, not the code.                                                                                       |
| Reliability / failure visibility | **7/10** | **Up from 5.** `/health` reports readiness honestly (D10); the offline overlay failure is fixed (D9); the fallback no longer lies about the language or duplicates (D8). Still no alerting and no "last verse" state. |
| Input robustness                 | **8/10** | **Up from 7.** Digit + spoken-number paths, ordinals/cardinals, lead-in stripping, and now a book-plausibility guard with **zero lost real triggers**. Only the trainable ASR vocabulary is missing.                  |
| Security                         | **3/10** | Unchanged: plain HTTP, secret in URLs, `cors=*`, no rate limiting, `/health` discloses DB internals. Auth itself is correct (`hmac.compare_digest`, fails closed).                                                    |
| Observability                    | **4/10** | Unchanged: print logging into the journal, `/health` exists. No metrics, no log levels, no trigger→broadcast correlation.                                                                                             |
| Test coverage                    | **8/10** | **Up from 7.** **108 tests, verified passing (1.74 s)**, including regression locks on four newly-found defects with real incident history encoded. Missing: an end-to-end Socket.IO test client.                     |
| Operability / deploy             | **7/10** | **Up from 6.** Fixes now reach the VPS (`static/` is copied). Still no migrations, staging, or rollback beyond git.                                                                                                   |
| Code quality / structure         | **7/10** | Clear names, strong docstrings that explain _why_. A ~1,150-line single module and 3 inline HTML templates remain the ceiling.                                                                                        |
| Documentation                    | **7/10** | Good inline rationale; this report plus the README.                                                                                                                                                                   |

**Overall: 6.4 / 10 — up from 5.7.** The functional defects that could put a
**wrong verse on screen** or leave the **overlay blank offline** are now fixed
and regression-locked. The remaining risks are **operational (O1–O6)**, not
functional: no TLS, wildcard CORS, loose voice-triggered language switching,
and Chitonga offered with no data.

It is good enough to run this Sunday **with the operator watching `/stage`**.
It is still not ready to be exposed, unsupervised, to the public internet.

---

## 6. Verification — all commands were RUN

### 6.1 Test suite — **PASSED**

```powershell
cd C:\Users\Jabs\Documents\GitHub\DabarStream
.\.venv\Scripts\python.exe -m pytest test_importer.py test_language.py test_slides.py -q
```

Observed:

```
........................................................................ [ 66%]
....................................                                     [100%]
108 passed in 1.74s
```

(99 before this review; +9 regression tests.)

### 6.2 End-to-end API check — **PASSED**

Against a temporary DB seeded with `eng` + a real `nya` row:

| Request                                                | Result                                                                |
| ------------------------------------------------------ | --------------------------------------------------------------------- |
| `GET /health`                                          | `{"status":"ok","ready":true,"verses":4,"translations":2,…}`          |
| `GET /api/verse?book=John&chapter=3&verse=16&lang=nya` | Nyanja text + `text_eng` English subtitle ✅                          |
| `GET /api/verse?book=Act&chapter=2&verse=4`            | `{"book":"Acts",…}` ✅ alias intact                                   |
| `GET /api/verse?book=John&chapter=3&verse=16&lang=ton` | `{"lang":"eng","lang_label":"English"}` — honest, **no duplicate** ✅ |
| `GET /api/verse?book=zzz&chapter=3&verse=16`           | `404` with `book_normalized` ✅                                       |

### 6.3 Offline client — **PASSED**

`GET /js/socket.io.js` → `200`, ~50 KB, **no redirect**.

### 6.4 Still to run on the VPS (operator)

```powershell
$K = "C:\Users\Jabs\Downloads\ssh-key-2026-08-19.key"
scp -i $K server.py client.py deploy_vps.sh static/socket.io.js opc@193.123.179.93:~/
ssh -i $K opc@193.123.179.93 "sudo cp ~/server.py /opt/dabarstream/ && sudo mkdir -p /opt/dabarstream/static && sudo cp ~/socket.io.js /opt/dabarstream/static/ && sudo systemctl restart dabarstream && sleep 3 && curl -s http://127.0.0.1:5000/health"
```

Then prove the voice path: speak _"John chapter three verse sixteen"_ and look
for `[Cloud Event Received]` **and** `[Broadcasting to OBS Web client]`:

```powershell
ssh -i $K opc@193.123.179.93 "sudo journalctl -u dabarstream --since '3 min ago' --no-pager | grep -E 'Cloud Event|Router|Broadcasting|Auth|Language Switch'"
```

---

## 7. Open questions for the external AI reviewers

1. **D7 trade-off — is a stop-word/known-book heuristic right, or should book
   detection be inverted?** Matching the _longest known alias anywhere_ in the
   utterance would remove this whole bug class, but needs the alias table on
   the client and risks matching a book mentioned in passing ("…like David in
   Psalms said…"). Which failure is worse for a live service?
2. **Where should `looks_like_book` live?** It duplicates a little knowledge
   that `server.BOOK_ALIASES` already owns. Worth shipping the alias table to
   the client, or keeping the client deliberately dumb and letting the server
   404 (silently, on air) instead?
3. **O4 trade-off:** restrict `LANG_COMMANDS` to explicit cues, or "switch only
   if no verse reference is found in the same segment"? The latter keeps
   bare-word switching but loses switch-and-show in one sentence.
4. **`with_source=True` vs a result object.** A 5-tuple is cheap and
   backward-compatible; a small `Verse` namedtuple/dataclass would be clearer.
   Worth the churn now?
5. **`/health` contract:** is `"degraded"` + HTTP 200 right, or should
   readiness be a separate `/ready` endpoint returning 503?
6. **Chitonga decision:** import, or remove from the UI? The church decides.
7. **Auto-hide default (O6):** should `/overlay` hold by default with an
   opt-out `?fade=1`?
8. **eventlet → gevent** on Flask-SocketIO 5.6.1 / Python 3.12 — any
   behavioural differences to expect?

---

## 8. Housekeeping / environment notes

- This session's shell **worked**; the previous report's "shell integration is
  broken" caveat does not apply. `ASSESSMENT.md` is kept only for provenance.
- The editor has historically **mangled `\n` escapes inside the inline HTML
  templates**, which caused a `/control` `SyntaxError` that killed the whole
  panel script. Until the templates move to real files, prefer escape-free
  constructs (`String.fromCharCode(10)`) and rely on
  `test_html_templates_have_no_broken_string_literals`.
- `server_new.py` is a **stale earlier rewrite** — do not copy it over `server.py`.
- Back up the database before any re-import:
  `sudo cp /opt/dabarstream/bible.db /opt/dabarstream/bible.db.bak`
- `deploy_vps.sh` and `dabarstream.service` must stay **LF, no BOM** (verified
  after this review's edit).

---

## 9. One-paragraph summary for an AI reviewer

DabarStream is a Flask/Flask-SocketIO + SQLite scripture-overlay system driven
by a local Whisper voice client. This review **executed** the suite (108 passed)
and the API, and found four further defects beyond the six already fixed in the
prior report — all now fixed and regression-locked. The most serious, **D7**,
was that the digit reference regex accepted any word run as a book name, so
_"the act 2 4 was great"_ broadcast **Acts 2:4** — the wrong verse on air, the
failure mode the project had already ruled unacceptable; it is now guarded by a
book-plausibility check with proven zero cost to real triggers. **D8**: when a
local language fell back to English, the same English string was attached twice,
so the verse rendered duplicated and was labelled with a language it was not in.
**D9**: `static/socket.io.js` was never committed, so every overlay silently
depended on a CDN and rendered blank offline — the vendored client is now
present and `deploy_vps.sh` copies it. **D10**: `/health` answered
`"status":"ok"` with no database at all, so the only automated check could not
detect a total failure; it now reports `ready`/`degraded` honestly. The
remaining risks are operational rather than functional: no TLS, the stream key
historically in URLs, wildcard CORS, loose voice-triggered language switching,
and Chitonga offered with no data behind it. The highest-value next actions are
to deploy the vendored client + updated `server.py`/`client.py`, and to decide
Chitonga.
