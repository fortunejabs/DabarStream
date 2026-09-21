# DabarStream Project Assessment & Remediation Plan

Comprehensive assessment of the DabarStream project based on static analysis, code audit, git state review, database verification, and test execution.

---

## 1. Project Health & Assessment Summary

| Component | Status | Key Observations |
| :--- | :--- | :--- |
| **Test Suite** | 🟢 90/90 passing | Importer, language resolver, and slides tests pass cleanly on Python 3.12. |
| **Database (`bible.db`)** | 🟡 33 translations | 31,104 verses for Bemba (`bem`) & Chewa (`nya`), 31,102 for English (`eng`). **Chitonga (`ton`) has 0 rows** (no XML file in repo; falls back to English). |
| **VPS Deployment (`deploy_vps.sh`)** | 🔴 **BROKEN** | Step 2 checks `/opt/dabarstream/$f` instead of `./$f`; `missing` variable is uninitialized (breaks under `set -u`); `bible.db` wrongly required. |
| **Capture Client (`client.py`)** | 🔴 **CRASH RISK** | `sio.emit("verse_triggered")` has no `try/except` (crashes capture loop if disconnected); no automatic reconnect retry if VPS is unreachable at startup. |
| **Secret Hygiene** | 🔴 **SECURITY LEAK** | Hardcoded 64-character hex secret is currently stored as the default `STREAM_KEY` in `client.py`. |
| **Dependencies (`requirements.txt`)** | 🟡 Missing `sounddevice` | `client.py` uses `sounddevice` as preferred backend, but it is omitted from `requirements.txt`. VPS lacks `simple-websocket`/`eventlet`. |
| **Control Panel (`/control`)** | 🟡 Partial Phase 1 | JS event handlers for slides and timers exist, but corresponding HTML inputs/buttons were never added to the page body. |
| **Repo Hygiene** | 🔴 Cluttered | 0-byte `clip.exe`, 28MB `python-3.13.exe`, diff dump `s`, duplicate `server_new.py`, temporary patch scripts (`patcher*.py`). Malformed line in `.gitignore`. |

---

## 2. Issues Identified

### P0 (Critical / Blocker / Security)
1. **`deploy_vps.sh` Step 2 Fails on Fresh Deployment**:
   - The script inspects `/opt/dabarstream/$f` before files are copied there from `/tmp/dabarstream/`.
   - Variable `missing` is not initialized to `0`, causing an `unbound variable` exit with `set -euo pipefail`.
   - `bible.db` is included in the fatal check even though line 30 states it is optional for initial setup.
2. **`client.py` Crashes on VPS Disconnect**:
   - In `process_audio()`, `sio.emit("verse_triggered", ...)` is unhandled. If the VPS is down or connection drops, `python-socketio` throws `BadNamespaceError` or `ConnectionError`, terminating `capture_loop` and killing the client process mid-service.
   - If `sio.connect()` fails at startup, the client prints an error and continues without scheduling any reconnection attempts; any subsequent verse recognition immediately crashes.
3. **Hardcoded Stream Key in `client.py`**:
   - `STREAM_KEY = os.environ.get("DABARSTREAM_KEY", "9ca795cf458fe17806d8c07df380de6355df44ceda989cb4c57a26a21829e9f5")`. This secret should not be committed to source control.

### P1 (High Priority / Feature & Reliability Gaps)
4. **Missing `sounddevice` in `requirements.txt`**:
   - Users installing from `requirements.txt` will fail to get `sounddevice`, forcing fallback to `pyaudio` which cannot compile on newer Pythons without MSVC C++ tools.
5. **VPS WebSockets Support (`deploy_vps.sh`)**:
   - Installing only `flask flask-socketio` without `simple-websocket` forces Flask-SocketIO into Werkzeug HTTP long-polling with high latency. Adding `simple-websocket` enables efficient native WebSockets.
6. **Incomplete Control Panel (`CONTROL_HTML`)**:
   - Slide sending (`#slide-btn`, `#slide-title`, `#slide-lines`) and timer controls (`#timer-start`, `#timer-stop`, `#timer-clear`, `#timer-minutes`, `#timer-label`) have JavaScript logic in `CONTROL_HTML`, but the HTML markup is completely missing from the UI.
