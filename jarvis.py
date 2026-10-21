"""JARVIS desktop assistant.

    python jarvis.py          typed commands, spoken replies
    python jarvis.py --voice  microphone, with typing as a fallback
"""

from __future__ import annotations

import os
import sys
import urllib.parse
import webbrowser

for _stream in (sys.stdout, sys.stderr):
    _reconfigure = getattr(_stream, "reconfigure", None)
    if _reconfigure:
        try:
            _reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

import config  # noqa: F401
from input_engine import listen
from main import WishMe, date, introduce, jokes, screenshot, time
from main import activate_how_to_do_mode, hide, unhide
from memory import add_memory, extract_remember_payload, get_all_memories, get_memory_context, init_memory
from nim_client import get_nim_response
from speech_engine import continue_speaking, should_ignore_query, speak, stop_speaking
from weather import weather


HELP = """
Commands you can type or say:
  what time is it
  what is the date
  tell me a joke
  weather in Cape Town
  wikipedia black holes
  search google for python tutorials
  play Duze by De Mthunda
  play a song on Spotify
  open notepad / chrome / edge / calculator / spotify
  close chrome
  increase volume / decrease volume / mute audio
  what is the cpu usage
  what is the ram usage
  take screenshot
  remember that my name is ...
  what do you remember
  how to tie a tie
  help
  exit
Anything else is sent to the chat model.
""".strip()


def _asks_for_time(query: str) -> bool:
    phrases = (
        "what time",
        "what is the time",
        "what's the time",
        "whats the time",
        "current time",
        "tell me the time",
        "time is it",
    )
    return query in {"time", "the time"} or any(phrase in query for phrase in phrases)


def _asks_for_date(query: str) -> bool:
    phrases = (
        "what is the date",
        "what's the date",
        "whats the date",
        "what date",
        "current date",
        "today's date",
        "todays date",
        "what day is it",
        "what's today",
        "whats today",
    )
    return query in {"date", "the date"} or any(phrase in query for phrase in phrases)


def _city_from_weather_query(query: str) -> str:
    lowered = query.lower()
    for marker in ("weather in ", "weather for ", "forecast in ", "forecast for "):
        if marker in lowered:
            return query[lowered.index(marker) + len(marker) :].strip(" ?.")
    if lowered.strip() in {
        "weather",
        "what is the weather",
        "what's the weather",
        "what is the weather forecast",
        "whats the weather",
    }:
        return ""
    return ""


def _wiki(topic: str) -> None:
    topic = topic.replace("search", "").strip(" ?.")
    if not topic:
        speak("What should I look up on Wikipedia?")
        return
    url = "https://en.wikipedia.org/api/rest_v1/page/summary/" + urllib.parse.quote(topic.replace(" ", "_"))
    try:
        import json
        import urllib.request

        request = urllib.request.Request(url, headers={"User-Agent": "JARVIS/1.0 (desktop assistant)"})
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
        summary = payload.get("extract") or "I could not find a summary for that."
    except Exception as exc:
        print(f"[Wikipedia] {exc}")
        summary = "I could not reach Wikipedia."
    print(summary)
    speak(summary, blocking=False)


def _open_search(url: str, spoken: str) -> None:
    webbrowser.open(url)
    speak(spoken)


def _clean_song(text: str) -> tuple[str, bool]:
    on_youtube = "youtube" in text
    for noise in (
        "on spotify",
        "in spotify",
        "from spotify",
        "on youtube",
        "in youtube",
        "on you tube",
    ):
        text = text.replace(noise, " ")
    text = " ".join(text.split())
    if text.startswith("play "):
        text = text[5:].strip()
    elif text == "play":
        text = ""
    return text, on_youtube


def _song_title(query: str) -> tuple[str, bool] | None:
    """Return (title, on_youtube). None when this is not a play command."""
    if query != "play" and not query.startswith("play "):
        return None
    return _clean_song(query)


