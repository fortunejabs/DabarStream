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
    assert ("set_language", {"lang": "bem"}) in client.recorder.events


def test_process_audio_switches_language_then_tags_following_verses(client):
    """A spoken switch must apply to every verse trigger that follows it."""
    model = _FakeModel(["show bemba", "Yohane chapter 3 verse 16"])
    returned = client.process_audio(b"audio", model)

    assert returned == "bem"
    assert ("set_language", {"lang": "bem"}) in client.recorder.events

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


def test_translation_labels_cover_hotkey_languages(client):
    from server import TRANSLATION_LABELS

    for code in client.HOTKEY_LANGS.values():
        assert code in TRANSLATION_LABELS


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
