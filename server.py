"""
DabarStream - Cloud WebSocket Engine
Runs on the Oracle Linux 10 VPS. Receives verse triggers from the local
Windows capture client, resolves book aliases, queries SQLite, and
broadcasts scripture to OBS browser-source overlays.
"""

import hmac
import os
import re
import sqlite3
import json
import time

from flask import Flask, render_template_string, request, jsonify
from flask_socketio import SocketIO, emit

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*")

DB_PATH = "bible.db"

# Comprehensive map for speech-to-text anomalies and short prefixes
BOOK_ALIASES = {
    # Shorthand Acronyms
    "gen": "genesis", "exo": "exodus", "lev": "leviticus", "num": "numbers",
    "deut": "deuteronomy", "josh": "joshua", "judg": "judges", "sam": "samuel",
    "chr": "chronicles", "ps": "psalms", "prov": "proverbs", "eccl": "ecclesiastes",
    "isa": "isaiah", "jer": "jeremiah", "lam": "lamentations", "ezek": "ezekiel",
    "dan": "daniel", "hos": "hosea", "obad": "obadiah", "mic": "micah",
    "nah": "nahum", "hab": "habakkuk", "zeph": "zephaniah", "hag": "haggai",
    "zech": "zechariah", "mal": "malachi", "mat": "matthew", "mk": "mark",
    "lk": "luke", "jn": "john", "act": "acts", "rom": "romans",
    "cor": "corinthians", "gal": "galatians", "eph": "ephesians",
    "phil": "philippians", "col": "colossians", "thess": "thessalonians",
    "tim": "timothy", "tit": "titus", "phm": "philemon", "heb": "hebrews",
    "jas": "james", "pet": "peter", "rev": "revelation",
    # --- Spelling variants Whisper commonly returns for canonical names ---
    "psalm": "psalms", "psalms of david": "psalms",
    "song of songs": "song of solomon", "canticles": "song of solomon",
    "revelations": "revelation", "apocalypse": "revelation",
    # Spoken Number Variations to Standard Books
    "first john": "1 john", "1st john": "1 john", "second john": "2 john",
    "2nd john": "2 john", "third john": "3 john", "3rd john": "3 john",
    "first peter": "1 peter", "1st peter": "1 peter", "second peter": "2 peter",
    "2nd peter": "2 peter", "first timothy": "1 timothy", "1st timothy": "1 timothy",
    "second timothy": "2 timothy", "2nd timothy": "2 timothy",
    "first corinthians": "1 corinthians", "1st corinthians": "1 corinthians",
    "second corinthians": "2 corinthians", "2nd corinthians": "2 corinthians",
    "first thessalonians": "1 thessalonians", "1st thessalonians": "1 thessalonians",
    "second thessalonians": "2 thessalonians", "2nd thessalonians": "2 thessalonians",
    "first samuel": "1 samuel", "1st samuel": "1 samuel", "second samuel": "2 samuel",
    "2nd samuel": "2 samuel", "first kings": "1 kings", "1st kings": "1 kings",
    "second kings": "2 kings", "2nd kings": "2 kings",
    "first chronicles": "1 chronicles", "1st chronicles": "1 chronicles",
    "second chronicles": "2 chronicles", "2nd chronicles": "2 chronicles",
    # --- Nyanja / Chewa / Nsenga book names ---
    "chibandakazi": "genesis", "yohane": "john", "mateyu": "matthew",
    "marko": "mark", "lukasi": "luke", "machitidwe": "acts", "aroma": "romans",
    "agalatiya": "galatians", "efeso": "ephesians", "afilipi": "philippians",
    "akolose": "colossians", "atimotheo": "timothy", "filemoni": "philemon",
    "abahebri": "hebrews", "yakobo": "james", "pita": "peter",
    "chivumbulutso": "revelation",
    # --- Bemba book names ---
    "ututendelo": "genesis", "ukufuma": "exodus", "abena roma": "romans",
    "mafumu": "kings", "1 mafumu": "1 kings", "2 mafumu": "2 kings",
    "imilimo": "acts", "matayo": "matthew", "mako": "mark", "luka": "luke",
    "petulo": "peter", "salimo": "psalms", "esaya": "isaiah",
    "yeremiya": "jeremiah", "ezekieli": "ezekiel", "danieli": "daniel",
    "hoseya": "hosea", "yoswa": "joshua", "abalamuzi": "judges",
    "rute": "ruth", "samweli": "samuel", "1 samweli": "1 samuel",
    "2 samweli": "2 samuel", "ezira": "ezra", "nehemiya": "nehemiah",
    "esiteli": "esther", "yobu": "job", "amalango": "leviticus",
    "abena korinto": "corinthians", "abena galatiya": "galatians",
    "abena efeso": "ephesians", "abena filipi": "philippians",
    "abena kolosai": "colossians", "abena tesalonika": "thessalonians",
    "abena heburani": "hebrews", "ukubvumbuluka": "revelation",
    # --- Chewa / Nyanja: book names shared with the Bemba map above ---
    "eksodo": "exodus", "deuteronomo": "deuteronomy", "levitiko": "leviticus",
    "numeri": "numbers", "owalamula": "judges", "masalmo": "psalms",
    "masalimo": "psalms", "miyambo": "proverbs", "mlaliki": "ecclesiastes",
    "nyimbo ya solomoni": "song of solomon", "yesaya": "isaiah",
    "yeremiya": "jeremiah", "maliro": "lamentations", "yoweli": "joel",
    "amosi": "amos", "obadiya": "obadiah", "yona": "jonah", "mika": "micah",
    "nahumu": "nahum", "habakuku": "habakkuk", "sefaniya": "zephaniah",
    "hagai": "haggai", "zekariya": "zechariah", "malaki": "malachi",
    "akorinto": "corinthians", "tesalonika": "thessalonians",
    "timoteo": "timothy", "yuda": "jude",
    "1 akorinto": "1 corinthians", "2 akorinto": "2 corinthians",
    "1 tesalonika": "1 thessalonians", "2 tesalonika": "2 thessalonians",
    "1 timoteo": "1 timothy", "2 timoteo": "2 timothy",
    "1 petulo": "1 peter", "2 petulo": "2 peter",
    "1 yohane": "1 john", "2 yohane": "2 john", "3 yohane": "3 john",
    # --- Tonga book names ---
    "machingonzi": "genesis",
}

