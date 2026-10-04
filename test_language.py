"""
Validates the voice-activated and hotkey language-switching layer.

`client.py` imports pyaudio / faster-whisper / numpy at module load, which are
not installable on this interpreter yet, so light stubs are injected into
sys.modules before the module is imported. The logic under test
(detect_language_command / switch_language / process_audio / normalize_lang)
is the real production code.
"""

import sys
import types

import pytest


# --------------------------------------------------------------------------
# Minimal dependency stubs so `import client` works without audio hardware
# --------------------------------------------------------------------------
class _FakeArray:
    def astype(self, *args, **kwargs):
        return self

    def __truediv__(self, other):
        return self


class _FakeSegment:
    def __init__(self, text):
        self.text = text


class _FakeModel:
    """Stand-in for faster_whisper.WhisperModel returning canned segments."""

    def __init__(self, texts):
        self.texts = texts

    def transcribe(self, audio, beam_size=1, vad_filter=True):
        return [_FakeSegment(t) for t in self.texts], None


class _RecordingSocket:
    """Captures emitted events instead of sending them to a VPS."""

    def __init__(self):
        self.events = []

    def emit(self, event, payload=None):
        self.events.append((event, payload))

    def connect(self, url):
        return None


@pytest.fixture(scope="module", autouse=True)
def audio_stubs():
    fake_numpy = types.ModuleType("numpy")
    fake_numpy.int16 = "int16"
    fake_numpy.float32 = "float32"
    fake_numpy.frombuffer = lambda data, dtype=None: _FakeArray()

    fake_pyaudio = types.ModuleType("pyaudio")
    fake_pyaudio.paInt16 = 8
    fake_pyaudio.PyAudio = object

    fake_faster_whisper = types.ModuleType("faster_whisper")
    fake_faster_whisper.WhisperModel = _FakeModel

    stubs = {
        "numpy": fake_numpy,
        "pyaudio": fake_pyaudio,
        "faster_whisper": fake_faster_whisper,
    }
    saved = {name: sys.modules.get(name) for name in stubs}
    sys.modules.update(stubs)
    yield
    for name, original in saved.items():
        if original is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = original


@pytest.fixture
def client(monkeypatch):
    """Imports client.py with a recording socket and clean language state."""
    import client as client_module

    recorder = _RecordingSocket()
    monkeypatch.setattr(client_module, "sio", recorder)
    monkeypatch.setattr(client_module, "ACTIVE_LANG", client_module.DEFAULT_LANG)
    client_module.recorder = recorder
    return client_module


# --------------------------------------------------------------------------
# Voice-activated switching
# --------------------------------------------------------------------------
def test_detect_language_command_english(client):
    assert client.detect_language_command("let us read in English") == "eng"


def test_client_emits_carry_stream_key(client, monkeypatch):
    """Every outbound control emit must include the shared secret."""
    monkeypatch.setattr(client, "STREAM_KEY", "s3cret")
    client.switch_language("bem")
    assert ("set_language", {"lang": "bem", "key": "s3cret"}) in client.recorder.events

    model = _FakeModel(["John 3:16"])
    client.process_audio(b"audio", model)
    verse_events = [p for e, p in client.recorder.events if e == "verse_triggered"]
    assert verse_events and all(p.get("key") == "s3cret" for p in verse_events)


def test_parse_verse_reference_digits(client):
    assert client.parse_verse_reference("John 3:16") == ("John", "3", "16")


def test_parse_verse_reference_digit_form_keeps_numbered_books(client):
    """Whisper's digit form must not drop the leading numeral.

    Regression: the book group originally excluded digits, so the pattern
    matched at "Timothy" in "1 Timothy 3:16" and lost the numbered book
    entirely. Twelve books are affected (1-2 Samuel, Kings, Chronicles,
    Corinthians, Thessalonians, Timothy, Peter and 1-3 John).
    """
    assert client.parse_verse_reference("1 Timothy 3:16") == ("1 Timothy", "3", "16")
    assert client.parse_verse_reference("2 Corinthians 5:17") == ("2 Corinthians", "5", "17")
    assert client.parse_verse_reference("1 John 4:8") == ("1 John", "4", "8")
    assert client.parse_verse_reference("2 Kings 5:14") == ("2 Kings", "5", "14")


def test_parse_verse_reference_spoken_words_keep_numbered_books(client):
    """Spoken ordinals must survive the word path too."""
    book, chapter, verse = client.parse_verse_reference("First John four eight")
    assert book.lower() == "first john"
    assert (chapter, verse) == ("4", "8")


def test_parse_verse_reference_does_not_split_binding_number_words(client):
    """'twenty three' is 23, not 20:3 - a binding word must stay joined.

    Guards the bare-form split: two adjacent number words are treated as
    chapter/verse, but only when the first word does not bind to the next.
    """
    assert client.parse_verse_reference("John chapter twenty three") is None

    # With an explicit verse separator the same words still resolve as 23:1.
    book, chapter, verse = client.parse_verse_reference(
        "John chapter twenty three verse one"
    )
    assert book.lower() == "john"
    assert (chapter, verse) == ("23", "1")


def test_parse_verse_reference_spoken_number_words(client):
    book, chapter, verse = client.parse_verse_reference(
        "John chapter three verse sixteen"
    )
    assert book.lower() == "john"
    assert (chapter, verse) == ("3", "16")


def test_parse_verse_reference_compound_numbers(client):
    book, chapter, verse = client.parse_verse_reference(
        "Psalms twenty three verse one"
    )
    assert book.lower() == "psalms"
    assert (chapter, verse) == ("23", "1")


