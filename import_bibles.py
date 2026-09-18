"""Import major Bible translations into bible.db (3 XML schemas, resumable).

Handles: Holy-Bible-XML-Format (<bible><testament><book number=><chapter
number=><verse number=>), Zefania (<XMLBIBLE><BIBLEBOOK bnumber=><CHAPTER
cnumber=><VERS vnumber=>) and OpenSong (<b><c><v n=>).

Normalisation rules that keep every translation queryable:
  * Book names are always the canonical English 66 (derived from the book
    number where available). Zefania modules often ship a localised bname
    (ISV literally ships German "Matthäus"), and the Zambian collection has
    no names at all - so a single English book_normalized key is used and
    local language names are matched via importer.BOOK_LOCAL_TO_ENGLISH and
    server.BOOK_ALIASES instead.
  * Verse text is gathered with itertext() so inline markup (KJ2000 red
    letters inside <STYLE css=...>) is not truncated, then whitespace is
    collapsed by the caller.
  * Unparseable rows/chapters are skipped rather than raising, and a file
    that yields 0 verses is reported and retried instead of being recorded.

A manifest (Bibles/_import_manifest.json) makes re-runs skip completed files.
Curated list = Zambian languages + the English majors the operator chose.
"""
import hashlib
import json
import os
import sqlite3
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from importer import init_database, BOOK_LOCAL_TO_ENGLISH  # noqa: E402

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Bibles",
                    "Holy-Bible-XML-Format-master")
DB_PATH = "bible.db"
MANIFEST = os.path.join("Bibles", "_import_manifest.json")

# Canonical Protestant canon: 1..66
BOOK_NAMES = [
    "Genesis", "Exodus", "Leviticus", "Numbers", "Deuteronomy", "Joshua",
    "Judges", "Ruth", "1 Samuel", "2 Samuel", "1 Kings", "2 Kings",
    "1 Chronicles", "2 Chronicles", "Ezra", "Nehemiah", "Esther", "Job",
    "Psalms", "Proverbs", "Ecclesiastes", "Song of Solomon", "Isaiah",
    "Jeremiah", "Lamentations", "Ezekiel", "Daniel", "Hosea", "Joel",
    "Amos", "Obadiah", "Jonah", "Micah", "Nahum", "Habakkuk", "Zephaniah",
    "Haggai", "Zechariah", "Malachi", "Matthew", "Mark", "Luke", "John",
    "Acts", "Romans", "1 Corinthians", "2 Corinthians", "Galatians",
    "Ephesians", "Philippians", "Colossians", "1 Thessalonians",
    "2 Thessalonians", "1 Timothy", "2 Timothy", "Titus", "Philemon",
    "Hebrews", "James", "1 Peter", "2 Peter", "1 John", "2 John", "3 John",
    "Jude", "Revelation",
]

