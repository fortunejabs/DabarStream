"""
DabarStream - Local Windows Capture Client
Captures microphone audio, transcribes locally on CPU with faster-whisper
(INT8), detects scripture references, and emits them to the cloud VPS.
"""

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


# Regex trigger scanning spoken language for scripture coordinates
verse_pattern = re.compile(
    r"\b([A-Za-z-\s]+?)(?:\s+chapter)?\s+(\d+)(?:\s*:\s*|\s+verse\s+|\s+)(\d+)",
    re.IGNORECASE,
)


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
        sio.emit("set_language", {"lang": lang_code})
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

            match = verse_pattern.search(text)
            if match:
                book, chapter, verse = match.groups()
                book_cleaned = " ".join(book.split())
                print(f"Trigger Detected -> {book_cleaned} {chapter}:{verse} [{active_lang}]")
                sio.emit(
                    "verse_triggered",
                    {
                        "book": book_cleaned,
                        "chapter": chapter,
                        "verse": verse,
                        "lang": active_lang,
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
