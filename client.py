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
# the server environment. Empty disables auth (local dev only).
STREAM_KEY = os.environ.get("DABARSTREAM_KEY", "")



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
    """
    digit_match = verse_pattern.search(text)
    if digit_match:
        book, chapter, verse = digit_match.groups()
        return " ".join(book.split()), chapter, verse

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
    book = " ".join(book_tokens)
    if not re.search(r"[A-Za-z]", book):
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

            # Voice-activated language switch takes priority over verse detection
            lang_code = detect_language_command(text)
            if lang_code:
                print(f"Language Switch Command -> {lang_code}")
                switch_language(lang_code)
                active_lang = lang_code
                continue

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