# (relative path under BASE, translation_code, display label)
# License-safe only: public-domain English majors + the Zambian collection.
IMPORTS = [
    # --- Zambian (all editions; primary codes bem/nya used by voice switching) ---
    (r"Zambiantranslationbibles\BembaBible.xml", "bem", "Bemba (Ishiwi Lyakwa Lesa 2015)"),
    (r"Zambiantranslationbibles\Chewa2016Bible.xml", "nya", "Chewa 2016"),
    (r"Zambiantranslationbibles\ChibembaBible.xml", "bem_chibemba", "Chibemba (comparison copy)"),
    (r"Zambiantranslationbibles\Chewa1992Bible.xml", "nya_1992", "Chewa 1992"),
    (r"Zambiantranslationbibles\Chewa2014Bible.xml", "nya_2014", "Chewa 2014"),
    (r"Zambiantranslationbibles\ChewaBLYDCBible.xml", "nya_blydc", "Chewa BLYDC"),
    # --- English fallback set from 'other Bibles' (fill gaps + extras) ---
    (r"other Bibles\Bible_English_MSG.xml", "eng_msg", "The Message"),
    (r"other Bibles\Bible_English_GNB.xml", "eng_gnb", "Good News Bible"),
    (r"other Bibles\Bible_English_HCSB.xml", "eng_hcsb", "Holman Christian Standard Bible"),
    (r"other Bibles\Bible_English_NIV_UK.xml", "eng_nivuk", "NIV UK"),
    (r"other Bibles\Bible_English_RNKJV.xml", "eng_rnkjv", "Restored Name KJV"),
    (r"other Bibles\SF_2009-01-20_ENG_BBE_(BIBLE IN BASIC ENGLISH).xml", "eng_bbe", "Bible in Basic English"),
    (r"other Bibles\SF_2009-01-20_ENG_BIBLE_AKJV_(AMERICAN KING JAMES VERSION).xml", "eng_akjv", "American KJV"),
    (r"other Bibles\SF_2009-01-20_ENG_KJ2000_(KING JAMES 2000).xml", "eng_kj2000", "King James 2000"),
    (r"other Bibles\SF_2015-08-14_ENG_BWE96_(BIBLE IN WORLDWIDE ENGLISH).xml", "eng_bwe", "Worldwide English 1996"),
    (r"other Bibles\SF_2009-01-22_ENG_ISV_(ISV NT).xml", "eng_isv", "ISV (New Testament only)"),
    # --- English, Lockman Foundation (AMP / AMPC: personal & church use only,
    #     NOT redistributable - kept local + in the private repo by agreement) ---
    (r"EnglishAmplifiedBible.xml", "eng_amp", "Amplified Bible 2015"),
    (r"EnglishAmplifiedClassicBible.xml", "eng_ampc", "Amplified Classic 1987"),
    # --- English, modern translations (LICENSE WARNING: NIV/ESV/NLT/NKJV/NASB/
    #     CSB/MEV/LSB/GW/NET are all copyrighted by their publishers. These rows
    #     are for the user's private church use ONLY - do NOT redistribute
    #     bible.db or these XML files publicly.) ---
    (r"EnglishNIVBible.xml", "eng_niv", "New International Version"),
    (r"EnglishESVBible.xml", "eng_esv", "English Standard Version"),
    (r"EnglishNLTBible.xml", "eng_nlt", "New Living Translation"),
    (r"EnglishNKJBible.xml", "eng_nkjv", "New King James Version"),
    (r"EnglishNASBBible.xml", "eng_nasb", "New American Standard Bible"),
    (r"EnglishCSBBible.xml", "eng_csb", "Christian Standard Bible"),
    (r"EnglishMEVBible.xml", "eng_mev", "Modern English Version"),
    (r"EnglishLSBBible.xml", "eng_lsb", "Legacy Standard Bible"),
    (r"EnglishGWBible.xml", "eng_gw", "God's Word"),
    (r"EnglishNETBible.xml", "eng_net", "New English Translation"),
    # MSG: no XML source in this repo (only .bib / folder formats) - add later
    # if an XML copy is obtained.
    # --- English, public domain ---
    (r"EnglishKJBible.xml", "eng", "King James Version"),
    (r"EnglishASVBible.xml", "eng_asv", "American Standard Version 1901"),
    (r"EnglishDarbyBible.xml", "eng_darby", "Darby 1890"),
    (r"EnglishYLTBible.xml", "eng_ylt", "Young's Literal Translation 1898"),
    (r"EnglishTyndale1537Bible.xml", "eng_tyndale", "Tyndale 1537 (partial: pre-Canonical era)"),
]


def missing_files():
    """IMPORTS entries whose XML is not present on disk.

    Checked from main() (never at import time) so the parsers in this module
    can be unit-tested on a machine without the full Bibles/ tree.
    """
    return [rel for rel, _, _ in IMPORTS
            if not os.path.exists(os.path.join(BASE, rel))]


def _tag(el):
    return el.tag.rsplit("}", 1)[-1].lower()


def _attr(el, *names):
    for n in names:
        if n in el.attrib:
            return el.attrib[n]
    for k, v in el.attrib.items():
        if k.rsplit("}", 1)[-1].lower() in names:
            return v
    return None


def _int(val):
    """None-safe integer coercion for XML attributes (skips junk rows)."""
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def parse_holy_xml_format(root):
    for book in root.iter():
        if _tag(book) != "book":
            continue
        bnum = _int(_attr(book, "number"))
        if not bnum or not 1 <= bnum <= len(BOOK_NAMES):
            continue
        for ch in book:
            if _tag(ch) != "chapter":
                continue
            cnum = _int(_attr(ch, "number"))
            if not cnum:
                continue
            for v in ch:
                if _tag(v) != "verse":
                    continue
                vnum = _int(_attr(v, "number"))
                if not vnum:
                    continue
                # itertext(): capture text inside inline markup (e.g. <STYLE>)
                yield (BOOK_NAMES[bnum - 1], cnum, vnum, "".join(v.itertext()))


def parse_zefania(root):
    for book in root.iter():
        if _tag(book) != "biblebook":
            continue
        bnum = _int(_attr(book, "bnumber", "number"))
        bname = _attr(book, "bname", "name")
        # Prefer the canonical English name derived from bnumber: Zefania
        # modules often carry a localised bname (e.g. ISV ships "Matthäus"),
        # which would pollute the shared book_normalized key. Local-language
        # book names are matched instead via importer.BOOK_LOCAL_TO_ENGLISH
        # and server.BOOK_ALIASES.
        if bnum and 1 <= bnum <= len(BOOK_NAMES):
            bname = BOOK_NAMES[bnum - 1]
        elif not bname:
            continue
        for ch in book:
            if _tag(ch) != "chapter":
                continue
            cnum = _int(_attr(ch, "cnumber", "number"))
            if not cnum:
                continue
            for v in ch:
                if _tag(v) not in ("verse", "vers"):
                    continue
                vnum = _int(_attr(v, "vnumber", "number"))
                if not vnum:
                    continue
                # itertext(): keep text inside inline markup (e.g. <STYLE>)
                yield bname, cnum, vnum, "".join(v.itertext())


