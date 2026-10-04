"""
DabarStream - Local Windows Capture Client
Captures microphone audio, transcribes locally on CPU with faster-whisper
(INT8), detects scripture references, and emits them to the cloud VPS.
"""

import os
import queue
import re
import threading
import time

import numpy as np
import socketio
from faster_whisper import WhisperModel

# --- AUDIO BACKEND ---------------------------------------------------------
# Prefer sounddevice: it wraps PortAudio through ctypes and publishes prebuilt
# wheels for every current CPython, so `pip install sounddevice` simply works.
# PyAudio 0.2.14 has no Python 3.14 wheel and building it from source requires
# MSVC Build Tools, so it is retained only as a fallback.
try:
    import sounddevice as sd
    AUDIO_BACKEND = "sounddevice"
except ImportError:  # pragma: no cover - depends on the host environment
    sd = None
    import pyaudio
    AUDIO_BACKEND = "pyaudio"

# --- NETWORK CONFIG ---
TARGET_VPS_IP = "193.123.179.93"
TARGET_PORT = 5000
VPS_URL = f"http://{TARGET_VPS_IP}:{TARGET_PORT}"

# --- AI SPEECH ENGINE CONFIG (CPU OPTIMIZED) ---
MODEL_SIZE = "base.en"   # 'tiny.en' or 'base.en' are best for low-resource CPU use
COMPUTE_TYPE = "int8"    # Quantization reduces memory & CPU overhead
AUDIO_BUFFER_LIMIT = 4   # Evaluate incoming audio every N seconds
DEFAULT_LANG = "eng"     # Translation to display: 'eng', 'bem', 'nya', 'ton'...

# Shared secret sent with every control emit; must match DABARSTREAM_KEY in
# the server environment.
#
# Resolution order:
#   1. the DABARSTREAM_KEY environment variable (run_client.ps1 exports it)
#   2. stream_key.txt next to this file (git-ignored; one bare line)
#   3. "" - which DISABLES client-side auth and therefore makes the VPS reject
#      every emit with "[Auth]: Rejected". main() warns loudly in that case,
#      because the symptom (a silent overlay) looks like a broken socket.
_KEY_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "stream_key.txt"
)


def _load_stream_key():
    """Env var first, then stream_key.txt, then empty."""
    key = (os.environ.get("DABARSTREAM_KEY") or "").strip()
    if key:
        return key
    try:
        with open(_KEY_FILE, "r", encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


STREAM_KEY = _load_stream_key()



# Voice-activated language switching: spoken phrases -> translation codes
LANG_COMMANDS = {
    "show english": "eng", "english": "eng", "in english": "eng",
    "show bemba": "bem", "bemba": "bem", "icibemba": "bem", "baibele": "bem",
    "show nyanja": "nya", "nyanja": "nya", "chinyanja": "nya", "chewa": "nya",
    "show chewa": "nya",
    "show tonga": "ton", "tonga": "ton", "chitonga": "ton",
}

# Keyboard hotkeys (optional, needs `pip install pynput`): 1=eng 2=bem 3=nya 4=ton
HOTKEY_LANGS = {"1": "eng", "2": "bem", "3": "nya", "4": "ton"}

# Live state: translation currently shown on the overlay. Updated by voice
# commands or hotkeys, and attached to every verse trigger we emit.
ACTIVE_LANG = DEFAULT_LANG

# Audio capture defaults
FORMAT = "int16"   # sounddevice dtype name; the PyAudio fallback maps this to paInt16
CHANNELS = 1
RATE = 16000
CHUNK = 1024

sio = socketio.Client()

# Lock ensuring only one thread calls sio.connect() at a time.
# Without this, the background reconnect manager and an accidental concurrent
# call can both see sio.connected == False and fire simultaneously, leaving
# the socket in an undefined double-connected state.
_connect_lock = threading.Lock()

# Bounded queue between the audio-capture producer and the Whisper worker.
# maxsize=4 caps memory at ~4 * AUDIO_BUFFER_LIMIT seconds of audio; if the
# worker falls behind (very slow CPU), old buffers are silently dropped via
# the non-blocking put below rather than growing the queue without limit.
_audio_queue: queue.Queue = queue.Queue(maxsize=4)


@sio.event
def connect():
    print(f"Successfully linked to Cloud Streaming Hub at {VPS_URL}")


@sio.event
def disconnect():
    print("Disconnected from Cloud Streaming Hub. Retrying...")


# Regex trigger scanning spoken language for scripture coordinates.
# The book group allows an optional leading numeral so Whisper's digit form
# keeps numbered books intact: "1 Timothy 3:16" -> book "1 Timothy" (not
# "Timothy", which would lose the book entirely).
verse_pattern = re.compile(
    r"\b((?:\d+\s+)?[A-Za-z][A-Za-z-\s]*?)(?:\s+chapter)?\s+(\d+)(?:\s*:\s*|\s+verse\s+|\s+)(\d+)",
    re.IGNORECASE,
)

# Spoken number words Whisper emits instead of digits, e.g. "chapter three
# verse sixteen". Hyphens/spaces are normalized before lookup so "twenty-one"
# and "twenty one" both resolve.
NUMBER_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
    "hundred": 100, "hundred and": 100,
}

