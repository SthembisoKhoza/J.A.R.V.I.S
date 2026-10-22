"""Text-to-speech for JARVIS.

Uses the Windows speech synthesizer. If KittenTTS is installed and
JARVIS_TTS=kitten, that voice is used instead.
"""

from __future__ import annotations

import os
import re
import subprocess
import threading
from typing import Optional

_lock = threading.Lock()
_listeners: list = []
_current_speech_thread: Optional[threading.Thread] = None
_speech_process: Optional[subprocess.Popen] = None
_stop_requested = False
_remaining_text = ""
_saved_remaining_text = ""
_last_spoken_text = ""


def _clean_for_speech(text: str) -> str:
    text = re.sub(r"[*#_`~|\[\]{}]", " ", text)
    text = re.sub(r"-{2,}", " ", text)
    text = re.sub(r"\s+-\s+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _muted() -> bool:
    return os.environ.get("JARVIS_MUTE", "").strip() in {"1", "true", "yes"}


def _speak_windows(text: str) -> None:
    global _speech_process
    script = (
        "Add-Type -AssemblyName System.Speech; "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        "$s.Rate = 1; "
        "$t = [Console]::In.ReadToEnd(); "
        "if ($t) { $s.Speak($t) }"
    )
    process = subprocess.Popen(
        [
            "powershell",
            "-NoProfile",
            "-WindowStyle",
            "Hidden",
            "-Command",
            script,
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    with _lock:
        _speech_process = process
    try:
        process.communicate(text, timeout=120)
    except subprocess.TimeoutExpired:
        process.kill()
    finally:
        with _lock:
            if _speech_process is process:
                _speech_process = None


def _speak_kitten(text: str) -> bool:
    try:
        import numpy as np
        import sounddevice as sd
        from kittentts import KittenTTS
    except Exception:
        return False

    model_name = os.getenv("JARVIS_TTS_MODEL", "KittenML/kitten-tts-nano-0.8")
    voice = os.getenv("JARVIS_TTS_VOICE", "Hugo")
    speed = float(os.getenv("JARVIS_TTS_SPEED", "1.15"))
    try:
        model = KittenTTS(model_name)
        if voice not in model.available_voices and model.available_voices:
            voice = model.available_voices[0]
        audio = np.asarray(model.generate(text, voice=voice, speed=speed), dtype=np.float32)
        sd.play(audio, 24000, blocking=True)
        return True
    except Exception as exc:
        print(f"[TTS] KittenTTS failed: {exc}")
        return False


def _speech_worker(text: str) -> None:
    global _stop_requested, _remaining_text, _last_spoken_text
    _stop_requested = False
    _remaining_text = text
    _last_spoken_text = text
    try:
        if os.getenv("JARVIS_TTS", "windows").lower() == "kitten":
            if _speak_kitten(text):
                _remaining_text = ""
                return
        if not _stop_requested:
            _speak_windows(text)
        _remaining_text = ""
    except Exception as exc:
        print(f"[TTS] Could not speak: {exc}")
        _remaining_text = ""


def publish(text: str) -> None:
    """Send a line to the screen without speaking it."""
    print(text)
    _notify(text)


def _notify(text: str) -> None:
    for listener in list(_listeners):
        try:
            listener(text)
        except Exception as exc:
            print(f"[UI] {exc}")


def add_listener(listener) -> None:
    """Register a function called with each spoken or published line."""
    _listeners.append(listener)


def speak(audio: object, blocking: bool = True) -> None:
    """Speak text. Always prints the line so the conversation is visible."""
    global _current_speech_thread

    stop_speaking()
    if _current_speech_thread is not None:
        _current_speech_thread.join(timeout=0.3)

    text = _clean_for_speech(str(audio))
    if not text:
        return

    print(f"JARVIS: {text}")
    _notify(text)
    if _muted():
        return

    _current_speech_thread = threading.Thread(
        target=_speech_worker, args=(text,), daemon=True
    )
    _current_speech_thread.start()
    if blocking:
        _current_speech_thread.join()


def stop_speaking() -> None:
    """Stop speech that is currently playing."""
    global _stop_requested, _saved_remaining_text, _remaining_text, _speech_process
    _stop_requested = True
    if _remaining_text:
        _saved_remaining_text = _remaining_text
        _remaining_text = ""
    with _lock:
        process = _speech_process
    if process is not None and process.poll() is None:
        process.kill()
    try:
        import sounddevice as sd

        sd.stop()
    except Exception:
        pass


def continue_speaking() -> bool:
    global _saved_remaining_text
    if not _saved_remaining_text:
        return False
    text = _saved_remaining_text
    _saved_remaining_text = ""
    speak(text, blocking=False)
    return True


def is_speaking() -> bool:
    return _current_speech_thread is not None and _current_speech_thread.is_alive()


def should_ignore_query(query: str) -> bool:
    """Ignore the microphone picking up JARVIS's own voice."""
    if os.environ.get("JARVIS_INPUT", "text") != "voice":
        return False
    if not is_speaking():
        return False

    query_clean = query.lower().strip().rstrip(".!?")
    if not query_clean:
        return True

    control_words = ["stop", "continue", "pause", "shut up", "wait"]
    if any(word in query_clean for word in control_words):
        return False

    if query_clean in _last_spoken_text.lower():
        print(f"[Echo Filter] Ignored self-talk: {query_clean!r}")
        return True
    return False
