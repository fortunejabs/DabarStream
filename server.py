"""
DabarStream - Cloud WebSocket Engine
Runs on the Oracle Linux 10 VPS. Receives verse triggers from the local
Windows capture client, resolves book aliases, queries SQLite, and
broadcasts scripture to OBS browser-source overlays.
"""

import re
import sqlite3

from flask import Flask, render_template_string
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
    # --- Tonga book names ---
    "machingonzi": "genesis",
}

# Translation codes -> display labels for the overlay
TRANSLATION_LABELS = {
    "eng": "",        # primary line needs no label
    "bem": "Icibemba",
    "nya": "Chinyanja",
    "ton": "Chitonga",
}

# Currently active translation (voice/hotkey/panel switchable)
CURRENT_LANG = "eng"

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
                            db_path: str = DB_PATH):
    """
    Parses verbal shortcuts (in any supported language) and queries the
    database for the requested translation. Falls back to the English
    translation ('eng') when the requested language lacks the verse.
    """
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
    return {"status": "ok"}


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
    </style>
</head>
<body>
    <h1>DabarStream Control Panel</h1>
    <div class="row" id="lang-buttons"></div>
    <form id="verse-form">
        <input class="book" id="book" placeholder="Book (e.g. Yohane)">
        <input id="chapter" type="number" min="1" placeholder="Ch">
        <input id="verse" type="number" min="1" placeholder="Vs">
        <button type="submit">Send Verse</button>
    </form>
    <div id="status"></div>
    <script>
        const socket = io();
        const labels = ["eng|English", "bem|Icibemba", "nya|Chinyanja", "ton|Chitonga"];
        const box = document.getElementById('lang-buttons');
        const status = document.getElementById('status');
        let currentLang = 'eng';

        labels.forEach(item => {
            const [code, label] = item.split('|');
            const btn = document.createElement('button');
            btn.textContent = label;
            btn.dataset.lang = code;
            btn.onclick = () => socket.emit('set_language', {lang: code});
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
            });
            status.innerText = 'Verse sent.';
        };
        refresh();
    </script>
</body>
</html>
"""


@app.route("/control")
def control_panel():
    return render_template_string(CONTROL_HTML)


@socketio.on("set_language")
def handle_set_language(data):
    """Voice/hotkey/panel language switch. Broadcasts the new state to all overlays."""
    global CURRENT_LANG
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
    lang = data.get("lang") or CURRENT_LANG
    print(
        f"[Cloud Event Received]: Querying database for "
        f"{data['book']} {data['chapter']}:{data['verse']} [{lang}]"
    )
    scripture = resolve_and_query_bible(data["book"], data["chapter"], data["verse"], lang)

    if not scripture:
        # Last-resort fallback to English coordinates
        scripture = resolve_and_query_bible(data["book"], data["chapter"], data["verse"], "eng")
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
            english = resolve_and_query_bible(data["book"], data["chapter"], data["verse"], "eng")
            if english:
                payload["text_eng"] = english[3]
                payload["book_eng"] = english[0]

        emit("update_overlay", payload, broadcast=True)


if __name__ == "__main__":
    # Listen on all interfaces over port 5000
    socketio.run(app, host="0.0.0.0", port=5000)

