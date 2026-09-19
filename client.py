"""
DabarStream - Local Windows Capture Client
Captures microphone audio, transcribes locally on CPU with faster-whisper
(INT8), detects scripture references, and emits them to the cloud VPS.
"""

import os
import re

import numpy as np
import pyaudio
import socketio
from faster_whisper import WhisperModel

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
FORMAT = pyaudio.paInt16
CHANNELS = 1
RATE = 16000
CHUNK = 1024

sio = socketio.Client()


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


def main():
    try:
        sio.connect(VPS_URL)
    except Exception as e:
        print(f"Could not reach VPS server: {e}. Running local capture anyway...")

    # Optional global hotkeys (1=English, 2=Bemba, 3=Nyanja, 4=Tonga)
    start_hotkey_listener()

    print("Loading optimized speech model into CPU registers...")
    model = WhisperModel(MODEL_SIZE, device="cpu", compute_type=COMPUTE_TYPE)

    p = pyaudio.PyAudio()
    stream = p.open(
        format=FORMAT, channels=CHANNELS, rate=RATE, input=True, frames_per_buffer=CHUNK
    )

    print(
        f"\nListening live. Ready for Scripture cues... "
        f"(active translation: {ACTIVE_LANG})"
    )
    audio_buffer = bytearray()
    try:
        while True:
            data = stream.read(CHUNK, exception_on_overflow=False)
            audio_buffer.extend(data)
            if len(audio_buffer) >= RATE * 2 * AUDIO_BUFFER_LIMIT:
                process_audio(bytes(audio_buffer), model)
                audio_buffer.clear()
    except KeyboardInterrupt:
        print("\nHalting client process cleanly.")
    finally:
        stream.stop_stream()
        stream.close()
        p.terminate()


if __name__ == "__main__":
    main()
