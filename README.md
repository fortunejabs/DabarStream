# DabarStream

AI-powered Bible streaming system: local CPU speech recognition on Windows detects
spoken scripture references, and a cloud-hosted overlay displays verses live in
OBS (or any software supporting browser sources / NDI via DistroAV).

> **Dabar** (Hebrew דָּבָר) — the active, event-creating word of God. **Stream** — what
> this software does: routing spoken scripture to screens in real time.

## Architecture
- `client.py` (Windows streaming PC): mic capture (PyAudio) -> faster-whisper INT8 transcription
  -> verse regex -> Socket.IO emit to VPS.
- `server.py` (Oracle Linux 10 VPS @ 193.123.179.93:5000): Flask-SocketIO engine,
  fuzzy book-alias resolver, SQLite lookup, serves `/overlay` for OBS Browser Source.
- `importer.py`: builds `bible.db` from FreeShow JSON, BibleShow delimited text,
  EasyWorship CSV, or Zefania/OpenSong XML (African-language modules).
- `import_bibles.py`: bulk-loads the curated `Bibles/` collection (Zambian
  languages + English majors, 33 translations) into `bible.db`; resumable.

## Multilingual translations
Every row carries a `translation_code` (`eng`, `bem`, `nya`, `ton`, ...), so all
translations coexist in one database and queries stay indexed. Local-language
book names (Yohane, Ututendelo, Machingonzi, ...) are stored under a canonical
English key so a reference spoken in any language resolves correctly. If the
requested language lacks a verse, the server falls back to English rather than
showing nothing. In dual-language mode the overlay renders the local language as
the primary line with English beneath it.

Import a local translation:
```python
import_xml_translation("Bemba_Bible.xml", translation_code="bem")
```

### What spoken references are detected

`client.parse_verse_reference` accepts both styles of Whisper output:

- **digits** — `"John 3:16"`, `"1 Timothy 3:16"`. The leading numeral is kept, so
  all twelve numbered books (1–2 Samuel, Kings, Chronicles, Corinthians,
  Thessalonians, Timothy, Peter, 1–3 John) resolve instead of silently losing
  the numeral.
- **spoken numbers** — `"John chapter three verse sixteen"`,
  `"Psalms twenty three verse one"`, compound values like
  `"one hundred and fifty"`, and the bare two-number form
  `"First John four eight"` (→ chapter 4, verse 8). A binding word keeps its
  compound meaning, so `"twenty three"` is 23, never 20:3.

The book itself may be English or any local alias (`Yohane`, `Ututendelo`) —
see `BOOK_ALIASES` in `server.py`.

### Bulk import of the whole `Bibles/` library

Drop XML modules into `Bibles/Holy-Bible-XML-Format-master/` and run:

```powershell
python import_bibles.py
```

It auto-detects the three schemas in use — **Holy-Bible-XML-Format**
(`<book number=><chapter number=><verse number=>`), **Zefania**
(`<BIBLEBOOK bnumber=><CHAPTER cnumber=><VERS vnumber=>`) and **OpenSong**
(`<b n=><c n=><v n=>`) — and is safe to re-run: a manifest
(`Bibles/_import_manifest.json`) skips files already imported, and a file that
fails or yields no verses is reported so you can inspect it without the run
dying half-way.

```powershell
python import_bibles.py                             # incremental (default)
python import_bibles.py --force eng_msg eng_nivuk   # re-import these codes only
python import_bibles.py --reset                     # ignore manifest; redo all
python import_bibles.py --report                    # + full per-book listing (slow)
```

`--report` prints the per-book breakdown for every translation. It re-scans the
whole table (~1M rows) and takes minutes, so it is opt-in; the default summary is
a fast one-line-per-translation verse count.

Use `--force` after changing the importer or a parser: it ignores the manifest
for the named `translation_code`s and replaces their rows, without rebuilding
every translation in the library.

Rules that keep every translation queryable:

- Book names are always normalised to the canonical English 66 (derived from the
  book number where the module provides one). Zefania modules sometimes ship a
  localised name — ISV literally ships German `Matthäus` — and the Zambian
  modules carry no names at all, so a single English key is used and
  local-language names are matched through `BOOK_LOCAL_TO_ENGLISH` / `BOOK_ALIASES`.
- Verse text is read with `itertext()`, so inline markup is preserved. King
  James 2000 wraps its red-letter words in `<STYLE css=...>`, which would
  otherwise truncate the verse at the first tag.
- Malformed rows are skipped rather than raising, so one odd module cannot abort
  the batch.