# Number words that must bind to what follows them: "twenty three" is 23, not
# 20:3. Consulted when splitting a bare two-number spoken reference.
BINDING_NUMBER_WORDS = {
    "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty",
    "ninety", "hundred",
}

# Canonical book names plus the spoken forms Whisper most often produces. A
# candidate book must match one of these before a trigger is emitted.
#
# WHY THIS EXISTS: the digit pattern matches any "words + number + number" run,
# so ordinary preaching created phantom triggers - "we have 3 16 members" ->
# book "we have", "the act 2 4 was great" -> book "act" -> resolve_book maps it
# to ACTS and the WRONG VERSE goes on air (Acts 2:4). A garbage book that
# happens to be a valid alias is the dangerous case; the rest merely log noise.
# Requiring a known book name removes the whole class.
#
# Local-language names (Yohane, Mafumu, Salimo, ...) are NOT listed here; they
# are accepted through _SINGLE_WORD_BOOK_MIN_LEN and the multi-word rule
# below, and ultimately validated by the server's BOOK_ALIASES.
_KNOWN_BOOKS = {
    "genesis", "exodus", "leviticus", "numbers", "deuteronomy", "joshua",
    "judges", "ruth", "samuel", "kings", "chronicles", "ezra", "nehemiah",
    "esther", "job", "psalm", "psalms", "proverbs", "ecclesiastes",
    "song of solomon", "isaiah", "jeremiah", "lamentations", "ezekiel",
    "daniel", "hosea", "joel", "amos", "obadiah", "jonah", "micah",
    "nahum", "habakkuk", "zephaniah", "haggai", "zechariah", "malachi",
    "matthew", "mark", "luke", "john", "acts", "romans", "corinthians",
    "galatians", "ephesians", "philippians", "colossians", "thessalonians",
    "timothy", "titus", "philemon", "hebrews", "james", "peter", "jude",
    "revelation",
    # Common abbreviations Whisper actually emits.
    "gen", "exo", "lev", "num", "deut", "josh", "judg", "ps", "prov",
    "eccl", "isa", "jer", "lam", "ezek", "dan", "hos", "obad", "mic",
    "nah", "hab", "zeph", "hag", "zech", "mal", "mat", "mk", "lk",
    "jn", "act", "rom", "cor", "gal", "eph", "phil", "col", "thess",
    "tim", "tit", "phm", "heb", "jas", "pet", "rev",
    # Spoken ordinal/cardinal prefixes kept by the digit pattern.
    "first", "second", "third", "one", "two", "three",
}

