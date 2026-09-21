"""
DabarStream - database book-key audit.

Answers one question: are the `book_normalized` values stored in bible.db the
SAME strings the server looks up at query time?

A verse is reachable only when resolve_book(stored_key) == stored_key. If
importer.py and server.py ever disagreed on an alias, rows were written under a
key resolve_book() never produces, and those verses are silently unservable -
no error, just nothing on the overlay.

Usage (on the VPS, from /opt/dabarstream):
    sudo python3 check_books.py
    sudo python3 check_books.py /path/to/bible.db
"""

import sqlite3
import sys

DB = sys.argv[1] if len(sys.argv) > 1 else "bible.db"

try:
    import server
    resolve = server.resolve_book
except Exception as exc:  # noqa: BLE001 - keep the audit usable without Flask
    print(f"WARNING: could not import server ({exc}); key comparison disabled")
    resolve = None

conn = sqlite3.connect(DB)
cur = conn.cursor()

cur.execute("SELECT COUNT(*), COUNT(DISTINCT translation_code) FROM verses")
verses, langs = cur.fetchone()
print(f"database     : {DB}")
print(f"verses       : {verses:,}")
print(f"translations : {langs}")
print()

cur.execute(
    "SELECT translation_code, COUNT(DISTINCT book_normalized) "
    "FROM verses GROUP BY 1 ORDER BY 1"
)
print("books per translation:")
for code, n in cur.fetchall():
    print(f"  {str(code):<8} {n:>3} distinct book keys")
print()

LOCAL = ("bem", "nya", "ton")
cur.execute(
    "SELECT translation_code, book_normalized, COUNT(*) FROM verses "
    "WHERE translation_code IN (?,?,?) GROUP BY 1,2 ORDER BY 1,2",
    LOCAL,
)

stale = []
print("local-language book keys  (reachable iff resolve_book(key) == key):")
for code, book, n in cur.fetchall():
    if resolve is None:
        flag = "?"
    else:
        looked_up = resolve(book)
        if looked_up != book:
            flag = "STALE"
            stale.append((code, book, looked_up, n))
        else:
            flag = "ok"
    print(f"  [{flag:>5}] {code} | {book:<24} {n:>7} verses")

print()
if resolve is None:
    print("Install Flask in this interpreter to enable the reachability check.")
elif stale:
    print("STALE ROWS FOUND - these verses can never be served:")
    seen = set()
    for code, stored, looked_up, n in stale:
        print(f"  {code}: stored '{stored}' but the server looks up '{looked_up}' ({n} verses)")
        seen.add(code)
    print()
    print("Fix by re-importing the affected translation(s):")
    for code in sorted(seen):
        print(f"    sudo python3 reimport.py {code}")
else:
    print("OK - every local-language key equals what the server looks up.")

print()
print("UI translations vs database:")
try:
    offered = list(server.TRANSLATION_LABELS.keys())
except Exception:  # noqa: BLE001
    offered = ["eng", "bem", "nya", "ton"]
for code in offered:
    row = cur.execute(
        "SELECT COUNT(*) FROM verses WHERE translation_code=?", (code,)
    ).fetchone()
    n = row[0] if row else 0
    if n == 0:
        print(f"  {code:<5} MISSING - the UI offers it but no rows exist.")
        print("        Selecting it silently serves ENGLISH text under a "
              f"'{code}' label via the language fallback.")
    else:
        print(f"  {code:<5} {n:,} verses")

conn.close()
