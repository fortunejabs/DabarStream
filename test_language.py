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