# Words that never appear in a Bible book name. A multi-word candidate
# containing one is an ordinary sentence fragment, not a reference: phantom
# triggers detected as books were "turn with me to page", "meeting is at",
# "had a job" - each carried a stop-word, while every real multi-word book
# name ("abena roma", "song of solomon", "nyimbo ya solomoni") is free of them.
_BOOK_STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "been", "but", "by",
    "can", "could", "did", "do", "does", "for", "from", "had", "has",
    "have", "he", "her", "here", "him", "his", "how", "i", "if", "in",
    "into", "is", "it", "its", "just", "me", "my", "no", "not", "of",
    "on", "or", "our", "out", "page", "she", "should", "so", "that",
    "the", "their", "them", "then", "there", "these", "they", "this",
    "those", "to", "up", "us", "was", "we", "were", "what", "when",
    "where", "which", "who", "will", "with", "would", "you", "your",
}

# Verbs that turn a following stop-word into a deliberate cue rather than
# filler: "turn TO Psalm 23" and "go TO John 3" are commands, whereas
# "the act 2 4" / "we have 3 16" are prose. Consulted only when a stop-word
# sits immediately in front of the detected book.
_BOOK_CUE_WORDS = {
    "turn", "go", "come", "look", "read", "open", "see", "check",
    "switch", "show", "give", "let", "take", "find", "sing", "quote",
}


def looks_like_book(book):
    """True when `book` could plausibly be a Bible book name.

    Guards against phantom triggers built from ordinary speech. Accepts:
      * a known English book or abbreviation ("john", "1 timothy", "acts");
      * a single local-language name of plausible length ("yohane", "mafumu"),
        which the server validates against its alias table;
      * a numbered form ("1 mafumu", "2 samweli").

    Rejects sentence fragments such as "we have", "the meeting is at", and
    "2024 we saw", which produced garbage books and, on a coincidental alias,
    the wrong verse on air.
    """
    cleaned = " ".join((book or "").split()).lower()
    if not cleaned:
        return False
    # Spoken lead-ins ('let us read', 'turn to') sit in front of a REAL book
    # name, so they are removed before judging the remainder. The stop-word
    # test below then only fires on words that consume the actual book, which
    # is what separates 'let us read john' (a reference) from 'the act 2 4 was
    # great' (prose).
    cleaned = strip_lead_in(cleaned)
    # Drop a leading numeral so "1 timothy" is checked as "timothy".
    parts = cleaned.split()
    if parts and parts[0].isdigit():
        parts = parts[1:]
    if not parts:
        return False
    core = " ".join(parts)
    if core in _KNOWN_BOOKS:
        return True
    # A bare English ordinal that keeps no book ("first zebra") is not a book.
    if core in {"first", "second", "third", "one", "two", "three"}:
        return False
    if len(parts) == 1:
        # A single word that is itself a stop-word ('have', 'act' aside) is
        # prose, never a book: 'we HAVE 3 16 members' survived only because the
        # stop-word test used to run on the multi-word branch alone.
        if parts[0] in _BOOK_STOP_WORDS:
            return False
        # Single local-language name: plausible if long enough not to be noise
        # ('job' is 3, so 3 keeps it, while 'at'/'is'/'we' are rejected).
        return len(parts[0]) >= 3
    # A multi-word candidate is only credible when EVERY word could belong to a
    # book name. It must contain no stop-words: sentence fragments detected as
    # books all carried one ("turn WITH me TO page", "meeting IS AT", "had a
    # job"). Real multi-word names never do - "abena roma", "nyimbo ya
    # solomoni", "song of solomon", "1 mafumu" - so this rejects the phantom
    # triggers while leaving local-language names to the server's alias table.
    if any(word in _BOOK_STOP_WORDS for word in parts):
        return False
    # Guard against long runs of ordinary words being read as one book name.
    return len(parts) <= 4