# Translation codes -> display labels for the overlay
TRANSLATION_LABELS = {
    "eng": "English",        # primary line needs no label
    "bem": "Icibemba",
    "nya": "Chinyanja",
    "ton": "Chitonga",
}

# Currently active translation (voice/hotkey/panel switchable)
CURRENT_LANG = "eng"

# Shared secret protecting the Socket.IO control events. Set DABARSTREAM_KEY
# in the environment on BOTH the VPS (server) and the streaming PC (client,
# control-panel key field). When unset, auth is disabled (local dev only) --
# never expose an unauthenticated server to the internet.
ENV_KEY_NAME = "DABARSTREAM_KEY"


def _required_key() -> str:
    return os.environ.get(ENV_KEY_NAME, "")


def is_authorized(data) -> bool:
    """True when the payload carries the configured stream key (or none is set)."""
    required = _required_key()
    if not required:
        return True
    if not isinstance(data, dict):
        return False
    provided = data.get("key", "")
    return hmac.compare_digest(str(provided), required)


def validate_verse_payload(data):
    """Validates an incoming verse trigger.

    Returns {"book", "chapter", "verse", "lang"} with chapter/verse coerced
    to ranged ints, or None when the payload is malformed and must be ignored.
    """
    if not isinstance(data, dict):
        return None
    book = data.get("book", "")
    if not isinstance(book, str):
        return None
    book = " ".join(book.split())
    if not book or len(book) > 80:
        return None
    try:
        chapter = int(str(data.get("chapter", "")).strip())
        verse = int(str(data.get("verse", "")).strip())
    except (TypeError, ValueError, AttributeError):
        return None
    if not 1 <= chapter <= 150 or not 1 <= verse <= 176:
        return None
    lang = normalize_lang(data.get("lang") or CURRENT_LANG) or "eng"
    return {"book": book, "chapter": chapter, "verse": verse, "lang": lang}


