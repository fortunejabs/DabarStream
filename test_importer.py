import csv
import json
import os
import sqlite3

import pytest

from importer import (
    DB_PATH,
    canonical_book_key,
    import_bibleshow_csv,
    import_easyworship_csv,
    import_freeshow_json,
    import_xml_translation,
    init_database,
)


@pytest.fixture(autouse=True)
def setup_and_teardown(tmp_path, monkeypatch):
    """Keep default relative database paths inside a disposable test directory.

    Never delete the application's bible.db: function defaults capture that
    relative filename at import time, so changing cwd isolates those defaults
    as well as direct sqlite3 connections in this module.
    """
    monkeypatch.chdir(tmp_path)


def test_default_database_is_isolated(tmp_path):
    """Default-path imports must create their database only in the test folder."""
    from pathlib import Path

    assert Path.cwd() == tmp_path
    assert not Path(DB_PATH).exists()
    init_database()
    assert Path(DB_PATH).resolve() == tmp_path / "bible.db"
    assert (tmp_path / "bible.db").is_file()


def test_database_initialization():
    """Validates that tables and performance indices are safely built."""
    init_database()
    assert os.path.exists(DB_PATH), "Database file was not created."

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("PRAGMA table_info(verses);")
    columns = [col[1] for col in cursor.fetchall()]
    for col in ("book_name", "book_normalized", "chapter", "verse", "text"):
        assert col in columns

    cursor.execute("PRAGMA index_list(verses);")
    indexes = [idx[1] for idx in cursor.fetchall()]
    assert "idx_book_chap_verse" in indexes
    conn.close()


def test_freeshow_json_validation(tmp_path):
    """Mocks and validates structural JSON parameters used by FreeShow."""
    test_file = tmp_path / "mock_freeshow.json"
    mock_data = {"John": {"3": {"16": "For God so loved the world..."}}}
    test_file.write_text(json.dumps(mock_data), encoding="utf-8")

    import_freeshow_json(str(test_file))

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT book_name, book_normalized, chapter, verse, text FROM verses")
    row = cursor.fetchone()
    conn.close()

    assert row is not None
    assert row[0] == "John"
    assert row[1] == "john"
    assert row[2] == 3
    assert row[3] == 16
    assert "loved the world" in row[4]


def test_bibleshow_csv_validation(tmp_path):
    """Validates pipe-delimited BibleShow structural text data."""
    test_file = tmp_path / "mock_bibleshow.txt"
    test_file.write_text("Genesis|1|1|In the beginning God created...\n", encoding="utf-8")

    import_bibleshow_csv(str(test_file))

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT book_name, chapter, verse, text FROM verses WHERE book_normalized='genesis'"
    )
    row = cursor.fetchone()
    conn.close()

    assert row is not None
    assert row[0] == "Genesis"
    assert row[1] == 1
    assert row[2] == 1


def test_easyworship_csv_validation(tmp_path):
    """Mocks and validates flat standard matrix schemas from EasyWorship headers."""
    test_file = tmp_path / "mock_easyworship.csv"
    with open(test_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Book", "Chapter", "Verse", "Text"])
        writer.writerow(["Genesis", "1", "1", "In the beginning God created..."])

    import_easyworship_csv(str(test_file))

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT book_normalized, chapter, verse FROM verses WHERE book_normalized='genesis'"
    )
    row = cursor.fetchone()
    conn.close()

    assert row is not None
    assert row[1] == 1
    assert row[2] == 1


def test_server_resolver_aliases():
    """Validates the fuzzy book alias resolution layer."""
    from server import resolve_book

    assert resolve_book("Jn") == "john"
    assert resolve_book("First John") == "1 john"
    assert resolve_book("1st. Corinthians") == "1 corinthians"
    assert resolve_book("Genesis") == "genesis"


def test_validate_verse_payload_accepts_digit_form():
    from server import validate_verse_payload

    assert validate_verse_payload(
        {"book": "  John ", "chapter": "3", "verse": 16, "lang": "eng"}
    ) == {"book": "John", "chapter": 3, "verse": 16, "lang": "eng"}


def test_validate_verse_payload_rejects_malformed():
    from server import validate_verse_payload

    assert validate_verse_payload(None) is None
    assert validate_verse_payload({"chapter": 3, "verse": 16}) is None
    assert validate_verse_payload({"book": "John", "chapter": "x", "verse": 16}) is None
    assert validate_verse_payload({"book": "John", "chapter": 3, "verse": 0}) is None
    assert validate_verse_payload({"book": "John", "chapter": 151, "verse": 1}) is None
    assert validate_verse_payload({"book": "x" * 81, "chapter": 3, "verse": 16}) is None