def test_words_to_number_edge_cases(client):
    assert client.words_to_number("twenty-one") == 21
    assert client.words_to_number("one hundred and fifty") == 150
    assert client.words_to_number("hallelujah") is None
    assert client.words_to_number("") is None


def test_verse_trigger_carries_stream_key(client):
    model = _FakeModel(["John 3:16"])
    client.process_audio(b"audio", model)
    verse_events = [p for e, p in client.recorder.events if e == "verse_triggered"]
    assert len(verse_events) == 1
    assert verse_events[0]["key"] == client.STREAM_KEY


def test_spoken_words_become_verse_trigger(client):
    model = _FakeModel(["Yohane chapter three verse sixteen"])
    client.process_audio(b"audio", model)
    verse_events = [p for e, p in client.recorder.events if e == "verse_triggered"]
    assert len(verse_events) == 1
    assert verse_events[0]["book"].lower() == "yohane"
    assert (verse_events[0]["chapter"], verse_events[0]["verse"]) == ("3", "16")


def test_detect_language_command_bemba(client):
    assert client.detect_language_command("now show Bemba") == "bem"
    assert client.detect_language_command("icibemba please") == "bem"


def test_detect_language_command_nyanja_and_chewa(client):
    assert client.detect_language_command("turn to Chinyanja") == "nya"
    assert client.detect_language_command("let us use chewa") == "nya"


def test_detect_language_command_tonga(client):
    assert client.detect_language_command("switch to Chitonga") == "ton"


def test_detect_language_command_returns_none_for_plain_speech(client):
    assert client.detect_language_command("let us turn to John chapter three") is None
    assert client.detect_language_command("") is None


def test_longest_phrase_wins(client):
    """'in english' must not be shadowed by the shorter 'english' entry."""
    assert client.detect_language_command("read it in english") == "eng"
    assert client.detect_language_command("show nyanja") == "nya"


# --------------------------------------------------------------------------
# Local state + socket notification
# --------------------------------------------------------------------------
def test_switch_language_updates_state_and_emits(client):
    client.switch_language("bem")
    assert client.ACTIVE_LANG == "bem"
    assert ("set_language", {"lang": "bem", "key": client.STREAM_KEY}) in (
        client.recorder.events
    )


def test_process_audio_switches_language_then_tags_following_verses(client):
    """A spoken switch must apply to every verse trigger that follows it."""
    model = _FakeModel(["show bemba", "Yohane chapter 3 verse 16"])
    returned = client.process_audio(b"audio", model)

    assert returned == "bem"
    assert ("set_language", {"lang": "bem", "key": client.STREAM_KEY}) in (
        client.recorder.events
    )

    verse_events = [p for e, p in client.recorder.events if e == "verse_triggered"]
    assert len(verse_events) == 1
    assert verse_events[0]["lang"] == "bem"
    assert verse_events[0]["book"].lower() == "yohane"
    assert verse_events[0]["chapter"] == "3"
    assert verse_events[0]["verse"] == "16"


def test_process_audio_language_command_is_not_treated_as_a_verse(client):
    model = _FakeModel(["show tonga"])
    client.process_audio(b"audio", model)

    assert [e for e, _ in client.recorder.events] == ["set_language"]


def test_process_audio_defaults_to_english_for_plain_verses(client):
    model = _FakeModel(["Genesis chapter 1 verse 1"])
    client.process_audio(b"audio", model)

    verse_events = [p for e, p in client.recorder.events if e == "verse_triggered"]
    assert len(verse_events) == 1
    assert verse_events[0]["lang"] == "eng"

# --------------------------------------------------------------------------
# Server-side validation of incoming language codes
# --------------------------------------------------------------------------
def test_normalize_lang_accepts_known_codes(client):
    from server import normalize_lang

    assert normalize_lang("eng") == "eng"
    assert normalize_lang(" BEM ") == "bem"
    assert normalize_lang("Nya") == "nya"
    assert normalize_lang("ton") == "ton"


def test_normalize_lang_rejects_unknown_codes(client):
    from server import normalize_lang

    assert normalize_lang("klingon") is None
    assert normalize_lang("") is None
    assert normalize_lang(None) is None
    assert normalize_lang("../../etc/passwd") is None


def test_is_authorized_dev_mode_and_key_checks(client, monkeypatch):
    """Unset key = local dev open mode; set key = constant-time check enforced."""
    import server as server_module

    monkeypatch.delenv(server_module.ENV_KEY_NAME, raising=False)
    assert server_module.is_authorized({}) is True
    assert server_module.is_authorized({"key": "anything"}) is True

    monkeypatch.setenv(server_module.ENV_KEY_NAME, "s3cret")
    assert server_module.is_authorized({"key": "s3cret"}) is True
    assert server_module.is_authorized({"key": "wrong"}) is False
    assert server_module.is_authorized({}) is False
    assert server_module.is_authorized(None) is False
    assert server_module.is_authorized("s3cret") is False


def test_validate_verse_payload_accepts_and_normalizes(client):
    from server import validate_verse_payload

    cleaned = validate_verse_payload(
        {"book": "  First   John ", "chapter": " 3 ", "verse": "16", "lang": "BEM"}
    )
    assert cleaned == {"book": "First John", "chapter": 3, "verse": 16, "lang": "bem"}

    # Missing/unknown lang falls back to the server's active translation.
    import server as server_module

    monkeypatch_lang = server_module.CURRENT_LANG
    server_module.CURRENT_LANG = "nya"
    try:
        assert (
            validate_verse_payload({"book": "Jn", "chapter": 3, "verse": 16})["lang"]
            == "nya"
        )
        assert (
            validate_verse_payload(
                {"book": "Jn", "chapter": 3, "verse": 16, "lang": "klingon"}
            )["lang"]
            == "eng"
        )
    finally:
        server_module.CURRENT_LANG = monkeypatch_lang