# Whitelisted style keys for slide themes (Phase 1: named presets on overlay side)
_THEME_KEYS = {"bg", "color", "align", "font"}


def validate_slide_payload(data):
    """Validates a generic slide payload (Projects/Slides feature seed).

    Returns {"title", "lines", "theme"} or None when malformed. `lines` is a
    list of at most 20 strings of at most 500 chars each; `theme` (optional)
    is a dict whose keys are limited to bg/color/align/font with short values.
    """
    if not isinstance(data, dict):
        return None
    title = data.get("title", "")
    if not isinstance(title, str):
        return None
    title = " ".join(title.split())
    if not title or len(title) > 120:
        return None
    lines = data.get("lines")
    if not isinstance(lines, list) or not 1 <= len(lines) <= 20:
        return None
    cleaned_lines = []
    for line in lines:
        if not isinstance(line, str):
            return None
        line = " ".join(line.split())
        if not line or len(line) > 100:
            return None
        cleaned_lines.append(line)
    theme = data.get("theme")
    cleaned_theme = {}
    if theme is not None:
        if not isinstance(theme, dict):
            return None
        for key, value in theme.items():
            if key not in _THEME_KEYS or not isinstance(value, str) or len(value) > 40:
                return None
            cleaned_theme[key] = value
    return {"title": title, "lines": cleaned_lines, "theme": cleaned_theme}


def validate_timer_payload(data):
    """Validates a timer control payload.

    Returns {"action", "label", "ends_at"} where ends_at is an epoch timestamp
    (only for 'start') or None when malformed.
    """
    if not isinstance(data, dict):
        return None
    action = data.get("action")
    if action not in ("start", "stop", "clear"):
        return None
    label = data.get("label", "")
    if not isinstance(label, str) or len(label) > 60:
        return None
    label = " ".join(label.split())
    ends_at = None
    if action == "start":
        try:
            minutes = float(data.get("minutes", 0))
        except (TypeError, ValueError):
            return None
        if not 0.1 <= minutes <= 1440:
            return None
        ends_at = round(time.time() + minutes * 60, 1)
    return {"action": action, "label": label, "ends_at": ends_at}

