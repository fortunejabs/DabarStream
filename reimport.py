"""
DabarStream - re-import one translation from its source module.

Clears the existing rows for a translation and re-imports it, so every
book_normalized value is rewritten using the CURRENT importer.py alias map.
Use this after any change to BOOK_LOCAL_TO_ENGLISH, otherwise previously
imported rows keep their old keys and become unreachable.

Usage (on the VPS, from /opt/dabarstream):
    sudo python3 reimport.py bem     # Icibemba
    sudo python3 reimport.py nya     # Chinyanja / Chewa
    sudo python3 reimport.py ton     # Chitonga
"""

import os
import sqlite3
import sys

# Translation code -> source module filename (see importer.py __main__).
SOURCES = {
    "bem": "bemba_baibele.xml",
    "nya": "buku_lopatulika.xml",
    "ton": "tonga_baibele.xml",
}

DB = "bible.db"


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] not in SOURCES:
        print("usage: python3 reimport.py " + "|".join(SOURCES))
        return 1

    code = sys.argv[1]
    src = SOURCES[code]

    if not os.path.isfile(DB):
        print(f"ERROR: {DB} not found in {os.getcwd()}")
        return 1
    if not os.path.isfile(src):
        print(f"ERROR: source module not found: {src}")
        print("Copy it next to this script, then retry. Expected names:")
        for c, f in SOURCES.items():
            print(f"  {c}: {f}")
        return 1

    conn = sqlite3.connect(DB)
    before = conn.execute(
        "SELECT COUNT(*) FROM verses WHERE translation_code=?", (code,)
    ).fetchone()[0]
    conn.execute("DELETE FROM verses WHERE translation_code=?", (code,))
    conn.commit()
    conn.close()
    print(f"cleared {before:,} existing '{code}' rows")

    from importer import import_xml_translation

    import_xml_translation(src, translation_code=code)

    conn = sqlite3.connect(DB)
    after = conn.execute(
        "SELECT COUNT(*) FROM verses WHERE translation_code=?", (code,)
    ).fetchone()[0]
    keys = conn.execute(
        "SELECT COUNT(DISTINCT book_normalized) FROM verses WHERE translation_code=?",
        (code,),
    ).fetchone()[0]
    conn.close()
    print(f"re-imported {after:,} '{code}' rows across {keys} book keys from {src}")
    print()
    print("Now audit the result:  python3 check_books.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