def _lead_in_precedes_book(raw_book, stripped_book):
    """True when a bare STOP-WORD was removed from directly in front of `book`.

    Distinguishes a DELIBERATE reference from prose that merely survived
    `strip_lead_in`. "Act 2:4" keeps its book as the whole capture and is real;
    "the act 2 4 was great" only becomes "act" because "the" was stripped, which
    means the speaker was describing an act, not naming the book of Acts.

    Only an immediate stop-word counts. Purpose-built lead-ins are NOT
    stop-words ("read", "turn to", "open"), so a genuine spoken command such as
    "Let us read John 3:16" -> "john" keeps working: "read" is a lead-in cue,
    whereas "the"/"we" are filler that real references do not carry.
    """
    raw = " ".join((raw_book or "").split()).lower()
    stripped = " ".join((stripped_book or "").split()).lower()
    if not raw or not stripped or raw == stripped:
        return False
    # Only a single surviving word is ambiguous enough to need this guard.
    if len(stripped.split()) != 1 or stripped not in _KNOWN_BOOKS:
        return False
    raw_parts = raw.split()
    stripped_parts = stripped.split()
    # The words removed from the front of the capture.
    removed = raw_parts[: len(raw_parts) - len(stripped_parts)]
    if not removed:
        return False
    # Only the word IMMEDIATELY before the book can turn prose into a "book".
    if removed[-1] not in _BOOK_STOP_WORDS:
        return False
    # A CUE phrase ending in a stop-word is a deliberate command, not prose:
    # 'turn TO Psalm 23' and 'go TO John 3' must keep working, while 'the act'
    # and 'we have' are bare filler and must not. The cue is recognised by the
    # word BEFORE the stop-word (turn/go/look/read/open...).
    if len(removed) >= 2 and removed[-2] in _BOOK_CUE_WORDS:
        return False
    return True

# Spoken lead-ins that Whisper folds into the front of a detected book name:
# "Let us read John 3:16" -> book "Let us read John". Left alone the resolver
# looks up "let us read john", finds no row, and the overlay stays blank with
# no error at all - the hardest kind of failure to diagnose during a service.
#
# Only LEADING words are removed, so numbered books ("1 John"), ordinals
# ("First John") and multi-word names ("Song of Solomon") are untouched.
_LEAD_IN_RE = re.compile(
    r"^(?:"
    r"let\s+us|let's|lets|we|please|now|and|so|then|alright|"
    r"okay|ok|well|read|turn\s+to|go\s+to|looking\s+at|look\s+at|"
    r"open|see|from|the|book|of"
    r")\s+",
    re.IGNORECASE,
)


def strip_lead_in(book):
    """Removes spoken lead-in words folded into a detected book name.

    Looped so several lead-ins in a row ("Now let us read John") are all
    removed. Falls back to the original text if everything would be stripped,
    so a book name is never reduced to nothing.
    """
    cleaned = " ".join(book.split())
    if not cleaned:
        return book
    previous = None
    while previous != cleaned:
        previous = cleaned
        cleaned = _LEAD_IN_RE.sub("", cleaned).strip()
    return cleaned or book


def words_to_number(text):
    """Converts a spoken number phrase to an int (e.g. 'twenty one' -> 21).

    Returns None when the phrase contains no recognizable number words.
    """
    if not isinstance(text, str):
        return None
    tokens = text.lower().replace("-", " ").split()
    total = 0
    current = 0
    found = False
    i = 0
    while i < len(tokens):
        two = " ".join(tokens[i:i + 2])
        if two in NUMBER_WORDS:
            value = NUMBER_WORDS[two]
            found = True
            if value == 100:
                current = max(current, 1) * 100
            else:
                current += value
            i += 2
            continue
        word = tokens[i]
        if word in NUMBER_WORDS:
            found = True
            value = NUMBER_WORDS[word]
            if value == 100:
                current = max(current, 1) * 100
            else:
                current += value
        elif word == "and":
            pass  # filler inside "hundred and X"; ignored unless nothing found
        else:
            return None  # non-number word breaks the phrase
        i += 1
    if not found:
        return None
    return total + current