def _play(query: str) -> bool:
    parsed = _song_title(query)
    if parsed is None:
        return False
    title, on_youtube = parsed
    generic = {"", "a song", "song", "music", "something", "anything", "some music", "a track"}
    if title in generic:
        if title == "" and not on_youtube:
            from pcautomati0n import resume_playback

            resume_playback()
            return True
        speak("Which song should I play?")
        heard = listen().strip()
        if not heard or heard == "none":
            speak("I did not catch the song.")
            return True
        title, heard_youtube = _clean_song(heard.lower())
        on_youtube = on_youtube or heard_youtube
        title = title.strip()
    if not title or title in generic:
        speak("Which song should I play?")
        return True
    if on_youtube:
        _open_search(
            "https://www.youtube.com/results?search_query=" + urllib.parse.quote(title),
            f"Playing {title} on YouTube.",
        )
        return True
    from pcautomati0n import play_spotify_song

    play_spotify_song(title)
    return True


def _simplify_command(query: str) -> str:
    """Drop filler words so 'open my spotify' matches 'open spotify'."""
    fillers = {"my", "the", "a", "an", "please", "me"}
    words = [word for word in query.split() if word not in fillers]
    text = " ".join(words)
    for prefix in ("launch ", "start "):
        if text.startswith(prefix):
            text = "open " + text[len(prefix) :]
    return text


def _pc(query: str) -> bool:
    try:
        import pcautomati0n as pc
    except Exception as exc:
        print(f"[PC] {exc}")
        speak("PC controls are missing a required library.")
        return True

    actions = {
        "what is the ram usage": pc.ram_usage,
        "what is the cpu usage": pc.cpu,
        "refresh this tab": pc.refresh,
        "save this file": pc.savefile,
        "select all": pc.selectall,
        "copy this": pc.copy,
        "paste it here": pc.paste,
        "open taskmanager": pc.taskmanager,
        "open task manager": pc.taskmanager,
        "make a new folder here": pc.newfolder,
        "open notepad": pc.notepad,
        "open chrome": pc.chrome,
        "open edge": pc.edge,
        "open browser": pc.edge,
        "open calculator": pc.calculator,
        "open file explorer": pc.explorer,
        "open vlc media player": pc.vlcmediaplayer,
        "open epic games store": pc.epicgamesstore,
        "open obs studio": pc.obsstudio,
        "open blender": pc.blender,
        "open tlauncher": pc.tlauncher,
        "open opera": pc.opera,
        "open spotify": pc.spotify,
        "switch the window": pc.switchthewindow,
        "increase volume": pc.volume_up,
        "decrease volume": pc.volume_down,
        "mute audio": pc.mute_audio,
        "unmute audio": pc.unmute_audio,
        "close windows media player": pc.close_windowsmediaplayer,
        "close chrome": pc.close_chrome,
        "close opera": pc.close_opera,
        "close visual code": pc.close_vscode,
        "close discord": pc.close_discord,
        "close fire fox": pc.close_firefox,
        "close firefox": pc.close_firefox,
        "close blender": pc.close_blender,
        "close minecraft": pc.close_minecraft,
        "close vlc media player": pc.close_vlcmediaplayer,
    }
    simplified = _simplify_command(query)
    for phrase, action in actions.items():
        if phrase in simplified:
            action()
            return True
    if query in {"close this", "close this window"}:
        pc.close()
        return True
    return False