OVERLAY_HTML = """
<!DOCTYPE html>
<html>
<head>
    <script src="/socket.io/socket.io.js"></script>
    <style>
        body { background-color: transparent; margin: 0px; overflow: hidden;
               font-family: 'Segoe UI', Tahoma, sans-serif; color: #ffffff; }
        #lower-third { position: absolute; bottom: 50px; left: 5%; width: 90%;
               background: rgba(0, 0, 0, 0.75); border-left: 8px solid #ffcc00;
               padding: 20px; box-sizing: border-box; border-radius: 4px;
               opacity: 0; transition: opacity 0.5s ease-in-out; }
        #reference { font-size: 24px; font-weight: bold; color: #ffcc00; margin-bottom: 5px; }
        #lang-label { font-size: 14px; font-style: italic; color: #cccccc; margin-bottom: 5px; }
        #text { font-size: 20px; line-height: 1.4; }
        #text-eng { font-size: 15px; line-height: 1.35; color: #dddddd;
                    font-style: italic; margin-top: 8px; display: none; }
    </style>
</head>
<body>
    <div id="lower-third">
        <div id="reference"></div>
        <div id="lang-label"></div>
        <div id="text"></div>
        <div id="text-eng"></div>
    </div>
    <script>
        const socket = io();
        const box = document.getElementById('lower-third');
        const refElement = document.getElementById('reference');
        const langLabel = document.getElementById('lang-label');
        const textElement = document.getElementById('text');
        const textEngElement = document.getElementById('text-eng');
        let hideTimeout;
        socket.on('language_changed', function(d) {
            // Brief banner announcing the active translation
            refElement.innerText = (d.lang_label || d.lang).toUpperCase();
            textElement.innerText = 'Now displaying in ' + (d.lang_label || d.lang);
            textEngElement.style.display = "none";
            box.style.opacity = "1";
            clearTimeout(hideTimeout);
            hideTimeout = setTimeout(() => { box.style.opacity = "0"; }, 3000);
        });
        socket.on('clear_overlay', function() {
            clearTimeout(hideTimeout);
            box.style.opacity = "0";
        });
        socket.on('update_overlay', function(data) {
            clearTimeout(hideTimeout);
            refElement.innerText = data.book + " " + data.chapter + ":" + data.verse;
            langLabel.innerText = data.lang_label || "";
            textElement.innerText = data.text;
            if (data.text_eng) {
                textEngElement.innerText = data.book_eng + " " + data.chapter + ":" + data.verse + " - " + data.text_eng;
                textEngElement.style.display = "block";
            } else {
                textEngElement.style.display = "none";
            }
            box.style.opacity = "1";
            hideTimeout = setTimeout(() => { box.style.opacity = "0"; }, 15000);
        });
    </script>
</body>
</html>
"""


def normalize_lang(code) -> str:
    """
    Validates a translation code coming from the client (voice command,
    hotkey, control panel, or verse payload). Returns the cleaned code when
    it is a known translation, otherwise None.
    """
    lang = (code or "").strip().lower()
    return lang if lang in TRANSLATION_LABELS else None


def resolve_book(raw_book: str) -> str:
    """Normalizes spoken variants and abbreviations into database keys."""
    clean_book = raw_book.strip().lower().replace(".", "")
    target_book = BOOK_ALIASES.get(clean_book, clean_book)
    target_book = re.sub(r"\b1st\b", "1", target_book)
    target_book = re.sub(r"\b2nd\b", "2", target_book)
    target_book = re.sub(r"\b3rd\b", "3", target_book)
    return " ".join(target_book.split())


def resolve_and_query_bible(raw_book, chapter, verse, translation_code: str = "eng",
                            db_path: str = None):
    """
    Parses verbal shortcuts (in any supported language) and queries the
    database for the requested translation. Falls back to the English
    translation ('eng') when the requested language lacks the verse.

    `db_path` is resolved at CALL time (not import time) so tests and the
    desktop runtime can point at a different database.
    """
    db_path = db_path or DB_PATH
    try:
        target_book = resolve_book(raw_book)
        print(f"[Routing Resolver]: '{raw_book}' -> '{target_book}' [{translation_code}]")
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT book_name, chapter, verse, text FROM verses "
            "WHERE translation_code=? AND book_normalized=? AND chapter=? AND verse=?",
            (translation_code, target_book, int(chapter), int(verse)),
        )
        res = cursor.fetchone()
        if res is None and translation_code != "eng":
            # Language fallback: keep coordinates, use English text
            cursor.execute(
                "SELECT book_name, chapter, verse, text FROM verses "
                "WHERE translation_code='eng' AND book_normalized=? AND chapter=? AND verse=?",
                (target_book, int(chapter), int(verse)),
            )
            res = cursor.fetchone()
        conn.close()
        return res
    except Exception as e:
        print(f"[Resolver Error]: Mapping failed - {e}")
        return None


@app.route("/overlay")
def show_overlay():
    return render_template_string(OVERLAY_HTML)


