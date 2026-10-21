import datetime
import os
import platform
import subprocess
import urllib.parse
import webbrowser
from pathlib import Path

from input_engine import listen
from speech_engine import speak


def take_command():
    return listen()


def WishMe():
    now = datetime.datetime.now()
    hour = now.hour
    if hour < 12:
        greeting = "Good morning"
    elif hour < 18:
        greeting = "Good afternoon"
    else:
        greeting = "Good evening"
    speak(now.strftime(f"{greeting}. It is %I:%M %p on %A, %B %d. JARVIS is online."))


def screenshot(name: str | None = None):
    if not name or name == "none":
        speak("What name should I give the screenshot?")
        name = take_command()
    if not name or name == "none":
        name = "screenshot"

    safe = "".join(char for char in name if char.isalnum() or char in (" ", "-", "_")).strip()
    safe = safe or "screenshot"
    folder = Path.home() / "Pictures" / "JARVIS"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{safe}.png"

    try:
        from PIL import ImageGrab

        ImageGrab.grab().save(path)
    except Exception as exc:
        print(f"[Screenshot] {exc}")
        speak("I could not take a screenshot.")
        return

    print(path)
    speak(f"I saved the screenshot as {safe}")


def date():
    today = datetime.datetime.now().strftime("%A, %B %d, %Y")
    speak(f"Today is {today}")


def time():
    current = datetime.datetime.now().strftime("%I:%M %p")
    speak(f"The time is {current}")


def jokes():
    try:
        import pyjokes

        joke = pyjokes.get_joke()
    except Exception:
        joke = "I told my computer I needed a break, and it said no problem, it already had one."
    print(joke)
    speak(joke, blocking=False)


def hide():
    if platform.system() == "Darwin":
        subprocess.run(
            "find . -maxdepth 1 -mindepth 1 -exec chflags hidden {} +",
            shell=True,
            check=False,
        )
    else:
        os.system("attrib +h /s /d")
    speak("Files in this folder are hidden")


def unhide():
    if platform.system() == "Darwin":
        subprocess.run(
            "find . -maxdepth 1 -mindepth 1 -exec chflags nohidden {} +",
            shell=True,
            check=False,
        )
    else:
        os.system("attrib -h /s /d")
    speak("Files in this folder are visible")


def activate_how_to_do_mode(topic: str | None = None):
    if not topic or topic == "none":
        speak("What do you want to know how to do?")
        topic = take_command()
    if not topic or topic == "none":
        speak("I did not catch the topic.")
        return
    url = "https://www.google.com/search?q=" + urllib.parse.quote(f"how to {topic}")
    webbrowser.open(url)
    speak(f"I opened a how-to search for {topic}")


def reminder_maker():
    from memory import add_memory

    speak("What should I remember?")
    remembered = take_command()
    if remembered and remembered != "none":
        add_memory(remembered)
        speak("Stored. I will remember that.")
    else:
        speak("I did not catch that.")


def introduce():
    speak(
        "I am JARVIS, a desktop assistant. "
        "I can tell you the time, the weather, open programs, play music, and answer questions."
    )