def test_is_authorized_matches_configured_key(monkeypatch):
    import server

    monkeypatch.setenv(server.ENV_KEY_NAME, "secret123")
    assert server.is_authorized({"key": "secret123"}) is True
    assert server.is_authorized({"key": "wrong"}) is False
    assert server.is_authorized({}) is False


def test_is_authorized_open_when_no_key_configured(monkeypatch):
    import server

    monkeypatch.delenv(server.ENV_KEY_NAME, raising=False)
    assert server.is_authorized({}) is True


def test_server_resolver_query(tmp_path):
    """Validates end-to-end resolver + database lookup."""
    test_file = tmp_path / "mock_freeshow.json"
    mock_data = {"John": {"3": {"16": "For God so loved the world..."}}}
    test_file.write_text(json.dumps(mock_data), encoding="utf-8")
    import_freeshow_json(str(test_file))

    from server import resolve_and_query_bible

    result = resolve_and_query_bible("Jn", 3, 16, db_path=DB_PATH)
    assert result is not None
    assert result[0] == "John"
    assert "loved the world" in result[3]


def test_canonical_book_key_multilingual():
    """Validates local-language book names map to canonical English keys."""
    assert canonical_book_key("Yohane") == "john"
    assert canonical_book_key("Chibandakazi") == "genesis"
    assert canonical_book_key("Ututendelo") == "genesis"
    assert canonical_book_key("Machingonzi") == "genesis"
    assert canonical_book_key("John") == "john"


def test_multilingual_translation_isolation(tmp_path):
    """Validates side-by-side storage of two translations in one database."""
    # English via FreeShow JSON
    eng_file = tmp_path / "eng.json"
    eng_file.write_text(
        json.dumps({"John": {"3": {"16": "For God so loved the world..."}}}), encoding="utf-8"
    )
    import_freeshow_json(str(eng_file), translation_code="eng")

    # Nyanja via OpenSong XML
    nya_file = tmp_path / "nya.xml"
    nya_file.write_text(
        '<XMLBIBLE><BIBLEBOOK bname="Yohane"><CHAPTER cnumber="3">'
        '<VERSE vnumber="16">Pakuti Mulungu anachikonda dziko lapansi...</VERSE>'
        "</CHAPTER></BIBLEBOOK></XMLBIBLE>",
        encoding="utf-8",
    )
    import_xml_translation(str(nya_file), translation_code="nya")

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT book_name, text FROM verses WHERE translation_code='nya' "
        "AND book_normalized='john' AND chapter=3 AND verse=16"
    )
    nya_row = cursor.fetchone()
    cursor.execute("SELECT COUNT(*) FROM verses")
    total = cursor.fetchone()[0]
    conn.close()

    assert nya_row is not None
    assert nya_row[0] == "Yohane"          # local display name preserved
    assert "Mulungu" in nya_row[1]
    assert total == 2                       # both languages coexist


def test_server_language_fallback(tmp_path):
    """When the requested language lacks the verse, English text is served."""
    eng_file = tmp_path / "eng.json"
    eng_file.write_text(
        json.dumps({"John": {"3": {"16": "For God so loved the world..."}}}), encoding="utf-8"
    )
    import_freeshow_json(str(eng_file), translation_code="eng")

    from server import resolve_and_query_bible

    # 'bem' has no data yet -> must fall back to the English row
    result = resolve_and_query_bible("Yohane", 3, 16, translation_code="bem", db_path=DB_PATH)
    assert result is not None
    assert "loved the world" in result[3]

    # Requested language with data wins over fallback
    result_eng = resolve_and_query_bible("Jn", 3, 16, translation_code="eng", db_path=DB_PATH)
    assert result_eng is not None
    assert result_eng[0] == "John"


# ---------------------------------------------------------------------------
# import_bibles.py bulk-loader parsers (regression tests)
#
# Real-world XML quirks these lock down:
#   * Zefania <BIBLEBOOK bnumber="1"> with NO bname (King James 2000)
#   * Zefania bname carrying a LOCALISED name (ISV ships German "Matthäus")
#   * <VERS> instead of <VERSE> (Zefania standard)
#   * inline <STYLE css=...> markup inside a verse (KJ2000 red-letter text)
# ---------------------------------------------------------------------------
def _parse(xml, parser_name):
    """Run one import_bibles parser over an XML string and return its rows."""
    import xml.etree.ElementTree as ET

    import import_bibles

    root = ET.fromstring(xml)
    parser = getattr(import_bibles, parser_name)
    return list(parser(root))