@app.route("/health")
def health():
    """Liveness + readiness probe, used to verify a deployment.

    Keys are additive: "status" is always present so existing checks keep
    working, and the database fields let one curl confirm that bible.db was
    actually deployed (a missing database serves no verses but still runs).
    """
    info = {"status": "ok"}
    info["db_path"] = os.path.abspath(DB_PATH)
    info["db_exists"] = os.path.exists(DB_PATH)
    if info["db_exists"]:
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM verses")
            info["verses"] = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(DISTINCT translation_code) FROM verses")
            info["translations"] = cursor.fetchone()[0]
            conn.close()
        except Exception as exc:  # noqa: BLE001 - report, never 500
            info["db_error"] = f"{type(exc).__name__}: {exc}"
    else:
        info["db_error"] = "bible.db not found next to server.py"
    return info


CONTROL_HTML = """
<!DOCTYPE html>
<html>
<head>
    <script src="/socket.io/socket.io.js"></script>
    <style>
        body { font-family: 'Segoe UI', sans-serif; background: #1a1a2e; color: #eee;
               display: flex; flex-direction: column; align-items: center; padding: 30px; }
        h1 { font-size: 20px; }
        .row { display: flex; gap: 10px; margin: 10px 0; flex-wrap: wrap; justify-content: center; }
        button, select, input { padding: 10px 16px; border-radius: 6px; border: 1px solid #444;
               background: #16213e; color: #eee; font-size: 15px; cursor: pointer; }
        button:hover { background: #0f3460; }
        button.active { background: #e94560; border-color: #e94560; }
        #status { margin-top: 15px; color: #ffcc00; min-height: 20px; }
        form { display: flex; gap: 8px; flex-wrap: wrap; justify-content: center; margin-top: 10px; }
        input { width: 80px; }
        input.book { width: 160px; }
        input.key { width: 200px; }
        .danger { margin-top: 10px; }
    </style>
</head>
<body>
    <h1>DabarStream Control Panel</h1>
    <div class="row"><input class="key" id="stream-key" type="password"
        placeholder="Stream key (DABARSTREAM_KEY)"></div>
    <div class="row" id="lang-buttons"></div>
    <form id="verse-form">
        <input class="book" id="book" placeholder="Book (e.g. Yohane)">
        <input id="chapter" type="number" min="1" placeholder="Ch">
        <input id="verse" type="number" min="1" placeholder="Vs">
        <button type="submit">Send Verse</button>
    </form>
    <div class="row danger"><button id="clear-btn">Clear Overlay</button></div>
    <div id="status"></div>
    <script>
        const socket = io();
        const labels = ["eng|English", "bem|Icibemba", "nya|Chinyanja", "ton|Chitonga"];
        const box = document.getElementById('lang-buttons');
        const status = document.getElementById('status');
        let currentLang = 'eng';

        function streamKey() { return document.getElementById('stream-key').value; }

        labels.forEach(item => {
            const [code, label] = item.split('|');
            const btn = document.createElement('button');
            btn.textContent = label;
            btn.dataset.lang = code;
            btn.onclick = () => socket.emit('set_language', {lang: code, key: streamKey()});
            box.appendChild(btn);
        });

        function refresh() {
            box.querySelectorAll('button').forEach(b =>
                b.classList.toggle('active', b.dataset.lang === currentLang));
        }

        socket.on('language_changed', d => { currentLang = d.lang; refresh();
            status.innerText = 'Language: ' + (d.lang_label || d.lang); });
        socket.on('connect', () => status.innerText = 'Connected.');

        document.getElementById('verse-form').onsubmit = e => {
            e.preventDefault();
            socket.emit('verse_triggered', {
                book: document.getElementById('book').value,
                chapter: document.getElementById('chapter').value,
                verse: document.getElementById('verse').value,
                key: streamKey(),
            });
            status.innerText = 'Verse sent.';
        };

        document.getElementById('clear-btn').onclick = () => {
            socket.emit('clear_overlay', {key: streamKey()});
            status.innerText = 'Overlay cleared.';
        };

        // Phase 1: slide + timer controls. Null-guarded so this script keeps
        // working even before the matching HTML inputs/buttons are added.
        const slideBtn = document.getElementById('slide-btn');
        if (slideBtn) slideBtn.onclick = () => {
            const title = document.getElementById('slide-title').value.trim();
            const lines = document.getElementById('slide-lines').value
                .split('\n').map(s => s.trim()).filter(Boolean);
            if (!title || !lines.length) {
                status.innerText = 'Slide needs a title and at least one line.';
                return;
            }
            socket.emit('show_slide', {title: title, lines: lines, key: streamKey()});
            status.innerText = 'Slide sent.';
        };

        const timerStart = document.getElementById('timer-start');
        if (timerStart) timerStart.onclick = () => {
            const minutes = parseFloat(document.getElementById('timer-minutes').value);
            const label = document.getElementById('timer-label').value.trim();
            if (!minutes || minutes <= 0) {
                status.innerText = 'Timer needs minutes greater than 0.';
                return;
            }
            socket.emit('timer_control', {action: 'start', minutes: minutes, label: label, key: streamKey()});
            status.innerText = 'Timer started.';
        };
        const timerStop = document.getElementById('timer-stop');
        if (timerStop) timerStop.onclick = () => {
            socket.emit('timer_control', {
                action: 'stop',
                label: (document.getElementById('timer-label') || {}).value || '',
                key: streamKey()
            });
            status.innerText = 'Timer stopped.';
        };
        const timerClear = document.getElementById('timer-clear');
        if (timerClear) timerClear.onclick = () => {
            socket.emit('timer_control', {action: 'clear', key: streamKey()});
            status.innerText = 'Timer cleared.';
        };

        refresh();
    </script>
</body>
</html>
"""