word_verse_pattern = re.compile(
    r"\b([A-Za-z-\s]+?)(?:\s+chapter)?\s+([a-z][a-z\s-]*?)(?:\s*:\s*|\s+verse\s+|\s+chapter\s+|\s+)([a-z][a-z\s-]*)",
    re.IGNORECASE,
)


def parse_verse_reference(text):
    """Parses 'John 3:16' or 'John chapter three verse sixteen' -> (book, ch, vs).

    Returns (book, chapter, verse) as (str, str, str) or None when no
    reference is detected. Digit form is tried first; spoken number words
    are the fallback. Number words are collected into maximal runs, with
    'chapter'/'verse'/':' acting as separators, so compound spoken numbers
    ("twenty three", "one hundred and fifty") resolve correctly. A bare
    two-number form with no separator ("First John four eight") is split into
    chapter/verse, unless the first word binds ("twenty three" stays 23).

    A candidate book must pass `looks_like_book` before a trigger is returned.
    Without that check ordinary speech produced phantom references -
    "the act 2 4 was great" became book "act", resolved to ACTS, and put the
    wrong verse on air.
    """
    digit_match = verse_pattern.search(text)
    if digit_match:
        raw_book, chapter, verse = digit_match.groups()
        # Decide on the RAW capture, before lead-ins are stripped: a genuine
        # reference ("Act 2:4") is already a bare book name, while a sentence
        # fragment ("the act 2 4 was great", "we have 3 16") still carries the
        # stop-word that proves it is prose. Checking after stripping would see
        # only "act"/"have" and lose that evidence.
        if not looks_like_book(raw_book):
            return None
        book = strip_lead_in(raw_book)
        if not looks_like_book(book):
            return None
        if _lead_in_precedes_book(raw_book, book):
            return None
        return book, chapter, verse

    tokens = text.split()

    # Book = everything before the first number word.
    book_end = None
    for idx, tok in enumerate(tokens):
        stripped = tok.lower().strip("-.:")
        if stripped in NUMBER_WORDS:
            book_end = idx
            break
    if book_end is None or book_end == 0:
        return None
    book_tokens = tokens[:book_end]
    # Drop trailing separator words that sit between book and numbers
    # ("John chapter three..." -> book "john", not "john chapter").
    while book_tokens and book_tokens[-1].lower().strip("-.:,") in ("chapter", "verse"):
        book_tokens.pop()
    book = strip_lead_in(" ".join(book_tokens))
    if not re.search(r"[A-Za-z]", book):
        return None
    if not looks_like_book(book):
        return None

    # Split the remainder into maximal runs of number words.
    runs = []
    current = []
    for tok in tokens[book_end:]:
        w = tok.lower().replace("-", "").strip(".,:;")
        if w in NUMBER_WORDS or w == "and":
            current.append(w)
        elif current:
            runs.append(" ".join(current))
            current = []
    if current:
        runs.append(" ".join(current))

    if len(runs) == 1:
        # Bare spoken form with no "chapter"/"verse" separator, e.g.
        # "First John four eight" -> chapter 4, verse 8. Only a two-token run
        # whose first word is a plain unit gets split; a binding word
        # (tens/hundred) must stay joined, so "twenty three" remains 23
        # instead of becoming 20:3.
        parts = runs[0].split()
        if len(parts) == 2 and parts[0] not in BINDING_NUMBER_WORDS:
            runs = parts

    if len(runs) < 2:
        return None
    chapter = words_to_number(runs[0])
    verse = words_to_number(runs[-1])
    if chapter and verse:
        return book, str(chapter), str(verse)
    return None