def test_import_bibles_is_safe_to_import():
    """Importing the bulk loader must not sys.exit() when Bibles/ is absent."""
    import import_bibles

    # No database/file side effects at import time; the list is still defined.
    assert isinstance(import_bibles.IMPORTS, list)
    assert len(import_bibles.IMPORTS) > 0
    assert callable(import_bibles.missing_files)


def test_zefania_book_without_bname_falls_back_to_canonical():
    """KJ2000-style files omit bname: book number drives the canonical name."""
    xml = (
        '<XMLBIBLE><BIBLEBOOK bnumber="1"><CHAPTER cnumber="1">'
        '<VERS vnumber="1">In the beginning God created the heaven and the earth.</VERS>'
        "</CHAPTER></BIBLEBOOK></XMLBIBLE>"
    )
    rows = _parse(xml, "parse_zefania")

    assert len(rows) == 1
    book, chapter, verse, text = rows[0]
    assert book == "Genesis"          # was a crash: None.split()
    assert (chapter, verse) == (1, 1)
    assert "In the beginning" in text


def test_zefania_localised_bname_is_overridden_by_canonical_name():
    """ISV ships German book names; the shared key must stay English."""
    xml = (
        '<XMLBIBLE><BIBLEBOOK bnumber="40" bname="Matth\u00e4us" bsname="Mt">'
        '<CHAPTER cnumber="1">'
        '<VERS vnumber="1">This is a record of the birth of Jesus Christ.</VERS>'
        "</CHAPTER></BIBLEBOOK></XMLBIBLE>"
    )
    rows = _parse(xml, "parse_zefania")

    assert len(rows) == 1
    assert rows[0][0] == "Matthew"     # not "Matthäus"


def test_zefania_keeps_inline_markup_text():
    """Red-letter <STYLE> runs must not truncate the verse to 'And God said, '."""
    xml = (
        '<XMLBIBLE><BIBLEBOOK bnumber="1"><CHAPTER cnumber="1">'
        '<VERS vnumber="3">And God said, '
        '<STYLE css="color:#FF0000">Let there be light:</STYLE> and there was light.'
        "</VERS></CHAPTER></BIBLEBOOK></XMLBIBLE>"
    )
    rows = _parse(xml, "parse_zefania")

    text = rows[0][3]
    assert "Let there be light:" in text
    assert "and there was light." in text


def test_zefania_skips_junk_rows_instead_of_raising():
    """Non-numeric or missing numbers must be skipped, not crash the import."""
    xml = (
        '<XMLBIBLE><BIBLEBOOK bnumber="1"><CHAPTER cnumber="1">'
        '<VERS vnumber="1">Good row.</VERS>'
        "<VERS>No number here.</VERS>"
        "</CHAPTER>"
        '<CHAPTER cnumber="x"><VERS vnumber="1">Bad chapter.</VERS></CHAPTER>'
        "</BIBLEBOOK></XMLBIBLE>"
    )
    rows = _parse(xml, "parse_zefania")

    assert len(rows) == 1
    assert rows[0][3] == "Good row."


def test_holy_xml_format_uses_canonical_names_and_itertext():
    """The Zambian collection: numbers only, plus inline markup support."""
    xml = (
        '<bible><testament name="Old">'
        '<book number="19"><chapter number="23">'
        '<verse number="1">The LORD is my shepherd; I shall not want.</verse>'
        "</chapter></book></testament></bible>"
    )
    rows = _parse(xml, "parse_holy_xml_format")

    assert len(rows) == 1
    assert rows[0] == ("Psalms", 23, 1, "The LORD is my shepherd; I shall not want.")


def test_holy_xml_format_rejects_out_of_range_book_number():
    xml = (
        '<bible><book number="99"><chapter number="1">'
        '<verse number="1">Should be ignored.</verse>'
        "</chapter></book></bible>"
    )
    assert _parse(xml, "parse_holy_xml_format") == []


def test_opensong_parser_reads_nested_verse_text():
    xml = (
        '<song><b n="John"><c n="3">'
        '<v n="16">For God so loved <i>the world</i>, that he gave...</v>'
        "</c></b></song>"
    )
    rows = _parse(xml, "parse_opensong")

    assert len(rows) == 1
    book, chapter, verse, text = rows[0]
    assert (book, chapter, verse) == ("John", 3, 16)
    assert "For God so loved the world, that he gave..." in text