@app.route("/control")
def control_panel():
    return render_template_string(CONTROL_HTML)


# Stage display: a text-only confidence monitor for people on stage.
# Shows the current verse/slide/timer without styling or transparency tricks.
STAGE_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>DabarStream - Stage Display</title>
    <script src="/socket.io/socket.io.js"></script>
    <style>
        body { margin: 0; background: #111; color: #fff; font-family: 'Segoe UI', sans-serif;
               display: flex; flex-direction: column; align-items: center; padding: 24px; }
        #clock { font-size: 28px; color: #9ad; }
        #label { font-size: 34px; font-weight: bold; color: #ffcc00; margin: 16px 0 8px; }
        #body { font-size: 40px; line-height: 1.5; text-align: center; max-width: 90%; }
        #timer { font-size: 56px; color: #7f7; margin-top: 24px; }
    </style>
</head>
<body>
    <div id="clock">--:--</div>
    <div id="label">DabarStream Stage</div>
    <div id="body">Waiting for the current item...</div>
    <div id="timer"></div>
    <script>
        const socket = io();
        const label = document.getElementById('label');
        const body = document.getElementById('body');
        const timer = document.getElementById('timer');
        let endsAt = null, timerLabel = '', timerTick = null;

        function show(title, lines) {
            label.innerText = title;
            body.innerHTML = '';
            for (const line of lines) {
                const p = document.createElement('div');
                p.innerText = line;
                body.appendChild(p);
            }
            timer.innerText = '';
            endsAt = null;
        }

        function clockTick() {
            const now = new Date();
            document.getElementById('clock').innerText =
                now.getHours().toString().padStart(2, '0') + ':' +
                now.getMinutes().toString().padStart(2, '0') + ':' +
                now.getSeconds().toString().padStart(2, '0');
        }
        setInterval(clockTick, 1000); clockTick();

        socket.on('update_overlay', (d) => {
            show(d.book + ' ' + d.chapter + ':' + d.verse + (d.lang_label ? ' [' + d.lang_label + ']' : ''),
                 [d.text].concat(d.text_eng ? ['— ' + d.text_eng] : []));
        });
        socket.on('update_slide', (d) => show(d.title, d.lines));
        socket.on('clear_overlay', () => {
            label.innerText = 'DabarStream Stage';
            body.innerText = '';
            timer.innerText = '';
            endsAt = null;
        });
        socket.on('language_changed', () => {}); // stage shows content only

        socket.on('timer_update', (d) => {
            if (d.action === 'start') {
                endsAt = d.ends_at; timerLabel = d.label || 'Timer';
                clearInterval(timerTick);
                timerTick = setInterval(() => {
                    const left = Math.max(0, Math.round(endsAt - Date.now() / 1000));
                    const m = Math.floor(left / 60), s = left % 60;
                    timer.innerText = timerLabel + ': ' + m + ':' + s.toString().padStart(2, '0');
                    if (left <= 0) { clearInterval(timerTick); timerLabel = ''; }
                }, 500);
            } else if (d.action === 'stop') {
                endsAt = null; clearInterval(timerTick); timer.innerText = (d.label || '') + ' stopped';
            } else {
                endsAt = null; clearInterval(timerTick); timer.innerText = '';
            }
        });
    </script>