def parse_opensong(root):
    for b in root.iter():
        if _tag(b) != "b":
            continue
        bname = _attr(b, "n", "name")
        if not bname:
            continue
        for c in b:
            if _tag(c) != "c":
                continue
            cnum = _int(_attr(c, "n", "number"))
            if not cnum:
                continue
            for v in c:
                if _tag(v) != "v":
                    continue
                vnum = _int(_attr(v, "n", "number"))
                if not vnum:
                    continue
                yield bname, cnum, vnum, "".join(v.itertext())


def detect_parser(root):
    for el in root.iter():
        t = _tag(el)
        if t == "bible":
            return parse_holy_xml_format
        if t == "xmlbible":
            return parse_zefania
        if t == "b":
            return parse_opensong
    return None


def sha_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_manifest():
    if os.path.exists(MANIFEST):
        with open(MANIFEST, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_manifest(m):
    os.makedirs("Bibles", exist_ok=True)
    with open(MANIFEST, "w", encoding="utf-8") as f:
        json.dump(m, f, indent=2)


def import_file(rel, code):
    """Import one XML file. Returns True on success (or skip), False on error.

    A failure in one file must never abort the run: subsequent translations
    still get their turn and the manifest only records real successes (so a
    file that parsed 0 verses - a schema variant - is retried next run).
    """
    path = os.path.join(BASE, rel)
    try:
        digest = sha_file(path)
        manifest = load_manifest()
        if manifest.get(rel) == digest:
            print(f"  SKIP (already imported): {rel}")
            return True
        tree = ET.parse(path)
        parser = detect_parser(tree.getroot())
        if parser is None:
            print(f"  ERROR: unknown schema in {rel}")
            return False
        rows = []
        for bname, cnum, vnum, text in parser(tree.getroot()):
            bname = " ".join(bname.split())
            if not bname:
                continue
            norm = BOOK_LOCAL_TO_ENGLISH.get(bname.lower(), bname.lower())
            rows.append((code, bname, norm, cnum, vnum, " ".join(text.split())))
        if not rows:
            print(f"  ERROR: parsed 0 verses from {rel} (unrecognised schema "
                  f"variant) - not recorded, will retry on the next run")
            return False
        init_database()
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("DELETE FROM verses WHERE translation_code=?", (code,))
        cur.executemany(
            "INSERT INTO verses (translation_code, book_name, book_normalized, chapter, verse, text)"
            " VALUES (?, ?, ?, ?, ?, ?)", rows)
        conn.commit()
        conn.close()
        manifest[rel] = digest
        save_manifest(manifest)
        print(f"  imported {len(rows):6d} verses -> code '{code}' from {rel}")
        return True
    except Exception as exc:  # noqa: BLE001 - keep going through the list
        print(f"  ERROR importing {rel}: {type(exc).__name__}: {exc}")
        return False


def report_books():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    for code in sorted({c for _, c, _ in IMPORTS}):
        cur.execute(
            "SELECT book_name, book_normalized, COUNT(*) FROM verses"
            " WHERE translation_code=? GROUP BY book_name, book_normalized"
            " ORDER BY MIN(rowid)", (code,))
        rows = cur.fetchall()
        print(f"\n--- Books in '{code}' ({len(rows)}) ---")
        for name, norm, n in rows:
            flag = "" if norm == name.lower() else f"  -> {norm}"
            print(f"  {name}{flag}  ({n} verses)")
    conn.close()


if __name__ == "__main__":
    missing = missing_files()
    if missing:
        print("MISSING FILES:")
        for rel in missing:
            print("  -", rel)
        print("Fix the paths or trim IMPORTS, then re-run.")
        sys.exit(1)

    failed = []
    for rel, code, label in IMPORTS:
        print(f"[{label}] {code}:")
        if not import_file(rel, code):
            failed.append(rel)
    report_books()
    if failed:
        print(f"\n{len(failed)} file(s) NOT imported:")
        for rel in failed:
            print("  -", rel)
    else:
        print("\nAll files imported successfully.")
    print("\nDONE. Next: review the book lists above, then fill BOOK_LOCAL_TO_ENGLISH")
    print("(importer.py) and BOOK_ALIASES (server.py) with the bem/nya names.")


