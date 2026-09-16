"""
DabarStream - Database Importer Engine (Multilingual)
Parses FreeShow JSON, BibleShow delimited text, EasyWorship CSV, and
Zefania/OpenSong XML translations (for Zambian/African language Bibles)
into a unified indexed SQLite schema (bible.db).

Each verse row carries a `translation_code` (e.g. 'eng', 'bem', 'nya',
'ton') so multiple languages live side-by-side in one database.
`book_normalized` always stores the CANONICAL ENGLISH key (e.g. 'john')
regardless of display language, so spoken aliases in any language
resolve via the server's BOOK_ALIASES map.
"""

import csv
import json
import sqlite3
import xml.etree.ElementTree as ET

DB_PATH = "bible.db"
DEFAULT_TRANSLATION = "eng"

# Local-language book names -> canonical English normalized keys.
# This is intentionally extensible: add missing spellings as you
# encounter them in your translation modules (or via an alias JSON file).
BOOK_LOCAL_TO_ENGLISH = {
    # --- Nyanja / Chewa / Nsenga ---
    "chibandakazi": "genesis",
    "yohane": "john",
    "mateyu": "matthew",
    "marko": "mark",
    "lukasi": "luke",
    "machitidwe": "acts",
    "aroma": "romans",
    "agalatiya": "galatians",
    "efeso": "ephesians",
    "afilipi": "philippians",
    "akolose": "colossians",
    "atimotheo": "timothy",
    "tito": "titus",
    "filemoni": "philemon",
    "abahebri": "hebrews",
    "yakobo": "james",
    "pita": "peter",
    "yuda": "jude",
    "chivumbulutso": "revelation",
    # --- Bemba ---
    "ututendelo": "genesis",
    "ukufuma": "exodus",
    "abena roma": "romans",
    # --- Tonga ---
    "machingonzi": "genesis",
}


def _local_name(tag: str) -> str:
    """Strips XML namespace, e.g. '{http://...}VERSE' -> 'VERSE'."""
    return tag.rsplit("}", 1)[-1].lower()


def canonical_book_key(raw_book: str) -> str:
    """
    Maps a book name in ANY supported language to the canonical English
    normalized key. Unknown names fall back to a lowercase cleanup, so
    imports never fail on missing mappings.
    """
    clean = raw_book.strip().lower().replace(".", "").replace(":", "")
    clean = " ".join(clean.split())
    return BOOK_LOCAL_TO_ENGLISH.get(clean, clean)


def init_database(db_path: str = DB_PATH):
    """Builds the database tables and operational performance indexes."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS verses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            translation_code TEXT NOT NULL DEFAULT 'eng',
            book_name TEXT NOT NULL,
            book_normalized TEXT NOT NULL,
            chapter INTEGER NOT NULL,
            verse INTEGER NOT NULL,
            text TEXT NOT NULL
        );
        """
    )
    # Language-bound index for instant multilingual queries
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_lang_book_chap_verse "
        "ON verses(translation_code, book_normalized, chapter, verse);"
    )
    # Retain the legacy index shape for compatibility with old queries
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_book_chap_verse "
        "ON verses(book_normalized, chapter, verse);"
    )
    conn.commit()
    conn.close()
    print("[Database Engine]: Relational tables initialized with performance indexing.")


def normalize_text(text: str) -> str:
    """Sanitizes text strings for storage mapping."""
    return " ".join(text.strip().split())


def _insert_records(records, source_label: str, db_path: str = DB_PATH):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.executemany(
        "INSERT INTO verses (translation_code, book_name, book_normalized, chapter, verse, text) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        records,
    )
    conn.commit()
    conn.close()
    print(f"[Importer Process]: Successfully loaded {len(records)} records from {source_label}.")


def import_freeshow_json(file_path: str, translation_code: str = DEFAULT_TRANSLATION,
                         db_path: str = DB_PATH):
    """
    Parses FreeShow style nested JSON formats.
    Expected Layout: {"Genesis": {"1": {"1": "In the beginning..."}}}
    """
    init_database(db_path)
    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    records = []
    for book, chapters in data.items():
        book_key = canonical_book_key(book)
        for chapter, verses in chapters.items():
            for verse, text in verses.items():
                records.append(
                    (translation_code, book.strip(), book_key,
                     int(chapter), int(verse), normalize_text(text))
                )
    _insert_records(records, f"FreeShow JSON format [{translation_code}]", db_path)