7. **Thread Blocking in `capture_loop`**:
   - Speech transcription with `WhisperModel.transcribe()` runs synchronously in the audio capture loop, which can cause audio buffer overflows and lost words during continuous speech.

### P2 (Medium Priority / Cleanliness & Maintenance)
8. **Workspace Pollution**:
   - Remove throwaway scripts (`patcher.py`, `patcher2.py`, `patcher3.py`, `patch_server.py`, `debug_read.py`, `server_copy.py`, `server_new.py`, `s`).
   - Remove binary files (`clip.exe`, `python-3.13.exe`).
9. **`.gitignore` Fixes**:
   - Fix mangled line `. s n a p s h o t s /  ` -> `.snapshots/`.
   - Add `*.exe`, `*.key`, `*.pem`, `*.out` to prevent committing binaries and SSH keys.

---

## 3. Proposed Changes & Action Plan

### Phase 1: Fix Deployment & Runtime Stability (P0)

#### [deploy_vps.sh](file:///c:/Users/Jabs/Documents/GitHub/DabarStream/deploy_vps.sh)
- Fix step 2 file existence check to verify current working directory (`"$f"` instead of `"/opt/dabarstream/$f"`).
- Initialize `missing=0`.
- Keep `bible.db` out of the required list so the service can deploy even if the database is transferred separately.
- Add `simple-websocket` to virtualenv pip install so Socket.IO gets real WebSocket transport.

#### [client.py](file:///c:/Users/Jabs/Documents/GitHub/DabarStream/client.py)
- Remove hardcoded default secret: `STREAM_KEY = os.environ.get("DABARSTREAM_KEY", "")`.
- Wrap `sio.emit("verse_triggered", ...)` in a `try...except` block so network hiccups never crash the audio capture loop.
- Add a resilient connection manager / background retry thread that connects and reconnects to `VPS_URL` automatically if disconnected.

#### [requirements.txt](file:///c:/Users/Jabs/Documents/GitHub/DabarStream/requirements.txt)
- Add `sounddevice` under the capture client dependencies.
- Add `simple-websocket` under core/server dependencies for fast WebSocket support.

---

### Phase 2: Complete Control Panel UI & Overlay Integration (P1)

#### [server.py](file:///c:/Users/Jabs/Documents/GitHub/DabarStream/server.py)
- Update `CONTROL_HTML`: Add structured HTML cards/sections for:
  - **Scripture Sender** (existing)
  - **Slide Presenter** (Title, Lines textarea, Send Slide button)
  - **Stage Timer** (Minutes input, Label input, Start, Stop, Clear buttons)
- Add overlay support for `update_slide` in `OVERLAY_HTML` if slide lower-thirds / announcements are desired on the stream.
- Provide environment-variable toggle for debug logging (`DEBUG_SOCKETIO = os.environ.get("DEBUG_SOCKETIO", "0") == "1"`) so journal logs aren't inundated in production.

---

### Phase 3: Cleanup & Hygiene (P2)

#### [.gitignore](file:///c:/Users/Jabs/Documents/GitHub/DabarStream/.gitignore)
- Fix `.snapshots/` formatting.
- Add `*.exe`, `*.key`, `*.pem`, `*.out`, `debug_out.txt`.

#### File Removal
- Delete junk files: `clip.exe`, `python-3.13.exe`, `s`, `server_copy.py`, `server_new.py`, `debug_read.py`, `patcher.py`, `patcher2.py`, `patcher3.py`, `patch_server.py`.
- Ensure useful diagnostics (`check_vps.sh`, `check_books.py`, `check_from_windows.ps1`, `reimport.py`, `run_client.ps1`) are cleanly preserved and documented.

---

## 4. Verification Plan

### Automated Tests
- Run full pytest test suite in `.venv`:
  ```powershell
  .venv\Scripts\pytest -v
  ```
- Add unit tests for:
  - `client.py` graceful handling of `sio.emit` failure when disconnected.
  - `CONTROL_HTML` containing the required slide and timer form elements.
  - `deploy_vps.sh` syntax validation (`bash -n deploy_vps.sh` or shellcheck if available).

### Manual Verification
- Verify `check_books.py` runs with no errors.
- Test `server.py` startup locally with `python server.py`.
- Verify `/control` page in browser: confirm buttons, slide inputs, timer controls, and language buttons render properly.
- Verify `git status` shows clean, tracked files with zero unintended binaries or leaks.