def test_validate_verse_payload_rejects_malformed(client):
    from server import validate_verse_payload

    assert validate_verse_payload(None) is None
    assert validate_verse_payload("John 3:16") is None
    assert validate_verse_payload({}) is None
    assert validate_verse_payload({"book": "", "chapter": 3, "verse": 16}) is None
    assert validate_verse_payload({"book": 123, "chapter": 3, "verse": 16}) is None
    assert validate_verse_payload({"book": "x" * 81, "chapter": 3, "verse": 16}) is None
    assert validate_verse_payload({"book": "John", "chapter": "three", "verse": 16}) is None
    assert validate_verse_payload({"book": "John", "chapter": 0, "verse": 16}) is None
    assert validate_verse_payload({"book": "John", "chapter": 3, "verse": 177}) is None
    assert validate_verse_payload({"book": "John", "chapter": 151, "verse": 1}) is None


def test_unauthorized_events_are_rejected(client, monkeypatch, tmp_path):
    """Bad-key verse triggers and clears must not touch the DB or broadcast."""
    import server as server_module
    from importer import import_freeshow_json

    db_path = str(tmp_path / "auth.db")
    seed = tmp_path / "eng.json"
    seed.write_text('{"John": {"3": {"16": "For God so loved..."}}}', encoding="utf-8")
    import_freeshow_json(str(seed), translation_code="eng", db_path=db_path)
    monkeypatch.setattr(server_module, "DB_PATH", db_path)
    monkeypatch.setenv(server_module.ENV_KEY_NAME, "s3cret")

    emitted = []
    monkeypatch.setattr(
        server_module, "emit", lambda *a, **k: emitted.append((a, k))
    )

    server_module.handle_verse({"book": "John", "chapter": 3, "verse": 16})
    server_module.handle_verse(
        {"book": "John", "chapter": 3, "verse": 16, "key": "wrong"}
    )
    server_module.handle_clear_overlay({})
    server_module.handle_set_language({"lang": "bem"})
    assert emitted == []

    # Correct key flows through to a broadcast.
    server_module.handle_verse(
        {"book": "John", "chapter": 3, "verse": 16, "key": "s3cret"}
    )
    assert any(a[0] == "update_overlay" for a, _ in emitted)
    emitted.clear()

    server_module.handle_set_language({"lang": "bem", "key": "s3cret"})
    assert any(a[0] == "language_changed" for a, _ in emitted)
    assert server_module.CURRENT_LANG == "bem"
    emitted.clear()

    server_module.handle_clear_overlay({"key": "s3cret"})
    assert any(a[0] == "clear_overlay" for a, _ in emitted)


def test_malformed_verse_payload_is_ignored_without_broadcast(
    client, monkeypatch, tmp_path
):
    import server as server_module
    from importer import import_freeshow_json

    db_path = str(tmp_path / "malformed.db")
    seed = tmp_path / "eng.json"
    seed.write_text('{"John": {"3": {"16": "For God so loved..."}}}', encoding="utf-8")
    import_freeshow_json(str(seed), translation_code="eng", db_path=db_path)
    monkeypatch.setattr(server_module, "DB_PATH", db_path)
    monkeypatch.delenv(server_module.ENV_KEY_NAME, raising=False)

    emitted = []
    monkeypatch.setattr(
        server_module, "emit", lambda *a, **k: emitted.append((a, k))
    )

    server_module.handle_verse({"chapter": 3, "verse": 16})  # no book
    server_module.handle_verse(
        {"book": "John", "chapter": "three", "verse": 16}  # non-numeric
    )
    server_module.handle_verse(
        {"book": "John", "chapter": 999, "verse": 1}  # out of range
    )
    assert emitted == []


def test_translation_labels_cover_hotkey_languages(client):
    from server import TRANSLATION_LABELS

    for code in client.HOTKEY_LANGS.values():
        assert code in TRANSLATION_LABELS


# --------------------------------------------------------------------------
# Server P0: shared-secret auth, payload validation, clear_overlay
# --------------------------------------------------------------------------
def test_auth_open_when_no_key_configured(client, monkeypatch):
    import server as server_module

    monkeypatch.delenv("DABARSTREAM_KEY", raising=False)
    assert server_module.is_authorized({"lang": "bem"}) is True
    assert server_module.is_authorized({}) is True  # open local dev mode


def test_auth_enforced_when_key_configured(client, monkeypatch):
    import server as server_module

    monkeypatch.setenv("DABARSTREAM_KEY", "s3cret")
    assert server_module.is_authorized({"lang": "bem", "key": "s3cret"}) is True
    assert server_module.is_authorized({"lang": "bem", "key": "wrong"}) is False
    assert server_module.is_authorized({"lang": "bem"}) is False
    assert server_module.is_authorized(None) is False
    assert server_module.is_authorized("not-a-dict") is False


def test_validate_verse_payload_accepts_digit_form(client):
    from server import validate_verse_payload

    cleaned = validate_verse_payload(
        {"book": "  John ", "chapter": "3", "verse": 16, "lang": "eng"}
    )
    assert cleaned == {"book": "John", "chapter": 3, "verse": 16, "lang": "eng"}


