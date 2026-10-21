import os
import platform
import subprocess
from time import sleep

from speech_engine import speak

IS_MAC = platform.system() == "Darwin"
MOD = "command" if IS_MAC else "ctrl"


def _existing(paths: list[str]) -> str:
    for path in paths:
        expanded = os.path.expandvars(path)
        if expanded and os.path.isfile(expanded):
            return expanded
    return ""


def _open_app(mac_app: str, win_path: str = "", extra: list[str] | None = None) -> None:
    if IS_MAC:
        subprocess.run(["open", "-a", mac_app], check=False)
        speak(f"Opening {mac_app}")
        return
    found = _existing(([win_path] if win_path else []) + (extra or []))
    if found:
        os.startfile(found)
        speak(f"Opening {mac_app}")
        return
    subprocess.Popen(["cmd", "/c", "start", "", mac_app], shell=False)
    speak(f"Trying to open {mac_app}")


def _close_app(mac_app: str, win_exe: str = "") -> None:
    if IS_MAC:
        subprocess.run(["osascript", "-e", f'tell application "{mac_app}" to quit'], check=False)
        return
    if win_exe:
        os.system(f"taskkill /f /im {win_exe}")


def _hotkey(*keys: str) -> None:
    combo = "+".join(keys)
    try:
        import keyboard

        keyboard.press_and_release(combo)
        return
    except Exception:
        pass
    try:
        import pyautogui

        pyautogui.hotkey(*keys)
    except Exception as exc:
        print(f"[PC] Could not send {combo}: {exc}")
        speak("I could not send that keyboard shortcut.")


def _tap_volume(kind: str) -> None:
    codes = {"up": 0xAF, "down": 0xAE, "mute": 0xAD}
    if platform.system() == "Windows":
        import ctypes

        code = codes[kind]
        ctypes.windll.user32.keybd_event(code, 0, 0, 0)
        ctypes.windll.user32.keybd_event(code, 0, 2, 0)
        return
    try:
        import pyautogui

        pyautogui.press({"up": "volumeup", "down": "volumedown", "mute": "volumemute"}[kind])
    except Exception as exc:
        print(f"[PC] Volume key failed: {exc}")

def takecommand():
    from input_engine import listen

    return listen()

def ram_usage():
    import psutil

    percent = str(psutil.virtual_memory().percent)
    speak("Current RAM usage is " + percent + " percent")

def cpu():
    import psutil

    usage = str(psutil.cpu_percent(interval=0.4))
    speak("CPU usage is at " + usage + " percent")

def refresh():
    _hotkey(MOD, "r")
    speak("Refreshed")

def pcrefresh():
    _hotkey("f5")
    speak("Refreshed")

def savefile():
    _hotkey(MOD, "s")
    speak("Saved")

def selectall():
    _hotkey(MOD, "a")
    speak("Selected all")

def copy():
    _hotkey(MOD, "c")
    speak("Copied")

def paste():
    _hotkey(MOD, "v")
    speak("Pasted")

def taskmanager():
    if IS_MAC:
        _open_app("Activity Monitor")
    else:
        subprocess.Popen(["taskmgr.exe"])
        speak("Opening Task Manager")

def close():
    if IS_MAC:
        _hotkey("command", "w")
    else:
        _hotkey("alt", "f4")
    speak("Closing this window")

def rename():
    _hotkey("f2")
    speak("Rename is ready")

def newfolder():
    _hotkey(MOD, "shift", "n")
    speak("Creating a new folder")

def notepad():
    _open_app("Notepad", r"C:\Windows\System32\notepad.exe")

def chrome():
    _open_app(
        "Google Chrome",
        extra=[
            r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
            r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
            r"%LocalAppData%\Google\Chrome\Application\chrome.exe",
        ],
    )

def edge():
    _open_app(
        "Microsoft Edge",
        extra=[
            r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
            r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe",
        ],
    )

def calculator():
    if IS_MAC:
        _open_app("Calculator")
        return
    subprocess.Popen(["calc.exe"])
    speak("Opening calculator")

def explorer():
    if IS_MAC:
        _open_app("Finder")
        return
    subprocess.Popen(["explorer.exe"])
    speak("Opening File Explorer")

def vlcmediaplayer():
    _open_app("VLC", extra=[r"%ProgramFiles%\VideoLAN\VLC\vlc.exe", r"D:\VLC\vlc.exe"])

def epicgamesstore():
    _open_app(
        "Epic Games Launcher",
        extra=[
            r"%ProgramFiles(x86)%\Epic Games\Launcher\Portal\Binaries\Win32\EpicGamesLauncher.exe",
            r"%ProgramFiles%\Epic Games\Launcher\Portal\Binaries\Win32\EpicGamesLauncher.exe",
        ],
    )

def obsstudio():
    _open_app("OBS", extra=[r"%ProgramFiles%\obs-studio\bin\64bit\obs64.exe"])

def blender():
    _open_app("Blender", extra=[r"%ProgramFiles%\Blender Foundation\Blender 4.2\blender.exe", r"%ProgramFiles%\Blender Foundation\Blender\blender.exe"])

def tlauncher():
    _open_app("TLauncher", extra=[r"%AppData%\.minecraft\TLauncher.exe"])

def opera():
    _open_app(
        "Opera",
        extra=[
            r"%LocalAppData%\Programs\Opera GX\opera.exe",
            r"%LocalAppData%\Programs\Opera\opera.exe",
        ],
    )

