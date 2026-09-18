# Bibles/ — translation & dictionary source files

Place Bible translation modules and study dictionaries here **before** running
`importer.py`. Nothing in this folder is committed to git (see `.gitignore`) —
licensing for most translations (even free ones) forbids redistribution, and
the private repo is no exception. Files here are source material only.

## What goes here

| Extension | Format | Typical source | Import function |
|---|---|---|---|
| `.xml` | Zefania XML / OpenSong XML | SourceForge Zefania archive, OpenSong downloads, GitHub preservation repos | `import_xml_translation()` |
| `.json` | FreeShow-style nested JSON (`{"Book": {"3": {"16": "text"}}}`) | FreeShow export | `import_freeshow_json()` |
| `.csv` / delimited `.txt` | EasyWorship CSV (`Book,Chapter,Verse,Text`) or BibleShow pipes (`Book|Ch|Vs|Text`) | EasyWorship / BibleShow export | `import_easyworship_csv()` / `import_bibleshow_csv()` |
| `.xmm` / `.bib` | e-Sword and other ministry-format modules | Your own library | **Not supported directly** — export to XML/JSON/CSV first (e.g. e-Sword → "Export to…", or convert with third-party tools), then import the exported file |

## Bulk import (`import_bibles.py`)

If you keep a whole library (like the `Holy-Bible-XML-Format-master` collection
used during development), skip the per-file calls and run:

```powershell
python import_bibles.py
```

It walks the curated list in `import_bibles.py:IMPORTS`, auto-detects the schema,
and skips anything already imported (manifest: `Bibles/_import_manifest.json`).
A file that errors, or that parses 0 verses, is reported and left out of the
manifest so the next run retries it — one odd module never aborts the batch.

Supported schemas:

| Schema | Shape |
|---|---|
| Holy-Bible-XML-Format | `<bible><book number=><chapter number=><verse number=>` |
| Zefania | `<XMLBIBLE><BIBLEBOOK bnumber=><CHAPTER cnumber=><VERS vnumber=>` |
| OpenSong | `<song><b n=><c n=><v n=>` |

**Important for African-language modules:** most of them identify books by
**number only** (the `Zambiantranslationbibles` folder here included), so the
imported rows carry the canonical English book name. Spoken local-language
references therefore resolve through `BOOK_LOCAL_TO_ENGLISH` (`importer.py`) and
`BOOK_ALIASES` (`server.py`) — extend those maps with the local book names for
each language (e.g. Chewa `Yohane`/`Machitidwe`, Bemba `Yohane`/`Imilimo`).

## Naming convention

Use `<language>_<translation>.<ext>` so the mapping to `translation_code` is
obvious, e.g.:

- `english_kjv.xml` → `translation_code="eng"`
- `bemba_baibele.xml` → `translation_code="bem"`
- `nyanja_buku.json` → `translation_code="nya"`
- `tonga_baibele.xml` → `translation_code="ton"`

## How to import

1. Drop the file here.
2. Open `importer.py`, scroll to the bottom, and add/uncomment a call:

   ```python
   if __name__ == "__main__":
       import_xml_translation("Bibles/bemba_baibele.xml", translation_code="bem")
       # import_freeshow_json("Bibles/english_kjv.json", translation_code="eng")
   ```

3. Run `python importer.py` from the project root — it writes everything into
   the indexed `bible.db` (multiple translations coexist side-by-side).
4. Upload `bible.db` to the VPS (`/opt/dabarstream/`) — the server reads it at
   query time; the XML/JSON sources stay on your PC.

## Checklist per module

- [ ] Correct book names for the language (add missing ones to
      `BOOK_LOCAL_TO_ENGLISH` in `importer.py` and `BOOK_ALIASES` in
      `server.py` so spoken references resolve)
- [ ] License permits **personal/church use** (redistribution usually not
      allowed — keep files out of git and off public shares)
- [ ] Import ran without skipped books; spot-check a known verse