def test_validate_verse_payload_rejects_invalid_and_defaults_lang(client):
    """Out-of-range numbers are rejected; unknown language falls back to eng.

    Named distinctly from the earlier malformed-payload test: duplicate
    function names in a module cause pytest to collect only the last
    definition, silently dropping coverage.
    """
    from server import validate_verse_payload

    assert validate_verse_payload(None) is None
    assert validate_verse_payload("John 3:16") is None
    assert validate_verse_payload({}) is None
    assert validate_verse_payload({"book": "", "chapter": 3, "verse": 16}) is None
    assert validate_verse_payload({"book": "John", "chapter": "three", "verse": 16}) is None
    assert validate_verse_payload({"book": "John", "chapter": 0, "verse": 16}) is None
    assert validate_verse_payload({"book": "John", "chapter": 3, "verse": 999}) is None
    # Unknown language falls back to eng rather than rejecting the verse
    cleaned = validate_verse_payload(
        {"book": "John", "chapter": "3", "verse": "16", "lang": "klingon"}
    )
    assert cleaned is not None and cleaned["lang"] == "eng"


def test_clear_overlay_handler_emits_broadcast(client, monkeypatch):
    import server as server_module

    monkeypatch.delenv("DABARSTREAM_KEY", raising=False)
    emitted = []
    monkeypatch.setattr(
        server_module, "emit", lambda event, payload, **kw: emitted.append((event, payload))
    )
    server_module.handle_clear_overlay({})
    assert emitted == [("clear_overlay", {})]


def test_clear_overlay_handler_rejects_bad_key(client, monkeypatch):
    import server as server_module

    monkeypatch.setenv("DABARSTREAM_KEY", "s3cret")
    emitted = []
    monkeypatch.setattr(
        server_module, "emit", lambda event, payload, **kw: emitted.append((event, payload))
    )
    server_module.handle_clear_overlay({"key": "wrong"})
    assert emitted == []


def test_server_serves_requested_local_language(client, tmp_path):
    """End-to-end: a 'nya' payload returns the Nyanja text, not English."""
    from importer import import_freeshow_json, import_xml_translation
    from server import resolve_and_query_bible

    db_path = str(tmp_path / "lang.db")
    eng_file = tmp_path / "eng.json"
    eng_file.write_text(
        '{"John": {"3": {"16": "For God so loved the world..."}}}', encoding="utf-8"
    )
    import_freeshow_json(str(eng_file), translation_code="eng", db_path=db_path)

    nya_file = tmp_path / "nya.xml"
    nya_file.write_text(
        '<XMLBIBLE><BIBLEBOOK bname="Yohane"><CHAPTER cnumber="3">'
        '<VERSE vnumber="16">Pakuti Mulungu anachikonda dziko lapansi...</VERSE>'
        "</CHAPTER></BIBLEBOOK></XMLBIBLE>",
        encoding="utf-8",
    )
    import_xml_translation(str(nya_file), translation_code="nya", db_path=db_path)

    # Spoken in Chinyanja (book name 'Yohane') -> resolver must map to 'john'
    nya = resolve_and_query_bible("Yohane", 3, 16, translation_code="nya", db_path=db_path)
    assert nya is not None
    assert nya[0] == "Yohane"
    assert "Mulungu" in nya[3]

    # Same coordinates asking for English -> English text
    eng = resolve_and_query_bible("John", 3, 16, translation_code="eng", db_path=db_path)
    assert eng is not None
    assert "loved the world" in eng[3]


# --------------------------------------------------------------------------
# Server P1: book-alias resolution (resolve_book)
# --------------------------------------------------------------------------
def test_resolve_book_reaches_numbered_local_language_books(client):
    """Plain, abbreviated and numbered aliases all reach database keys."""
    from server import resolve_book

    assert resolve_book("John") == "john"
    assert resolve_book("Jn.") == "john"          # abbreviation, trailing dot
    assert resolve_book("yohane") == "john"       # Nyanja
    assert resolve_book("1 yohane") == "1 john"   # numbered Nyanja
    assert resolve_book("1 timoteo") == "1 timothy"  # numbered Chewa
    assert resolve_book("1 mafumu") == "1 kings"     # numbered Bemba
    assert resolve_book("2 mafumu") == "2 kings"


def test_resolve_book_maps_spoken_ordinals_to_numbered_books(client):
    """A spoken ordinal prefix must land on the numbered alias.

    Regression: only the English ordinal form was aliased ("first john"), so
    "first yohane" fell through to a literal lookup, never matched a row, and
    the overlay silently showed nothing. The prefix is now rewritten to a
    digit and retried against the numbered alias table.
    """
    from server import resolve_book

    assert resolve_book("first yohane") == "1 john"
    assert resolve_book("third yohane") == "3 john"
    assert resolve_book("second mafumu") == "2 kings"
    # The pre-existing explicit English aliases must keep working unchanged.
    assert resolve_book("first john") == "1 john"
    assert resolve_book("1st john") == "1 john"


def test_resolve_book_passes_through_unknown_and_normalizes_whitespace(client):
    """Already-canonical input survives; spacing/case/dots are collapsed."""
    from server import resolve_book

    assert resolve_book("  Song of Solomon  ") == "song of solomon"
    assert resolve_book("1   kings") == "1 kings"
    # Not in the alias table -> cleaned literal, never dropped or None.
    assert resolve_book("Hezekiah") == "hezekiah"


