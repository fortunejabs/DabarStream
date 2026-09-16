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