def _spotify_window():
    """Return the main Spotify window, including while a song title is showing."""
    import ctypes
    from ctypes import wintypes

    import psutil

    pids = {
        proc.info["pid"]
        for proc in psutil.process_iter(["pid", "name"])
        if (proc.info["name"] or "").lower() == "spotify.exe"
    }
    if not pids:
        return None

    user32 = ctypes.windll.user32
    matches: list[tuple[int, int]] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value not in pids:
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        area = max(0, rect.right - rect.left) * max(0, rect.bottom - rect.top)
        matches.append((area, int(hwnd)))
        return True

    user32.EnumWindows(callback, 0)
    if not matches:
        return None

    hwnd = max(matches)[1]
    import pygetwindow as gw

    for window in gw.getAllWindows():
        if getattr(window, "_hWnd", None) == hwnd:
            return window
    return None


def spotify():
    if not IS_MAC:
        if _spotify_window() is not None:
            speak("Spotify is already open")
            return
        app_id = os.environ.get("JARVIS_SPOTIFY_APPID", "SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify")
        subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{app_id}"])
        speak("Opening Spotify")
        return
    _open_app("Spotify", extra=[r"%AppData%\Spotify\Spotify.exe"])


def resume_playback():
    """Toggle the system play control, which resumes Spotify when it is the active player."""
    import ctypes

    code = 0xB3  # VK_MEDIA_PLAY_PAUSE
    ctypes.windll.user32.keybd_event(code, 0, 0, 0)
    ctypes.windll.user32.keybd_event(code, 0, 2, 0)
    speak("Playing")


def play_spotify_song(title: str) -> None:
    """Search Spotify and play the top result."""
    import time

    import pyautogui

    window = _spotify_window()
    if window is None:
        app_id = os.environ.get("JARVIS_SPOTIFY_APPID", "SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify")
        subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{app_id}"])
        for _ in range(12):
            time.sleep(0.5)
            window = _spotify_window()
            if window is not None:
                break
    if window is None:
        speak("I could not find Spotify.")
        return

    try:
        if window.isMinimized:
            window.restore()
            time.sleep(0.3)
        window.maximize()
        window.activate()
    except Exception as exc:
        print(f"[Spotify] Could not focus the window: {exc}")

    time.sleep(0.6)
    pyautogui.hotkey("ctrl", "k")
    time.sleep(0.4)
    pyautogui.hotkey("ctrl", "a")
    typed = False
    try:
        import pyperclip

        pyperclip.copy(title)
        pyautogui.hotkey("ctrl", "v")
        typed = True
    except Exception:
        typed = False
    if not typed:
        pyautogui.typewrite(title, interval=0.02)
    time.sleep(1.6)
    pyautogui.hotkey("shift", "enter")
    speak(f"Playing {title} on Spotify")

def open_virtual_box():

    speak("Ok Sir Opeing Virtual Box")

    _open_app("VirtualBox", "G:\\Apps\\Virtual Box\\VirtualBox.exe")

def open_discord():

    speak("Ok Sir Opeing Discord")

    _open_app("Discord", "C:\\Users\\opsri\\AppData\\Local\\Discord\\Update1.exe --processStart Discord.exe")

#def firefox():
    #jpath = 

def switchthewindow():
    speak("Switching windows")
    _hotkey("command" if IS_MAC else "alt", "tab")

def volume_up():
    _tap_volume("up")
    speak("Volume increased")

def volume_down():
    _tap_volume("down")
    speak("Volume decreased")

def mute_audio():
    _tap_volume("mute")
    speak("Audio muted")

def unmute_audio():
    _tap_volume("mute")
    sleep(0.4)
    speak("Audio unmuted")

def close_windowsmediaplayer():
    speak("ok Sir closing media player")
    _close_app("QuickTime Player", "wmplayer.exe")
    speak("Sir Closed media player")

def close_chrome():
    speak("Ok Sir, closing chrome")
    _close_app("Google Chrome", "chrome.exe")
    speak("Sir, Closed Chrome")

def close_opera():
    speak("Ok Sir, Closing opera browser")
    _close_app("Opera", "opera.exe")
    speak("Sir, Closed Opera")

def close_vscode():
    speak("Ok Sir, closing visual studio code")
    _close_app("Visual Studio Code", "code.exe")
    speak("Sir, Closed Visual Studio Code")

def close_discord():
    speak("Ok Sir, Closing Discord")
    _close_app("Discord", "discord.exe")
    speak("Sir, Closed Discord")

def close_aducaiy():
    speak("Ok Sir, Closing audacity")
    _close_app("Audacity", "audacity.exe")
    speak("Sir, Closed Audacity")

def close_firefox():
    speak("Ok Sir, Closing firefox")
    _close_app("Firefox", "firefox.exe")
    speak("Sir, Closed Fire fox")

def close_touchvpn():
    speak("ok Sir, Closing Touch vpn")
    _close_app("TouchVPN", "TouchVpn.exe")
    speak("Sir, closed Touch Vpn")

def close_blender():
    speak("Ok Sir, Closing Blender 3d Softare")
    _close_app("Blender", "blender.exe")
    speak("Sir, Closed Blender")

def close_tlauncher():
    speak("Ok Sir, Closing Tlauncher")
    _close_app("TLauncher", "java.exe")
    speak("Sir, Closed Tlauncher")

def close_minecraft():
    speak("Ok Sir, Closing minecraft")
    _close_app("Minecraft", "minecraft.exe")
    speak("Sir, Closed Minecraft")

def close_d3dgear():
    speak("Ok Sir, Closing D3Dgear")
    _close_app("D3Dgear", "d3dgear.exe")
    speak("Sir, Closed D3DGEAR")

def close_vlcmediaplayer():
    speak("Ok Sir, Closing vlc media player")
    _close_app("VLC", "vlc.exe")
    speak("Sir, closed vlc media player")
        
def close_this_file():
    speak("Closing this program")
    if IS_MAC:
        _hotkey("command", "w")
    else:
        _hotkey("alt", "f4")
    speak("Closed")