# --------------------------------------------------------------------------
# Importer <-> server alias parity
# --------------------------------------------------------------------------
def test_importer_aliases_resolve_identically_on_the_server(client):
    """Every alias the importer writes must resolve to that same key.

    Regression: importer.py carried only three Bemba entries, so a Bemba XML
    with bname="Mafumu" was stored as book_normalized="mafumu" while the
    server resolved a spoken "mafumu" to "kings". Every such verse missed and
    the overlay stayed blank with no error.

    The invariant enforced here: for each K -> V in BOOK_LOCAL_TO_ENGLISH, a
    speaker saying K must resolve to V, because V is what was written to the
    database. The two maps may differ in coverage, but never in value.
    """
    from importer import BOOK_LOCAL_TO_ENGLISH
    from server import resolve_book

    drift = {
        alias: (expected, resolve_book(alias))
        for alias, expected in BOOK_LOCAL_TO_ENGLISH.items()
        if resolve_book(alias) != expected
    }
    assert drift == {}, f"importer/server alias drift: {drift}"


def test_bemba_kings_imports_and_resolves_as_reported(client):
    """The mapping reported from the church: kings = mafumu."""
    from importer import canonical_book_key
    from server import resolve_book

    assert canonical_book_key("Mafumu") == "kings"
    assert canonical_book_key("1 Mafumu") == "1 kings"
    assert canonical_book_key("2 Mafumu") == "2 kings"
    # The import key and the spoken lookup key must be identical, otherwise
    # the row exists but can never be found.
    assert resolve_book("mafumu") == canonical_book_key("Mafumu")
    assert resolve_book("1 mafumu") == canonical_book_key("1 Mafumu")
    assert resolve_book("2 mafumu") == canonical_book_key("2 Mafumu")


def test_resolve_book_maps_spoken_cardinal_prefixes(client):
    """Whisper says "one mafumu"; the alias table is keyed "1 mafumu"."""
    from server import resolve_book

    assert resolve_book("one mafumu") == "1 kings"
    assert resolve_book("two mafumu") == "2 kings"
    assert resolve_book("one yohane") == "1 john"
    assert resolve_book("one kings") == "1 kings"
    assert resolve_book("two samweli") == "2 samuel"


def test_bare_numbered_book_is_deliberately_not_guessed(client):
    """A bare "Mafumu 5:14" must never be silently guessed as 1 or 2 Kings.

    Decision recorded from the church (2026-09-21): leaving the overlay
    unchanged is preferable to putting the WRONG book on screen. "mafumu"
    resolves to the bare "kings" key, which no Bible module can define because
    every module numbers Kings, so the lookup misses and handle_verse emits
    nothing. The numbered forms are the supported path.
    """
    from server import resolve_book

    assert resolve_book("mafumu") == "kings"
    # The bare form is preserved, never upgraded to a specific book.
    assert resolve_book("mafumu") not in ("1 kings", "2 kings")
    assert resolve_book("kings") == "kings"
    # Every numbered spelling works, so the operator always has a safe route.
    assert resolve_book("1 mafumu") == "1 kings"
    assert resolve_book("one mafumu") == "1 kings"
    assert resolve_book("first mafumu") == "1 kings"
    assert resolve_book("2 mafumu") == "2 kings"
    assert resolve_book("two mafumu") == "2 kings"


# --------------------------------------------------------------------------
# /test broadcast route
# --------------------------------------------------------------------------
def test_test_broadcast_route_broadcasts_without_error(client, monkeypatch):
    """Regression: server-level SocketIO.emit() rejects broadcast=True.

    The overlay self-test route crashed in production with
    "TypeError: Server.emit() got an unexpected keyword argument 'broadcast'".
    broadcast=True is valid only on the handler-level flask_socketio.emit(),
    which consumes it and converts it to to=None; SocketIO.emit() forwards
    unknown kwargs straight to python-socketio, which no longer accepts it.
    Omitting `to` already broadcasts to every connected client.
    """
    import server as server_module

    monkeypatch.delenv(server_module.ENV_KEY_NAME, raising=False)
    with server_module.app.test_request_context("/test"):
        response = server_module.test_broadcast()

    assert response.status_code == 200
    body = response.get_json()
    assert body["sent"] is True
    assert body["event"] == "update_overlay"

    # With a shared secret configured, the route must refuse a keyless caller -
    # otherwise anyone could push a fake verse onto a live stream.
    monkeypatch.setenv(server_module.ENV_KEY_NAME, "s3cret")
    with server_module.app.test_request_context("/test"):
        _, status = server_module.test_broadcast()
    assert status == 403


def test_api_verse_resolves_and_normalizes(client, monkeypatch, tmp_path):
    """The overlay deep-link API must resolve aliases and honour the language."""
    import server as server_module
    from importer import import_freeshow_json

    db_path = str(tmp_path / "api.db")
    seed = tmp_path / "eng.json"
    seed.write_text(
        '{"1 Kings": {"5": {"14": "And he sent them to Lebanon..."}}}', encoding="utf-8"
    )
    import_freeshow_json(str(seed), translation_code="eng", db_path=db_path)
    monkeypatch.setattr(server_module, "DB_PATH", db_path)

    with server_module.app.test_request_context("/api/verse?book=1+mafumu&chapter=5&verse=14"):
        ok = server_module.api_verse().get_json()
    assert ok["book"] == "1 Kings"

    # A bare numbered-only book must 404, never guess a specific book.
    with server_module.app.test_request_context("/api/verse?book=mafumu&chapter=5&verse=14"):
        missing, status = server_module.api_verse()
    assert status == 404
    assert missing.get_json()["book_normalized"] == "kings"

    # Bad input is rejected, not crashed on.
    with server_module.app.test_request_context("/api/verse?book=John&chapter=x&verse=1"):
        _, status = server_module.api_verse()
    assert status == 400
    with server_module.app.test_request_context("/api/verse"):
        _, status = server_module.api_verse()
    assert status == 400