def detect_language_command(text):
    """
    Checks transcribed speech for a language-switch command, e.g.
    'let us read in Bemba' -> 'bem'. Returns the code or None.
    Longest phrases are matched first to avoid partial collisions.
    """
    cleaned = " ".join(text.lower().split())
    for phrase in sorted(LANG_COMMANDS, key=len, reverse=True):
        if phrase in cleaned:
            return LANG_COMMANDS[phrase]
    return None


def switch_language(lang_code):
    """
    Sets the active translation locally and notifies the cloud overlay.
    Safe to call before the socket connects - the failure is only logged.
    """
    global ACTIVE_LANG
    ACTIVE_LANG = lang_code
    try:
        sio.emit("set_language", {"lang": lang_code, "key": STREAM_KEY})
    except Exception as e:
        print(f"Language switch could not reach VPS yet: {e}")
    return lang_code


def process_audio(audio_bytes, model, active_lang=None):
    """
    Processes audio on CPU using quantized INT8 memory grids.
    `active_lang` defaults to the module-level ACTIVE_LANG so callers (and
    tests) can drive the pipeline without global state.
    """
    if active_lang is None:
        active_lang = ACTIVE_LANG
    audio_array = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
    segments, _ = model.transcribe(audio_array, beam_size=1, vad_filter=True)
    for segment in segments:
        text = segment.text.strip()
        if text:
            print(f"Recognized Speech: {text}")

            # Voice-activated language switch. This used to `continue`, which
            # meant a single sentence carrying BOTH a language command and a
            # verse reference ("read John 3:16 in English") switched language
            # and then threw the verse away. The switch is applied, then the
            # verse is still looked for. A segment that is only a language
            # command ("show tonga") still emits nothing but set_language,
            # because parse_verse_reference finds no reference in it.
            lang_code = detect_language_command(text)
            if lang_code:
                print(f"Language Switch Command -> {lang_code}")
                switch_language(lang_code)
                active_lang = lang_code

            match = parse_verse_reference(text)
            if match:
                book, chapter, verse = match
                book_cleaned = " ".join(book.split())
                print(f"Trigger Detected -> {book_cleaned} {chapter}:{verse} [{active_lang}]")
                try:
                    sio.emit(
                        "verse_triggered",
                        {
                            "book": book_cleaned,
                            "chapter": chapter,
                            "verse": verse,
                            "lang": active_lang,
                            "key": STREAM_KEY,
                        },
                    )
                except Exception as e:
                    print(f"Verse trigger could not reach VPS: {e}")
    return active_lang


def start_hotkey_listener():
    """
    Optional global hotkeys: press 1/2/3/4 to switch translation
    (eng/bem/nya/ton). Requires `pip install pynput`; silently skipped
    if not installed.
    """
    try:
        from pynput import keyboard

        def on_press(key):
            try:
                code = HOTKEY_LANGS.get(key.char)
            except AttributeError:
                return
            if code:
                print(f"Hotkey Language Switch -> {code}")
                switch_language(code)

        listener = keyboard.Listener(on_press=on_press)
        listener.daemon = True
        listener.start()
        print("Hotkeys active: 1=English 2=Bemba 3=Nyanja 4=Tonga")
        return listener
    except ImportError:
        print("pynput not installed - hotkeys disabled (pip install pynput to enable)")
        return None


def _transcription_worker(model):
    """Daemon thread: pulls audio buffers from _audio_queue and transcribes them.

    Runs independently of the microphone capture loop so that a slow Whisper
    inference pass never stalls the audio stream and causes buffer overflows.
    Blocks on queue.get() between bursts, so it uses zero CPU when idle.
    """
    while True:
        audio_bytes = _audio_queue.get()  # blocks until a buffer is ready
        process_audio(audio_bytes, model)
        _audio_queue.task_done()