def test_detect_parser_recognises_all_three_schemas():
    import xml.etree.ElementTree as ET

    import import_bibles

    holy = ET.fromstring('<bible><book number="1"/></bible>')
    zef = ET.fromstring('<XMLBIBLE><BIBLEBOOK bnumber="1"/></XMLBIBLE>')
    osg = ET.fromstring('<song><b n="John"/></song>')
    other = ET.fromstring("<root><thing/></root>")

    assert import_bibles.detect_parser(holy) is import_bibles.parse_holy_xml_format
    assert import_bibles.detect_parser(zef) is import_bibles.parse_zefania
    assert import_bibles.detect_parser(osg) is import_bibles.parse_opensong
    assert import_bibles.detect_parser(other) is None


def test_canonical_book_name_maps_module_spellings():
    """Modules that spell a book differently must still land on the shared key."""
    import import_bibles

    assert import_bibles.canonical_book_name("Psalm") == "Psalms"
    assert import_bibles.canonical_book_name("  psalm  ") == "Psalms"
    assert import_bibles.canonical_book_name("Song of Songs") == "Song of Solomon"
    assert import_bibles.canonical_book_name("Canticles") == "Song of Solomon"
    assert import_bibles.canonical_book_name("Revelations") == "Revelation"
    # Canonical names pass through untouched
    assert import_bibles.canonical_book_name("John") == "John"
    assert import_bibles.canonical_book_name("1 Corinthians") == "1 Corinthians"


def test_parse_args_force_and_reset():
    """CLI flags drive selective re-import after an importer fix."""
    import import_bibles

    force, reset, verbose = import_bibles.parse_args([])
    assert force == set() and reset is False and verbose is False

    force, reset, verbose = import_bibles.parse_args(["--force", "eng_msg", "ENG_NIVUK"])
    assert force == {"eng_msg", "eng_nivuk"}      # normalised to lowercase
    assert reset is False and verbose is False

    force, reset, verbose = import_bibles.parse_args(["--force"])
    assert force == set() and reset is False      # warns, forces nothing

    force, reset, verbose = import_bibles.parse_args(["--reset"])
    assert force == set() and reset is True

    # --force consumes codes up to the next flag, so --reset still registers
    force, reset, verbose = import_bibles.parse_args(["--force", "eng_bbe", "--reset"])
    assert force == {"eng_bbe"} and reset is True

    # --report opts into the slow full per-book listing
    force, reset, verbose = import_bibles.parse_args(["--report"])
    assert verbose is True and force == set() and reset is False

    # flags combine in any order
    force, reset, verbose = import_bibles.parse_args(["--report", "--force", "bem"])
    assert force == {"bem"} and verbose is True


def test_server_resolves_singular_psalm_alias():
    """Whisper often returns 'Psalm 23'; the alias map must cover it."""
    from server import BOOK_ALIASES

    assert BOOK_ALIASES["psalm"] == "psalms"
    assert BOOK_ALIASES["ps"] == "psalms"
    assert BOOK_ALIASES["song of songs"] == "song of solomon"


def test_server_finds_psalms_when_spoken_as_singular_psalm(tmp_path):
    """A 'Psalm' variant must hit the stored 'psalms' row instead of falling back."""
    seed = tmp_path / "eng.json"
    seed.write_text(
        json.dumps({"Psalms": {"23": {"1": "The LORD is my shepherd; I shall not want."}}}),
        encoding="utf-8",
    )
    import_freeshow_json(str(seed), translation_code="eng")

    from server import resolve_and_query_bible

    result = resolve_and_query_bible("Psalm", 23, 1, translation_code="eng", db_path=DB_PATH)
    assert result is not None
    assert result[0] == "Psalms"
    assert "shepherd" in result[3]