def handle_query(query: str) -> str:
    """Run one command. Returns 'exit' when the assistant should stop."""
    query = query.lower().strip()
    if not query or query == "none":
        return ""
    if should_ignore_query(query):
        return ""

    stop_speaking()

    if query in {"exit", "quit", "goodbye", "good bye", "shutdown", "shut down", "go to sleep"}:
        speak("Goodbye.")
        return "exit"

    if query in {"stop", "pause", "shut up"}:
        speak("Stopped.")
        return ""

    if query in {"continue", "resume"}:
        if not continue_speaking():
            speak("Nothing to continue.")
        return ""

    if query in {"help", "what can you do", "commands"}:
        print(HELP)
        speak("I can tell the time, check the weather, open programs, play music, and answer questions. The full list is on screen.")
        return ""

    if query in {"who are you", "what is your name", "what's your name", "introduce yourself"}:
        introduce()
        return ""

    if "what do you remember" in query or query in {"recall", "show memory"}:
        memories = get_all_memories()
        if not memories:
            speak("My memory is empty. Tell me to remember something.")
            return ""
        speak(f"I have {len(memories)} things in memory.")
        for item in memories[:5]:
            print(item)
            speak(item)
        if len(memories) > 5:
            speak(f"And {len(memories) - 5} more.")
        return ""

    if "remember" in query:
        payload = extract_remember_payload(query)
        if not payload:
            speak("What should I remember?")
            payload = listen()
        if payload and payload != "none":
            add_memory(payload)
            speak("I will remember that.")
        else:
            speak("I did not catch that.")
        return ""

    if _asks_for_time(query):
        time()
        return ""

    if _asks_for_date(query):
        date()
        return ""

    if "joke" in query.split() or query.startswith("joke"):
        jokes()
        return ""

    if "weather" in query or "forecast" in query:
        weather(_city_from_weather_query(query))
        return ""

    if "wikipedia" in query:
        _wiki(query.replace("wikipedia", ""))
        return ""

    if "screenshot" in query:
        screenshot()
        return ""

    if "how to" in query or "how do i" in query:
        topic = query.replace("turn on how to do", "").replace("how do i", "").replace("how to", "").strip()
        activate_how_to_do_mode(topic)
        return ""

    if "hide your files" in query or query == "hide files":
        hide()
        return ""

    if "unhide" in query:
        unhide()
        return ""

    if "search in youtube" in query or query.startswith("youtube "):
        topic = query.replace("search in youtube", "").replace("youtube", "").strip()
        if not topic:
            speak("What should I search on YouTube?")
            topic = listen()
        if topic and topic != "none":
            _open_search(
                "https://www.youtube.com/results?search_query=" + urllib.parse.quote(topic),
                "This is what I found on YouTube.",
            )
        return ""

    if "search" in query and ("google" in query or "search for" in query or query.startswith("search ")):
        topic = query.replace("search in google for", "").replace("search google for", "")
        topic = topic.replace("search in google", "").replace("search for", "").replace("google", "")
        topic = topic.replace("search", "").strip()
        if topic:
            _open_search(
                "https://www.google.com/search?q=" + urllib.parse.quote(topic),
                "This is what I found.",
            )
            return ""

    if _play(query):
        return ""

    if _pc(query):
        return ""

    try:
        import yt_automate as yt
    except Exception:
        yt = None
    if yt is not None:
        youtube_actions = {
            "pause video": yt.pause_video,
            "resume video": yt.resume_video,
            "skip 10 seconds forward": yt.skip_10_seconds_forward,
            "rewind 10 seconds back": yt.rewind_10_seconds_back,
            "fullscreen mode": yt.fullscreen,
            "theater mode": yt.therather_mode,
            "therather mode": yt.therather_mode,
        }
        for phrase, action in youtube_actions.items():
            if phrase in query:
                action()
                return ""

    memory_context = get_memory_context(query)
    response = get_nim_response(query, memory_context=memory_context)
    print(f"Fallback AI: {response}")
    speak(response, blocking=False)
    return ""


def run() -> None:
    if "--voice" in sys.argv:
        os.environ["JARVIS_INPUT"] = "voice"
    init_memory()
    WishMe()
    print("Type 'help' for commands, or 'exit' to quit.")
    if os.environ.get("JARVIS_INPUT") == "voice":
        print("Voice is on. If the microphone fails, type the command instead.")
    while True:
        query = listen()
        if query == "none":
            continue
        if handle_query(query) == "exit":
            break


if __name__ == "__main__":
    run()