Licensing: `eng_niv`, `eng_esv`, `eng_nlt`, `eng_nkjv`, `eng_nasb`, `eng_csb`,
`eng_mev`, `eng_lsb`, `eng_gw`, `eng_net`, `eng_msg`, `eng_amp` and `eng_ampc`
are copyrighted. They are imported for **private/church use only** — do not
redistribute `bible.db` or those XML files publicly. `bible.db` is git-ignored,
so the built database never leaves your machine unless you move it yourself.

## Switching languages live
Three ways to change the active translation during a service:

1. **Voice** - just say it. Phrases in `client.py:LANG_COMMANDS` map to codes,
   e.g. "show Bemba" -> `bem`, "let us use Chinyanja" -> `nya`, "in English" -> `eng`.
   Longest phrase wins, so "in english" is not shadowed by "english".
2. **Hotkeys** - optional, requires `pip install pynput`:
   `1`=English `2`=Bemba `3`=Nyanja `4`=Tonga (edit `HOTKEY_LANGS` to remap).
3. **Control panel** - open `http://193.123.179.93:5000/control` in a browser for
   clickable language buttons plus a manual book/chapter/verse sender and a
   **Clear Overlay** button (also wired in the overlay page itself).

## Stream key (required before going live)

All Socket.IO control events (`verse_triggered`, `set_language`, `clear_overlay`)
must carry the shared secret. Set it on both sides:

```powershell
# Streaming PC (client) — PowerShell
$env:DABARSTREAM_KEY = "pick-a-long-random-secret"
```

```bash
# VPS (server) — add to the systemd unit or export before launch
Environment=DABARSTREAM_KEY=pick-a-long-random-secret
```

Without the key the server only accepts local-dev traffic. Type the same key into
the password field on `/control`. Malformed verse payloads (missing book,
non-numeric or out-of-range chapter/verse) are rejected and logged.

A spoken switch applies to every verse trigger that follows it until you switch
again, and each overlay shows a short banner announcing the new translation.

## Quick Start (local dev)
```powershell
# PowerShell 5.1+ / PowerShell 7
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pytest                      # validation suite (79 tests)
python import_bibles.py     # build bible.db from the Bibles/ collection
python server.py            # run locally for testing
python client.py            # run on streaming machine (requires mic)
```

## OBS Integration
1. Add Browser Source: `http://193.123.179.93:5000/overlay` (1920x1080, custom CSS not needed - transparent background built in).
2. For NDI output to vMix/Wirecast/etc., install DistroAV (obs-ndi) and enable NDI output.

## VPS Deployment (Oracle Linux 10)

Run these in order — steps 1–2 on your PC, the rest on the VPS.

1. **Build the database locally.** It is git-ignored, so it must be transferred
   separately:
   ```powershell
   python import_bibles.py     # writes bible.db (~1M rows, 33 translations)
   ```
2. **Copy the app files to the VPS:**
   ```powershell
   ssh opc@193.123.179.93 "mkdir -p /tmp/dabarstream"
   scp server.py importer.py bible.db dabarstream.service deploy_vps.sh opc@193.123.179.93:/tmp/dabarstream/
   ```
3. **Set the stream key first.** Edit the `Environment=DABARSTREAM_KEY=...` line in
   `dabarstream.service` before deploying — the service refuses to start while it
   still reads `change-me-before-deploy`. Use the same key as the streaming PC.
4. **Deploy:**
   ```bash
   cd /tmp/dabarstream && sudo bash deploy_vps.sh
   ```
   Creates a locked `dabarstream` service user, builds a **virtualenv** at
   `/opt/dabarstream/.venv` (Oracle Linux 10 marks the system python as
   externally managed — PEP 668 — so dependencies cannot go into it), opens
   TCP 5000, and enables the systemd service.
5. **Verify on the VPS** — this is the step that catches a missing database:
   ```bash
   curl -s http://127.0.0.1:5000/health
   ```
   Expect `{"status":"ok", ..., "db_exists":true, "verses":<big number>,
   "translations":33}`. If `verses` is 0 or `db_error` is set, `bible.db` did not
   arrive — copy it and `systemctl restart dabarstream`.
6. **Open the port in OCI** (console only — the script cannot do this):
   VCN → Subnet → Security List → Add Ingress Rule: Protocol TCP, Destination
   Port 5000, Source **your home IP** rather than `0.0.0.0/0`.
7. **Verify from your PC, then point OBS at it:**
   ```powershell
   curl http://193.123.179.93:5000/health
   ```
   - OBS Browser Source: `http://193.123.179.93:5000/overlay` (1920x1080, transparent)
   - Control panel: `http://193.123.179.93:5000/control`
   - Logs: `journalctl -u dabarstream -f`

To redeploy after a code change, repeat steps 2, 4 and 5.