</body>
</html>
"""


@app.route("/stage")
def stage_display():
    return render_template_string(STAGE_HTML)


# ---------------------------------------------------------------------------
# Phase 2 (foundation) — Projects: named, git-friendly JSON slide collections.
# A project is any JSON object the client wants to persist (e.g. a list of
# saved slides). IDs are sanitized to safe filenames; writes require the
# shared key, reads are open in local-dev mode.
# ---------------------------------------------------------------------------
PROJECTS_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    os.getenv("DABARSTREAM_PROJECTS", "projects"),
)
os.makedirs(PROJECTS_DIR, exist_ok=True)

_PROJECT_ID_RE = re.compile(r"^[A-Za-z0-9 _.-]{1,64}$")


def _project_path(project_id):
    safe = (project_id or "").strip()
    if not _PROJECT_ID_RE.match(safe):
        return None
    fname = safe.replace("/", "_").replace("\\", "_").replace(" ", "_") + ".json"
    return os.path.join(PROJECTS_DIR, fname)


def save_project(project_id, payload):
    path = _project_path(project_id)
    if not path or not isinstance(payload, (dict, list)):
        return None
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    return path


def load_project(project_id):
    path = _project_path(project_id)
    if not path or not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def list_projects():
    out = []
    for name in os.listdir(PROJECTS_DIR):
        if name.endswith(".json"):
            base = name[:-5].replace("_", " ")
            out.append({"id": base, "url": "/api/projects/" + base})
    return sorted(out, key=lambda x: x["id"])


@app.route("/api/projects")
def api_list_projects():
    return jsonify(list_projects())


@app.route("/api/projects/<project_id>", methods=["GET"])
def api_get_project(project_id):
    data = load_project(project_id)
    if data is None:
        return jsonify({"error": "not found"}), 404
    return jsonify(data)


@app.route("/api/projects/<project_id>", methods=["POST"])
def api_save_project(project_id):
    if not is_authorized(request.args.to_dict(flat=True)):
        return jsonify({"error": "forbidden"}), 403
    path = save_project(project_id, request.get_json(silent=True))
    if path is None:
        return jsonify({"error": "invalid project id or payload"}), 400
    return jsonify({"saved": True, "id": project_id}), 201


@socketio.on("set_language")
def handle_set_language(data):
    """Voice/hotkey/panel language switch. Broadcasts the new state to all overlays."""
    global CURRENT_LANG
    if not is_authorized(data):
        print("[Auth]: Rejected set_language (bad or missing stream key)")
        return
    lang = normalize_lang((data or {}).get("lang"))
    if lang:
        CURRENT_LANG = lang
        print(f"[Language Switch]: Active translation is now '{lang}'")
        emit(
            "language_changed",
            {"lang": lang, "lang_label": TRANSLATION_LABELS.get(lang, lang)},
            broadcast=True,
        )
    else:
        print(f"[Language Switch]: Ignored unknown translation code '{(data or {}).get('lang')}'")


@socketio.on("verse_triggered")
def handle_verse(data):
    if not is_authorized(data):
        print("[Auth]: Rejected verse_triggered (bad or missing stream key)")
        return
    cleaned = validate_verse_payload(data)
    if cleaned is None:
        print(f"[Validation]: Ignored malformed verse payload: {data!r}")
        return
    lang = cleaned["lang"]
    print(
        f"[Cloud Event Received]: Querying database for "
        f"{cleaned['book']} {cleaned['chapter']}:{cleaned['verse']} [{lang}]"
    )
    scripture = resolve_and_query_bible(
        cleaned["book"], cleaned["chapter"], cleaned["verse"], lang
    )

    if not scripture:
        # Last-resort fallback to English coordinates
        scripture = resolve_and_query_bible(
            cleaned["book"], cleaned["chapter"], cleaned["verse"], "eng"
        )
        if scripture:
            lang = "eng"

    if scripture:
        display_book, ch, vs, text = scripture
        print(f"[Broadcasting to OBS Web client]: {display_book} {ch}:{vs} [{lang}]")

        payload = {
            "book": display_book,
            "chapter": ch,
            "verse": vs,
            "text": text,
            "lang": lang,
            "lang_label": TRANSLATION_LABELS.get(lang, lang),
        }

        # Dual-language mode: also fetch the English text when the
        # requested translation is a local language, for side-by-side display.
        if lang != "eng":
            english = resolve_and_query_bible(
                cleaned["book"], cleaned["chapter"], cleaned["verse"], "eng"
            )
            if english:
                payload["text_eng"] = english[3]
                payload["book_eng"] = english[0]

        emit("update_overlay", payload, broadcast=True)


@socketio.on("clear_overlay")
def handle_clear_overlay(data):
    """Manual clear: immediately hides the overlay on every connected client."""
    if not is_authorized(data):
        print("[Auth]: Rejected clear_overlay (bad or missing stream key)")
        return
    print("[Overlay]: Clearing overlay on all clients")
    emit("clear_overlay", {}, broadcast=True)


@socketio.on("show_slide")
def handle_show_slide(data):
    """Phase 1 Projects/Slides seed: push a generic text slide to all outputs."""
    if not is_authorized(data):
        print("[Auth]: Rejected show_slide (bad or missing stream key)")
        return
    cleaned = validate_slide_payload(data)
    if cleaned is None:
        print(f"[Validation]: Ignored malformed slide payload: {data!r}")
        return
    print(f"[Slide]: Broadcasting '{cleaned['title']}' ({len(cleaned['lines'])} lines)")
    emit(
        "update_slide",
        {"title": cleaned["title"], "lines": cleaned["lines"], "theme": cleaned["theme"]},
        broadcast=True,
    )


@socketio.on("timer_control")
def handle_timer_control(data):
    """Phase 1 Timers: start/stop/clear a countdown on stage + overlays."""
    if not is_authorized(data):
        print("[Auth]: Rejected timer_control (bad or missing stream key)")
        return
    cleaned = validate_timer_payload(data)
    if cleaned is None:
        print(f"[Validation]: Ignored malformed timer payload: {data!r}")
        return
    print(f"[Timer]: {cleaned['action']} (label={cleaned['label']!r})")
    emit("timer_update", cleaned, broadcast=True)


if __name__ == "__main__":
    # Listen on all interfaces over port 5000.
    # allow_unsafe_werkzeug: the bundled Werkzeug server is fine for a single
    # church stream; without this flag newer Flask-SocketIO releases refuse to
    # start when it detects a production environment.
    socketio.run(app, host="0.0.0.0", port=5000, allow_unsafe_werkzeug=True)

