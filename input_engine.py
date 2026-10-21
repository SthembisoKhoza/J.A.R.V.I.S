"""Command input for JARVIS.

Typed commands always work. Voice is used when JARVIS is started with
--voice, and falls back to typing if the microphone is unavailable.
"""

from __future__ import annotations

import os

_voice_failed = False


def _record_phrase(timeout: float = 6, phrase_limit: float = 12) -> bytes:
    """Record one spoken phrase from the default microphone."""
    import time

    import numpy as np
    import sounddevice as sd

    sample_rate = 16000
    block = int(0.1 * sample_rate)
    ambient = sd.rec(int(0.4 * sample_rate), samplerate=sample_rate, channels=1, dtype="int16")
    sd.wait()
    noise = int(np.percentile(np.abs(ambient), 90))
    threshold = max(int(os.environ.get("JARVIS_SR_ENERGY", "250")), noise * 3)

    chunks: list = []
    started = False
    silence_blocks = 0
    deadline = time.time() + timeout
    max_blocks = int(phrase_limit / 0.1)

    while len(chunks) < max_blocks:
        if not started and time.time() > deadline:
            raise TimeoutError("no speech")
        data = sd.rec(block, samplerate=sample_rate, channels=1, dtype="int16")
        sd.wait()
        level = int(np.max(np.abs(data)))
        if level >= threshold:
            started = True
            silence_blocks = 0
            chunks.append(data.copy())
        elif started:
            chunks.append(data.copy())
            silence_blocks += 1
            if silence_blocks >= 8:
                break

    if not chunks:
        raise TimeoutError("no speech")
    return np.concatenate(chunks).tobytes()


def _listen_voice() -> str:
    global _voice_failed
    if _voice_failed:
        return ""
    try:
        import speech_recognition as sr
    except Exception as exc:
        print(f"[Voice] Speech recognition is not installed ({exc}). Type commands instead.")
        _voice_failed = True
        return ""

    recognizer = sr.Recognizer()
    try:
        print("Listening...")
        frames = _record_phrase()
        audio = sr.AudioData(frames, 16000, 2)
        print("Recognizing....")
        query = recognizer.recognize_google(audio, language="en-US")
        print(f"You: {query}")
        if len(query.strip()) < 2:
            return "none"
        return query
    except TimeoutError:
        return "none"
    except Exception as exc:
        name = type(exc).__name__
        if name == "UnknownValueError":
            return "none"
        print(f"[Voice] Microphone unavailable ({exc}). Type commands instead.")
        _voice_failed = True
        return ""


def listen() -> str:
    """Return the next command, or 'none' when nothing was captured."""
    if os.environ.get("JARVIS_INPUT", "text") == "voice":
        heard = _listen_voice()
        if heard:
            return heard

    try:
        typed = input("You: ").strip()
    except EOFError:
        return "exit"
    return typed or "none"