def import_bibleshow_csv(file_path: str, delimiter: str = "|",
                         translation_code: str = DEFAULT_TRANSLATION, db_path: str = DB_PATH):
    """
    Parses BibleShow delimited text structural configurations.
    Expected Line Format: Genesis|1|1|In the beginning...
    """
    init_database(db_path)
    records = []
    with open(file_path, "r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f, delimiter=delimiter)
        for row in reader:
            if len(row) >= 4:
                book, chapter, verse, text = row[0], row[1], row[2], row[3]
                records.append(
                    (
                        translation_code,
                        book.strip(),
                        canonical_book_key(book),
                        int(chapter),
                        int(verse),
                        normalize_text(text),
                    )
                )
    _insert_records(records, f"BibleShow structured data [{translation_code}]", db_path)


def import_easyworship_csv(file_path: str, translation_code: str = DEFAULT_TRANSLATION,
                           db_path: str = DB_PATH):
    """
    Parses standard EasyWorship exported text data.
    Expected Header: Book, Chapter, Verse, Text
    """
    init_database(db_path)
    records = []
    with open(file_path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            book = row.get("Book") or row.get("book")
            chapter = row.get("Chapter") or row.get("chapter")
            verse = row.get("Verse") or row.get("verse")
            text = row.get("Text") or row.get("text")
            if all([book, chapter, verse, text]):
                records.append(
                    (
                        translation_code,
                        book.strip(),
                        canonical_book_key(book),
                        int(chapter),
                        int(verse),
                        normalize_text(text),
                    )
                )
    _insert_records(records, f"EasyWorship export matrix [{translation_code}]", db_path)


# XML tag/attribute variants handled generically:
#   Zefania:  <BIBLEBOOK bname="Genesis"><CHAPTER cnumber="1"><VERSE vnumber="1">...</VERSE>
#   OpenSong: <b n="Genesis"><c n="1"><v n="1">...</v></c></b>
_BOOK_TAGS = {"biblebook", "b", "book"}
_CHAPTER_TAGS = {"chapter", "c"}
_VERSE_TAGS = {"verse", "v"}
_BOOK_NAME_ATTRS = ("bname", "bsname", "n", "name")
_CH_NUM_ATTRS = ("cnumber", "n", "number")
_VS_NUM_ATTRS = ("vnumber", "n", "number")


def _first_attr(element, attr_names):
    for a in attr_names:
        if a in element.attrib:
            return element.attrib[a]
    return None


def import_xml_translation(file_path: str, translation_code: str = DEFAULT_TRANSLATION,
                           db_path: str = DB_PATH):
    """
    Parses Zefania XML and OpenSong XML Bible modules - the most common
    open formats for African-language translations (Bemba, Nyanja/Chewa,
    Nsenga, Tonga, etc.). Handles namespaced and plain tags alike.
    """
    init_database(db_path)
    tree = ET.parse(file_path)
    root = tree.getroot()

    records = []
    for el in root.iter():
        if _local_name(el.tag) not in _BOOK_TAGS:
            continue
        book_name = _first_attr(el, _BOOK_NAME_ATTRS) or ""
        book_key = canonical_book_key(book_name)

        for chapter in el:
            if _local_name(chapter.tag) not in _CHAPTER_TAGS:
                continue
            ch_num = _first_attr(chapter, _CH_NUM_ATTRS)

            for verse in chapter:
                if _local_name(verse.tag) not in _VERSE_TAGS:
                    continue
                vs_num = _first_attr(verse, _VS_NUM_ATTRS)
                text = "".join(verse.itertext())

                if ch_num is None or vs_num is None:
                    continue
                records.append(
                    (
                        translation_code,
                        book_name.strip(),
                        book_key,
                        int(ch_num),
                        int(vs_num),
                        normalize_text(text),
                    )
                )
    _insert_records(records, f"Zefania/OpenSong XML [{translation_code}]", db_path)


if __name__ == "__main__":
    # Example execution paths. Uncomment and use as required:
    # import_freeshow_json("my_freeshow_bible.json")                          # English
    # import_bibleshow_csv("bibleshow_export.txt", delimiter="|")             # English
    # import_easyworship_csv("easyworship_export.csv")                        # English
    # import_xml_translation("bemba_baibele.xml", translation_code="bem")     # Icibemba
    # import_xml_translation("buku_lopatulika.xml", translation_code="nya")   # Chichewa/Nyanja
    # import_xml_translation("tonga_baibele.xml", translation_code="ton")     # Chitonga
    pass