def capture_loop(model):
    """Reads fixed-size int16 blocks from the microphone and queues them for transcription.

    The actual Whisper inference happens in a separate daemon thread
    (_transcription_worker) so this loop is never stalled by a slow CPU.
    Runs until KeyboardInterrupt. Block size is CHUNK frames; a buffer is
    queued every AUDIO_BUFFER_LIMIT seconds of audio.
    """
    audio_buffer = bytearray()
    threshold = RATE * 2 * AUDIO_BUFFER_LIMIT  # 2 bytes per int16 sample

    def _enqueue(buf):
        """Non-blocking put; silently drops the oldest buffer when the queue is full."""
        try:
            _audio_queue.put_nowait(bytes(buf))
        except queue.Full:
            try:
                _audio_queue.get_nowait()   # discard oldest
                _audio_queue.task_done()
            except queue.Empty:
                pass
            _audio_queue.put_nowait(bytes(buf))

    if AUDIO_BACKEND == "sounddevice":
        stream = sd.RawInputStream(
            samplerate=RATE, blocksize=CHUNK, channels=CHANNELS, dtype=FORMAT
        )
        with stream:
            while True:
                data, _overflowed = stream.read(CHUNK)
                audio_buffer.extend(bytes(data))
                if len(audio_buffer) >= threshold:
                    _enqueue(audio_buffer)
                    audio_buffer.clear()
    else:
        audio = pyaudio.PyAudio()
        stream = audio.open(
            format=pyaudio.paInt16, channels=CHANNELS, rate=RATE,
            input=True, frames_per_buffer=CHUNK,
        )
        try:
            while True:
                audio_buffer.extend(stream.read(CHUNK, exception_on_overflow=False))
                if len(audio_buffer) >= threshold:
                    _enqueue(audio_buffer)
                    audio_buffer.clear()
        finally:
            stream.stop_stream()
            stream.close()
            audio.terminate()


def connect_vps(url=VPS_URL):
    """Attempts initial connection to the Cloud Streaming Hub.

    Uses _connect_lock so this is safe to call from multiple threads.
    """
    with _connect_lock:
        if sio.connected:
            return True
        try:
            sio.connect(url)
            return True
        except Exception as e:
            print(f"Could not reach VPS server ({e}). Running local capture anyway...")
            return False


def start_connection_manager(url=VPS_URL):
    """Background thread that keeps the VPS connection alive.

    Checks every 5 seconds and reconnects if disconnected.  _connect_lock
    prevents this thread and connect_vps() from calling sio.connect()
    concurrently, which would leave the socket in an undefined state.
    """
    def _manager():
        while True:
            time.sleep(5)
            if not sio.connected:
                with _connect_lock:
                    if not sio.connected:  # double-checked inside the lock
                        try:
                            sio.connect(url)
                        except Exception:
                            pass  # will retry in 5 s
    t = threading.Thread(target=_manager, daemon=True)
    t.start()
    return t


def main():
    if not STREAM_KEY:
        print(
            "WARNING: no stream key configured - the VPS will reject every\n"
            "         verse with '[Auth]: Rejected'. Put the DABARSTREAM_KEY\n"
            "         value in stream_key.txt next to client.py, or set the\n"
            "         DABARSTREAM_KEY environment variable."
        )
    connect_vps(VPS_URL)
    start_connection_manager(VPS_URL)

    # Optional global hotkeys (1=English, 2=Bemba, 3=Nyanja, 4=Tonga)
    start_hotkey_listener()

    print("Loading optimized speech model into CPU registers...")
    model = WhisperModel(MODEL_SIZE, device="cpu", compute_type=COMPUTE_TYPE)

    # Start the Whisper worker *after* the model is loaded so the thread
    # always has a valid model reference and never starts with None.
    worker = threading.Thread(target=_transcription_worker, args=(model,), daemon=True)
    worker.start()

    print(f"Audio backend: {AUDIO_BACKEND}")
    print(
        f"\nListening live. Ready for Scripture cues... "
        f"(active translation: {ACTIVE_LANG})"
    )
    try:
        capture_loop(model)
    except KeyboardInterrupt:
        print("\nHalting client process cleanly.")


if __name__ == "__main__":
    main()
