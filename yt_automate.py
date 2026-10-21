import webbrowser
from time import sleep
from speech_engine import speak

def takecommand():
    from input_engine import listen

    return listen()

def _press(key: str) -> None:
    try:
        import keyboard

        keyboard.press(key)
    except Exception as exc:
        print(f"[YouTube] Could not press {key}: {exc}")
        speak("I could not control the video. Click the YouTube window first.")

def _click(x: int, y: int) -> None:
    from pyautogui import click

    click(x=x, y=y)

def youtube_search():

    query = takecommand().lower()

    speak("OK Sir , This Is What I found For Your Search!")
    query = query.replace("Search","")
    query = query.replace("In","")
    query = query.replace("youtube","")
    web = 'https://www.youtube.com/results?search_query=' + query
    webbrowser.open(web)
    speak("Done Sir!")

def pause_video():
    speak("Ok Sir, I am Pausing video")
    _press("space")
    speak("Sir video paused")

def resume_video():
    speak("Ok Sir, I am resumeing video")
    _press("space")
    speak("Sir, video resumed")

def skip_10_seconds_forward():
    speak("OK Sir, Skiping the video ten seconds forward")
    _press("l")
    speak("Sir, I had skipped the video 10 seconds forward")    

def rewind_10_seconds_back():
    speak("ok Sir, i am rewinding the video")
    _press("j")
    speak("Sir, video rewinded")

def skip_ad():
    speak("Wait Sir, i am Skipping the add")
    _click(830, 535)
    speak("Sir, i had skipped the video")   

def like_video():
    speak("Wait Sir, I am liking")
    _click(473, 704)
    speak("Sir, I liked the video")

def fullscreen():
    speak("Ok Sir, playing video in full screen")
    _press("f")
    speak("Sir, done")

def therather_mode():
    speak("Ok Sir, playing video in thearther mode")
    _press("t")
    speak("Sir, Done")