def test_resolve_book_normalises_ordinal_abbreviations_outside_the_alias_table(client):
    """Exercises the 1st/2nd/3rd word-boundary regex path directly.

    Every real "1st X" book is already aliased, so this path only fires for
    input the alias table does not know. It is asserted here because a mangled
    escape in the pattern would make it match nothing and silently pass the
    ordinal through unnormalised.
    """
    from server import resolve_book

    assert resolve_book("1st zebra") == "1 zebra"
    assert resolve_book("2nd zebra") == "2 zebra"
    assert resolve_book("3rd zebra") == "3 zebra"


def test_html_templates_have_no_broken_string_literals(client):
    """Regression: a mangled escape left a real line break inside a JS string.

    The control panel shipped with the slide handler's line-split written as a
    string literal broken across two source lines, which raised "SyntaxError:
    '' string literal contains an unescaped line break" and killed the ENTIRE
    control-panel script: no io(), no language buttons, no verse form, so
    /control could never emit anything.
    """
    from server import CONTROL_HTML, OVERLAY_HTML, STAGE_HTML

    def broken(template):
        hits = []
        for i, line in enumerate(template.splitlines(), 1):
            stripped = line.strip()
            if not stripped or stripped.startswith("//") or stripped.startswith("<"):
                continue
            if stripped.endswith(("'", '"')) and (
                stripped.count("'") % 2 or stripped.count('"') % 2
            ):
                hits.append((i, line))
        return hits

    for name, template in (
        ("CONTROL_HTML", CONTROL_HTML),
        ("OVERLAY_HTML", OVERLAY_HTML),
        ("STAGE_HTML", STAGE_HTML),
    ):
        assert broken(template) == [], f"{name} has a broken string literal"


# --------------------------------------------------------------------------
# Stream-key resolution (regression: an empty key silently broke the voice path)
# --------------------------------------------------------------------------
def test_stream_key_prefers_env_then_falls_back_to_the_key_file(tmp_path, monkeypatch):
    """The key must be discoverable without hardcoding it in the source.

    Regression: STREAM_KEY was read purely from os.environ with an empty
    default, and run_client.ps1 never set that variable. The client therefore
    sent key="" and the VPS rejected EVERY emit with "[Auth]: Rejected", so the
    overlay looked dead while the socket was perfectly healthy.
    """
    import client as client_module

    key_file = tmp_path / "stream_key.txt"
    key_file.write_text("  from-file  ", encoding="utf-8")
    monkeypatch.setattr(client_module, "_KEY_FILE", str(key_file))

    monkeypatch.delenv("DABARSTREAM_KEY", raising=False)
    assert client_module._load_stream_key() == "from-file"

    monkeypatch.setenv("DABARSTREAM_KEY", "from-env")
    assert client_module._load_stream_key() == "from-env"


def test_stream_key_is_empty_when_nothing_is_configured(tmp_path, monkeypatch):
    import client as client_module

    monkeypatch.delenv("DABARSTREAM_KEY", raising=False)
    monkeypatch.setattr(client_module, "_KEY_FILE", str(tmp_path / "missing.txt"))
    assert client_module._load_stream_key() == ""


# --------------------------------------------------------------------------
# One sentence carrying both a language command and a verse
# --------------------------------------------------------------------------
def test_process_audio_emits_verse_when_the_sentence_also_names_a_language(client):
    """'Let us read John 3:16 in English' must switch AND show the verse.

    Regression: the language branch ended in `continue`, so the verse half of
    the sentence was discarded and nothing reached the overlay even though the
    speaker had clearly asked for a verse.
    """
    model = _FakeModel(["Let us read John 3:16 in English"])
    client.process_audio(b"audio", model)

    events = [e for e, _ in client.recorder.events]
    assert "set_language" in events

    verse_events = [p for e, p in client.recorder.events if e == "verse_triggered"]
    assert len(verse_events) == 1
    assert verse_events[0]["book"] == "John"
    assert (verse_events[0]["chapter"], verse_events[0]["verse"]) == ("3", "16")
    assert verse_events[0]["key"] == client.STREAM_KEY


# --------------------------------------------------------------------------
# Spoken lead-ins must not be folded into the book name
# --------------------------------------------------------------------------
def test_strip_lead_in_removes_spoken_preamble_only(client):
    """'Let us read John' is a preamble plus a book, not a book name.

    Regression: the detector captured the whole run of words before the
    numbers, so 'Let us read John 3:16' resolved the book 'let us read john',
    matched no row, and broadcast nothing - with no error logged anywhere.
    """
    assert client.strip_lead_in("Let us read John") == "John"
    assert client.strip_lead_in("Now let us read John") == "John"
    assert client.strip_lead_in("turn to 1 John") == "1 John"
    assert client.strip_lead_in("the book of John") == "John"

    # Real book names must survive untouched.
    assert client.strip_lead_in("First John") == "First John"
    assert client.strip_lead_in("Song of Solomon") == "Song of Solomon"
    assert client.strip_lead_in("Yohane") == "Yohane"


def test_parse_verse_reference_ignores_spoken_lead_in(client):
    assert client.parse_verse_reference("Let us read John 3:16") == ("John", "3", "16")
    assert client.parse_verse_reference("John 3:16") == ("John", "3", "16")
    assert client.parse_verse_reference("1 Timothy 3:16") == ("1 Timothy", "3", "16")