# ---------------------------------------------------------------------------
# Bemba / Chewa spoken-alias seed (UNVERIFIED - see PROGRESS.md)
# ---------------------------------------------------------------------------
def test_chewa_bemba_spoken_aliases_resolve():
    """Starter Bemba/Chewa aliases must resolve to the canonical English key.

    UNVERIFIED: transcribed from a language reference, not yet confirmed by a
    Bemba/Chewa speaker or against a printed local Bible. Correct any that are
    wrong rather than deleting the test.
    """
    from server import resolve_book

    chewa = {
        "Yohane": "john", "Mateyu": "matthew", "Marko": "mark",
        "Lukasi": "luke", "Machitidwe": "acts", "Aroma": "romans",
        "Masalmo": "psalms", "Miyambo": "proverbs", "Mlaliki": "ecclesiastes",
        "Yesaya": "isaiah", "Yeremiya": "jeremiah", "Ezekieli": "ezekiel",
        "Danieli": "daniel", "Yona": "jonah", "Malaki": "malachi",
        "Chivumbulutso": "revelation", "Chibandakazi": "genesis",
        "Ukufuma": "exodus", "Eksodo": "exodus", "Owalamula": "judges",
        "Machingonzi": "genesis", "Deuteronomo": "deuteronomy",
        "Levitiko": "leviticus", "Numeri": "numbers",
        "Nyimbo ya Solomoni": "song of solomon", "Yoweli": "joel",
        "Amosi": "amos", "Obadiya": "obadiah", "Mika": "micah",
        "Nahumu": "nahum", "Habakuku": "habakkuk", "Sefaniya": "zephaniah",
        "Hagai": "haggai", "Zekariya": "zechariah", "Yuda": "jude",
        "Akorinto": "corinthians", "Tesalonika": "thessalonians",
        "Timoteo": "timothy",
    }
    for spoken, canon in chewa.items():
        assert resolve_book(spoken) == canon, f"{spoken} -> {canon}"

    bemba = {
        "Ututendelo": "genesis", "Ukufuma": "exodus", "Imilimo": "acts",
        "Matayo": "matthew", "Mako": "mark", "Luka": "luke",
        "Petulo": "peter", "Salimo": "psalms", "Esaya": "isaiah",
        "Abena Roma": "romans", "Danieli": "daniel", "Yoswa": "joshua",
        "Abalamuzi": "judges", "Rute": "ruth", "Samweli": "samuel",
        "Amalango": "leviticus", "Abena Korinto": "corinthians",
        "Abena Galatiya": "galatians", "Abena Efeso": "ephesians",
        "Abena Filipi": "philippians", "Abena Kolosai": "colossians",
        "Abena Tesalonika": "thessalonians", "Abena Heburani": "hebrews",
        "Ukubvumbuluka": "revelation", "Yakobo": "james", "Yuda": "jude",
        "Ezira": "ezra", "Nehemiya": "nehemiah", "Esiteli": "esther",
        "Yobu": "job", "Yeremiya": "jeremiah", "Ezekieli": "ezekiel",
        "Hoseya": "hosea",
    }
    for spoken, canon in bemba.items():
        assert resolve_book(spoken) == canon, f"{spoken} -> {canon}"


def test_numbered_books_resolve_through_local_names():
    """Spoken '1 Mafumu' / '2 Samweli' / '1 Akorinto' must hit numbered keys."""
    from server import resolve_book

    assert resolve_book("1 Mafumu") == "1 kings"
    assert resolve_book("2 Mafumu") == "2 kings"
    assert resolve_book("1 Samweli") == "1 samuel"
    assert resolve_book("2 Samweli") == "2 samuel"
    assert resolve_book("1 Akorinto") == "1 corinthians"
    assert resolve_book("2 Akorinto") == "2 corinthians"
    assert resolve_book("1 Tesalonika") == "1 thessalonians"
    assert resolve_book("2 Tesalonika") == "2 thessalonians"
    assert resolve_book("1 Timoteo") == "1 timothy"
    assert resolve_book("2 Timoteo") == "2 timothy"
    assert resolve_book("1 Petulo") == "1 peter"
    assert resolve_book("2 Petulo") == "2 peter"
    assert resolve_book("1 Yohane") == "1 john"
    assert resolve_book("2 Yohane") == "2 john"
    assert resolve_book("3 Yohane") == "3 john"


def test_server_resolves_bemba_yohane_end_to_end(tmp_path):
    """'Yohane 3:16' spoken in Bemba must return the Bemba text, not English."""
    seed = tmp_path / "bem.json"
    seed.write_text(
        json.dumps({"Yohane": {"3": {"16": "Pakuti Mulungu anali chikonda dziko..."}}}),
        encoding="utf-8",
    )
    import_freeshow_json(str(seed), translation_code="bem")

    from server import resolve_and_query_bible

    result = resolve_and_query_bible("Yohane", 3, 16, translation_code="bem", db_path=DB_PATH)
    assert result is not None
    assert result[0] == "Yohane"
    assert "Mulungu" in result[3]