# --------------------------------------------------------------------------
# Honest labelling when a translation has no rows
# --------------------------------------------------------------------------
def test_lang_label_admits_when_english_is_shown_instead(client, monkeypatch, tmp_path):
    """'ton' has no rows, so the overlay must not silently say 'Chitonga'.

    Regression: the UI and hotkey 4 offered Chitonga, the resolver fell back to
    English text, and the label still read 'Chitonga' - so a correct fallback
    looked like a broken translation on air.
    """
    import server as server_module
    from importer import import_freeshow_json

    db_path = str(tmp_path / "labels.db")
    seed = tmp_path / "eng.json"
    seed.write_text('{"John": {"3": {"16": "For God so loved the world..."}}}', encoding="utf-8")
    import_freeshow_json(str(seed), translation_code="eng", db_path=db_path)

    monkeypatch.setattr(server_module, "DB_PATH", db_path)
    server_module._TRANSLATION_HAS_DATA.clear()

    assert server_module.lang_label_for("eng") == "English"
    assert server_module.translation_has_data("ton") is False
    assert server_module.lang_label_for("ton") == "Chitonga (English shown)"


# --------------------------------------------------------------------------
# Control panel: the slide/timer controls the script talks to must exist
# --------------------------------------------------------------------------
def test_control_panel_exposes_slide_and_timer_controls(client):
    """The panel JS drives these ids; without the elements they were dead code."""
    from server import CONTROL_HTML

    for element_id in (
        "slide-title", "slide-lines", "slide-btn",
        "timer-minutes", "timer-label", "timer-start", "timer-stop", "timer-clear",
    ):
        assert 'id="' + element_id + '"' in CONTROL_HTML, element_id


def test_control_panel_verse_emit_carries_the_active_language(client):
    from server import CONTROL_HTML

    assert "lang: currentLang," in CONTROL_HTML


# --------------------------------------------------------------------------
# Project listing URLs must be URL-encoded
# --------------------------------------------------------------------------
def test_project_list_urls_are_url_encoded(client, monkeypatch, tmp_path):
    import server as server_module

    monkeypatch.setattr(server_module, "PROJECTS_DIR", str(tmp_path))
    (tmp_path / "Sunday Set.json").write_text("{}", encoding="utf-8")

    listed = server_module.list_projects()
    assert len(listed) == 1
    assert listed[0]["url"] == "/api/projects/Sunday%20Set"






# --------------------------------------------------------------------------
# Phantom triggers from ordinary speech (wrong-verse-on-air class)
# --------------------------------------------------------------------------
def test_ordinary_speech_numbers_do_not_trigger_a_verse(client):
    """Prose containing two numbers must not be read as a scripture reference.

    Regression: the digit pattern accepted ANY word run as a book, so
    "we have 3 16 members here" produced book "we have" and "the act 2 4 was
    great" produced book "act" - which resolve_book maps to ACTS. The second
    case put the WRONG VERSE on the overlay mid-service, the exact failure the
    project already decided is worse than showing nothing.
    """
    for utterance in (
        "we have 3 16 members here",
        "turn with me to page 5 12",
        "the meeting is at 10 30",
        "the act 2 4 was great",
        "we had a job 3 12 yesterday",
        "give me 5 10 minutes",
        "he is 3 12 years old",
    ):
        assert client.parse_verse_reference(utterance) is None, utterance


def test_real_references_still_trigger_after_the_phantom_guard(client):
    """The guard must not cost a single genuine trigger - including 'act'.

    'Act 2:4' is a real request for the book of Acts; only the prose form
    ('the act 2 4 was great') is rejected. Cue phrases ending in a stop-word
    ('turn to Psalm 23', 'go to John 3') must also keep working.
    """
    expected = {
        "John 3:16": ("John", "3", "16"),
        "Let us read John 3:16": ("John", "3", "16"),
        "Now let us read John 3:16": ("John", "3", "16"),
        "1 Timothy 3:16": ("1 Timothy", "3", "16"),
        "Act 2:4": ("Act", "2", "4"),
        "Mark 5:6": ("Mark", "5", "6"),
        "Job 3:12": ("Job", "3", "12"),
        "Psalms 23:1": ("Psalms", "23", "1"),
        "Rom 8:1": ("Rom", "8", "1"),
        "1 John 4:8": ("1 John", "4", "8"),
        "Genesis 1:1": ("Genesis", "1", "1"),
    }
    for utterance, want in expected.items():
        assert client.parse_verse_reference(utterance) == want, utterance


def test_cue_phrase_ending_in_a_stop_word_is_not_rejected(client):
    """'turn to Psalm 23' is a command, not prose.

    The phantom guard rejects an immediately-preceding stop-word, so it must
    distinguish 'turn TO Psalm' (a cue verb precedes the stop-word) from
    'the act' (bare filler). Without that distinction the guard swallowed a
    perfectly good spoken reference.
    """
    assert client.parse_verse_reference("turn to Psalm 23 verse 1") == ("Psalm", "23", "1")
    assert client.parse_verse_reference("go to John 3:16") == ("John", "3", "16")


def test_looks_like_book_rejects_sentence_fragments(client):
    assert client.looks_like_book("John") is True
    assert client.looks_like_book("1 Timothy") is True
    assert client.looks_like_book("Yohane") is True          # local language
    assert client.looks_like_book("abena roma") is True      # multi-word local
    assert client.looks_like_book("") is False
    assert client.looks_like_book("the meeting is at") is False
    assert client.looks_like_book("we have") is False
    # 'the act' is accepted HERE because looks_like_book deliberately tolerates
    # a spoken lead-in - the phrase could be 'the book of Acts'. Distinguishing
    # prose from a reference is _lead_in_precedes_book's job, asserted below.
    assert client.looks_like_book("the act") is True
    assert client._lead_in_precedes_book("the act", "act") is True
    assert client._lead_in_precedes_book("Act", "Act") is False
    # A cue phrase ending in a stop-word is a command, not prose.
    assert client._lead_in_precedes_book("turn to Psalm", "Psalm") is False


# --------------------------------------------------------------------------
# The fallback must not print the English verse twice
# --------------------------------------------------------------------------
def test_fallback_to_english_does_not_duplicate_the_verse(
    client, monkeypatch, tmp_path
):
    """A local-language request served from English must render ONE line.

    Regression: `nya` had no rows, the resolver fell back to English, and
    handle_verse then added `text_eng` - the SAME English string - so the
    overlay showed the identical verse twice (primary + italic subtitle).
    """
    import server as server_module
    from importer import import_freeshow_json

    db_path = str(tmp_path / "dupe.db")
    seed = tmp_path / "eng.json"
    seed.write_text('{"John": {"3": {"16": "For God so loved the world..."}}}',
                    encoding="utf-8")
    import_freeshow_json(str(seed), translation_code="eng", db_path=db_path)

    monkeypatch.setattr(server_module, "DB_PATH", db_path)
    server_module._TRANSLATION_HAS_DATA.clear()

    emitted = []
    monkeypatch.setattr(server_module, "emit",
                        lambda e, p, **k: emitted.append((e, p)))

    server_module.handle_verse(
        {"book": "John", "chapter": 3, "verse": 16, "lang": "nya"}
    )

    assert len(emitted) == 1
    _, payload = emitted[0]
    # The payload must report what was ACTUALLY served, not what was asked for.
    assert payload["lang"] == "eng"
    assert payload["lang_label"] == "English"
    # And there must be no duplicate subtitle.
    assert "text_eng" not in payload
    assert "Mulungu" not in payload["text"]


def test_real_local_translation_still_gets_its_english_subtitle(
    client, monkeypatch, tmp_path
):
    """The duplicate fix must not remove the dual-language feature."""
    import server as server_module
    from importer import import_freeshow_json, import_xml_translation

    db_path = str(tmp_path / "dual.db")
    eng = tmp_path / "eng.json"
    eng.write_text('{"John": {"3": {"16": "For God so loved the world..."}}}',
                   encoding="utf-8")
    import_freeshow_json(str(eng), translation_code="eng", db_path=db_path)

    nya = tmp_path / "nya.xml"
    nya.write_text(
        '<XMLBIBLE><BIBLEBOOK bname="Yohane"><CHAPTER cnumber="3">'
        '<VERSE vnumber="16">Pakuti Mulungu anachikonda dziko lapansi</VERSE>'
        "</CHAPTER></BIBLEBOOK></XMLBIBLE>",
        encoding="utf-8",
    )
    import_xml_translation(str(nya), translation_code="nya", db_path=db_path)

    monkeypatch.setattr(server_module, "DB_PATH", db_path)
    server_module._TRANSLATION_HAS_DATA.clear()

    emitted = []
    monkeypatch.setattr(server_module, "emit",
                        lambda e, p, **k: emitted.append((e, p)))

    server_module.handle_verse(
        {"book": "John", "chapter": 3, "verse": 16, "lang": "nya"}
    )

    _, payload = emitted[0]
    assert payload["lang"] == "nya"
    assert "Mulungu" in payload["text"]
    # A genuinely DIFFERENT English line must still be attached.
    assert "text_eng" in payload
    assert "loved the world" in payload["text_eng"]


def test_query_reports_the_served_translation(monkeypatch, tmp_path):
    """resolve_and_query_bible must disclose which code it actually served."""
    import server as server_module
    from importer import import_freeshow_json

    db_path = str(tmp_path / "src.db")
    seed = tmp_path / "eng.json"
    seed.write_text('{"John": {"3": {"16": "For God so loved..."}}}', encoding="utf-8")
    import_freeshow_json(str(seed), translation_code="eng", db_path=db_path)

    # Requested nya, served eng -> with_source must say so.
    row = server_module.resolve_and_query_bible(
        "John", 3, 16, "nya", db_path=db_path, with_source=True
    )
    assert row is not None
    assert row[4] == "eng"

    # A genuine hit reports its own code.
    row_eng = server_module.resolve_and_query_bible(
        "John", 3, 16, "eng", db_path=db_path, with_source=True
    )
    assert row_eng[4] == "eng"

    # Default shape stays a 4-tuple so existing callers are unaffected.
    plain = server_module.resolve_and_query_bible("John", 3, 16, "eng", db_path=db_path)
    assert len(plain) == 4


# --------------------------------------------------------------------------
# The Socket.IO browser client must be served locally
# --------------------------------------------------------------------------
def test_socketio_client_is_served_locally_not_redirected(client):
    """The overlay must work with no internet.

    Regression: static/socket.io.js was never committed, so /js/socket.io.js
    always redirected to the cdnjs CDN. On a venue with no connectivity the
    browser got an HTML error page instead of the client, `io` was undefined,
    and the overlay rendered blank - the failure the route was added to fix.
    """
    import os

    import server as server_module

    assert os.path.isfile(server_module.SOCKETIO_CLIENT_LOCAL), (
        "static/socket.io.js is missing - the overlay cannot load offline"
    )

    response = server_module.app.test_client().get("/js/socket.io.js")
    assert response.status_code == 200
    # A redirect here means the CDN fallback fired, which breaks offline use.
    assert response.headers.get("Location") is None
    assert len(response.data) > 10_000
    assert b"Socket.IO" in response.data
