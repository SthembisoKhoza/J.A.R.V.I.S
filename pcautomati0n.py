import os
import platform
import subprocess
from time import sleep

from speech_engine import speak

IS_MAC = platform.system() == "Darwin"
MOD = "command" if IS_MAC else "ctrl"


def _existing(paths: list[str]) -> str:
    import shutil

    for path in paths:
        expanded = os.path.expandvars(path)
        if expanded and os.path.isfile(expanded):
            return expanded
        found = shutil.which(expanded) if expanded else None
        if found:
            return found
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


_installed_apps = None


def _skip_app(name: str) -> bool:
    lowered = name.lower()
    blocked = ("uninstall", "uninstaller", "readme", "documentation", "what's new", "release notes")
    return any(word in lowered for word in blocked)


def _installed_catalog() -> list[tuple[str, str, str]]:
    """Return installed apps as (name, target, kind)."""
    global _installed_apps
    if _installed_apps is not None:
        return _installed_apps

    from pathlib import Path

    catalog: list[tuple[str, str, str]] = []
    roots = [
        Path(os.environ.get("PROGRAMDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs",
        Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs",
        Path.home() / "Desktop",
    ]
    for root in roots:
        if not root.exists():
            continue
        for shortcut in root.rglob("*.lnk"):
            if _skip_app(shortcut.stem):
                continue
            catalog.append((shortcut.stem, str(shortcut), "lnk"))

    try:
        listing = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-WindowStyle",
                "Hidden",
                "-Command",
                "Get-StartApps | ForEach-Object { $_.Name + \"`t\" + $_.AppID }",
            ],
            capture_output=True,
            text=True,
            timeout=25,
            check=False,
        )
        for line in listing.stdout.splitlines():
            if "\t" not in line:
                continue
            name, app_id = line.split("\t", 1)
            name = name.strip()
            app_id = app_id.strip()
            if name and app_id and not _skip_app(name):
                catalog.append((name, app_id, "store"))
    except Exception as exc:
        print(f"[PC] Could not list Start apps: {exc}")

    _installed_apps = catalog
    print(f"[PC] Found {len(catalog)} installed apps")
    return catalog


def _match_app(request: str):
    query = " ".join(request.lower().split())
    if not query:
        return None
    best = None
    best_score = 0
    for name, target, kind in _installed_catalog():
        label = name.lower()
        if query == label:
            score = 100
        elif label.startswith(query):
            score = 85
        elif all(word in label for word in query.split()):
            score = 70
        elif query in label:
            score = 50
        else:
            score = 0
        if score == 0:
            continue
        score += max(0, 15 - abs(len(label) - len(query)))
        if kind == "lnk":
            score += 2
        if score > best_score:
            best_score = score
            best = (name, target, kind)
    return best


def _shortcut_target(path: str) -> tuple[str, str]:
    """Return the program and arguments a Start menu shortcut points to."""
    try:
        listing = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-WindowStyle",
                "Hidden",
                "-Command",
                (
                    "$s = (New-Object -ComObject WScript.Shell).CreateShortcut("
                    f"'{path.replace(chr(39), chr(39)*2)}'); "
                    "Write-Output $s.TargetPath; Write-Output $s.Arguments"
                ),
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        lines = [line.strip() for line in listing.stdout.splitlines() if line.strip()]
        if lines:
            return lines[0], lines[1] if len(lines) > 1 else ""
    except Exception as exc:
        print(f"[PC] Could not read shortcut: {exc}")
    return "", ""


def _process_names() -> set[str]:
    import psutil

    names = set()
    for proc in psutil.process_iter(["name"]):
        name = (proc.info.get("name") or "").lower()
        if name:
            names.add(name)
    return names


def _running(names: list[str]) -> bool:
    if not names:
        return False
    wanted = {name.lower() for name in names}
    return bool(wanted & _process_names())


def _wait_for_process(names: list[str], timeout: float = 8.0) -> bool:
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        if _running(names):
            return True
        time.sleep(0.4)
    return _running(names)


def _existing_path(paths: list[str]) -> str:
    return _existing(paths)


def _install_is_dead(paths: list[str]) -> bool:
    for path in paths:
        expanded = os.path.expandvars(path)
        if not expanded or os.path.isfile(expanded):
            continue
        if os.path.isfile(os.path.join(os.path.dirname(expanded), ".dead")):
            return True
    return False


def _start_program(program: str, arguments: str = "") -> None:
    command = [program]
    if arguments:
        command.extend(arguments.split())
    subprocess.Popen(command, cwd=os.path.dirname(program) or None)


def _launch_failure(name: str, exc: OSError) -> tuple[str, bool]:
    print(f"[PC] {exc}")
    if getattr(exc, "winerror", None) == 1223:
        return f"Windows blocked {name}.", False
    return f"{name} did not start.", False


def _bring_forward(processes: list[str]) -> bool:
    if not processes:
        return False
    return _focus_process(processes[0])


def open_result(request: str) -> tuple[str, bool]:
    """Open an allowlisted app. Return the sentence to speak and whether it worked."""
    from apps import find_allowed

    entry = find_allowed(request)
    if entry is None:
        shown = " ".join(request.split()) or "that"
        return f"I am not allowed to open {shown}.", False
    try:
        return _open_entry(entry)
    except OSError as exc:
        return _launch_failure(entry["name"], exc)
    except Exception as exc:
        print(f"[PC] {exc}")
        return f"I could not open {entry['name']}.", False


def open_named(request: str) -> bool:
    """Open an allowlisted app and say whether it actually opened."""
    message, ok = open_result(request)
    speak(message)
    return ok


def _open_entry(entry: dict) -> tuple[str, bool]:
    name = entry["name"]
    processes = list(entry.get("process") or [])
    paths = list(entry.get("paths") or [])
    always_new = any(path.lower().endswith("explorer.exe") for path in paths)
    if processes and _running(processes) and not always_new:
        if _bring_forward(processes):
            return f"{name} is already open.", True
        return f"{name} is already open, but I could not bring it forward.", True

    program = _existing_path(paths)
    if program:
        if always_new:
            return _open_explorer(name)
        _start_program(program)
        expected = processes or [os.path.basename(program)]
        if _wait_for_process(expected):
            _bring_forward(expected)
            return f"Opened {name}.", True
        return f"{name} did not start.", False

    if paths and _install_is_dead(paths):
        return f"{name} needs to be reinstalled. Its shortcut is still there, but the program files are gone.", False

    app_id = entry.get("app_id") or ""
    if app_id:
        subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{app_id}"])
        if processes and _wait_for_process(processes, timeout=15):
            _bring_forward(processes)
            return f"Opened {name}.", True
        if not processes:
            return f"I asked Windows to open {name}, but I could not confirm it started.", False
        return f"{name} did not start.", False

    target = entry.get("target") or ""
    kind = entry.get("kind") or ""
    if target:
        return _open_catalog_target(name, target, kind, processes)

    if paths:
        return f"{name} is allowed, but it is not installed.", False
    return f"{name} is allowed, but I could not find it.", False


def _open_explorer(name: str) -> tuple[str, bool]:
    before = _window_class_count("CabinetWClass")
    subprocess.Popen(["explorer.exe"])
    import time

    deadline = time.time() + 5
    while time.time() < deadline:
        if _window_class_count("CabinetWClass") > before:
            return f"Opened {name}.", True
        time.sleep(0.3)
    if _window_class_count("CabinetWClass") > before:
        return f"Opened {name}.", True
    return f"{name} did not open.", False


def _open_catalog_target(name: str, target: str, kind: str, processes: list[str]) -> tuple[str, bool]:
    if kind == "lnk":
        program, arguments = _shortcut_target(target)
        if program and os.path.isfile(program):
            _start_program(program, arguments)
            expected = processes or [os.path.basename(program)]
            if _wait_for_process(expected):
                _bring_forward(expected)
                return f"Opened {name}.", True
            return f"{name} did not start.", False
        if program and os.path.isfile(os.path.join(os.path.dirname(program), ".dead")):
            return f"{name} needs to be reinstalled. Its shortcut is still there, but the program files are gone.", False
        return f"{name} is allowed, but its program files are missing.", False
    subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{target}"])
    if processes and _wait_for_process(processes):
        _bring_forward(processes)
        return f"Opened {name}.", True
    return f"I asked Windows to open {name}, but I could not confirm it started.", False


def _window_class_count(class_name: str) -> int:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    total = 0

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd, _lparam):
        nonlocal total
        if not user32.IsWindowVisible(hwnd):
            return True
        buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, buf, 256)
        if buf.value == class_name:
            total += 1
        return True

    user32.EnumWindows(callback, 0)
    return total


def _focus_process(process_name: str) -> bool:
    hwnd = _hwnd_for_process(process_name)
    if not hwnd:
        return False
    return _focus_hwnd(hwnd)


def _hwnd_for_process(process_name: str) -> int | None:
    import ctypes
    from ctypes import wintypes

    import psutil

    wanted = process_name.lower()
    pids = {
        proc.info["pid"]
        for proc in psutil.process_iter(["pid", "name"])
        if (proc.info["name"] or "").lower() == wanted
    }
    if not pids:
        return None
    user32 = ctypes.windll.user32
    matches: list[tuple[int, int]] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd, _lparam):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value not in pids:
            return True
        if not user32.IsWindowVisible(hwnd) and not user32.IsIconic(hwnd):
            return True
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        area = max(0, rect.right - rect.left) * max(0, rect.bottom - rect.top)
        if user32.IsIconic(hwnd):
            area += 10_000_000
        matches.append((area, int(hwnd)))
        return True

    user32.EnumWindows(callback, 0)
    if not matches:
        return None
    return max(matches)[1]


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

def _spotify_hwnd() -> int | None:
    """Largest visible Spotify window, including the minimized player."""
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
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value not in pids:
            return True
        if not user32.IsWindowVisible(hwnd) and not user32.IsIconic(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        area = max(0, rect.right - rect.left) * max(0, rect.bottom - rect.top)
        if user32.IsIconic(hwnd):
            area += 10_000_000
        matches.append((area, int(hwnd)))
        return True

    user32.EnumWindows(callback, 0)
    if not matches:
        return None
    return max(matches)[1]


def _window_title(hwnd: int) -> str:
    import ctypes

    user32 = ctypes.windll.user32
    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value.strip()


def spotify_track() -> str:
    """Artist and song from Spotify's window, without bringing Spotify forward."""
    hwnd = _spotify_hwnd()
    if not hwnd:
        return ""
    title = _window_title(hwnd)
    if not title or title.lower() in {"spotify", "spotify free", "spotify premium"}:
        return ""
    return title


def _focus_hwnd(hwnd: int) -> bool:
    """Bring a window forward even when JARVIS currently has the keyboard."""
    import ctypes
    import time

    user32 = ctypes.windll.user32
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)
    user32.keybd_event(0x12, 0, 0, 0)  # Alt lets Windows accept the focus change
    time.sleep(0.05)
    user32.SwitchToThisWindow(hwnd, True)
    user32.SetForegroundWindow(hwnd)
    user32.BringWindowToTop(hwnd)
    time.sleep(0.1)
    user32.keybd_event(0x12, 0, 2, 0)
    time.sleep(0.15)
    return user32.GetForegroundWindow() == hwnd


def _chord(*keys: int) -> None:
    import ctypes
    import time

    user32 = ctypes.windll.user32
    for key in keys:
        user32.keybd_event(key, 0, 0, 0)
        time.sleep(0.02)
    for key in reversed(keys):
        user32.keybd_event(key, 0, 2, 0)
        time.sleep(0.02)


def _copy_text(text: str) -> bool:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalFree.argtypes = [ctypes.c_void_p]
    user32.SetClipboardData.argtypes = [wintypes.UINT, ctypes.c_void_p]
    user32.SetClipboardData.restype = ctypes.c_void_p
    if not user32.OpenClipboard(None):
        return False
    try:
        user32.EmptyClipboard()
        data = (text + "\0").encode("utf-16-le")
        handle = kernel32.GlobalAlloc(0x0002, len(data))
        if not handle:
            return False
        locked = kernel32.GlobalLock(handle)
        if not locked:
            kernel32.GlobalFree(handle)
            return False
        ctypes.memmove(locked, data, len(data))
        kernel32.GlobalUnlock(handle)
        if not user32.SetClipboardData(13, handle):
            kernel32.GlobalFree(handle)
            return False
        return True
    except Exception:
        return False
    finally:
        user32.CloseClipboard()


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
    open_named("spotify")


def resume_playback():
    """Resume the current media session and say what actually happened."""
    from playback import media_play

    speak(media_play())


def _launch_spotify() -> None:
    app_id = os.environ.get("JARVIS_SPOTIFY_APPID", "SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify")
    subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{app_id}"])


def play_spotify_song(title: str) -> str:
    """Search Spotify and report the track that actually started."""
    try:
        return _play_spotify_song(title)
    except Exception as exc:
        print(f"[PC] {exc}")
        return f"I could not start {title}."


def _play_spotify_song(title: str) -> str:
    import time

    from playback import matches_request, media_status, track_label

    current = media_status()
    if matches_request(title, current) and current.get("status") == "playing":
        label = track_label(current)
        source = current.get("source") or ""
        if source and source.lower() not in label.lower():
            return f"Already playing {label} on {source}."
        return f"Already playing {label}."

    hwnd = _spotify_hwnd()
    launched = hwnd is None
    if launched:
        _launch_spotify()
        for _ in range(40):
            time.sleep(0.5)
            hwnd = _spotify_hwnd()
            if hwnd is not None:
                break
    if hwnd is None:
        return "I could not find Spotify."
    if launched:
        time.sleep(2)

    focused = False
    for _ in range(5):
        if _focus_hwnd(hwnd):
            focused = True
            break
        time.sleep(0.3)
    if not focused:
        return "Spotify is open, but I could not control its window."

    time.sleep(0.4)
    _chord(0x11, 0x4B)  # Ctrl+K, Spotify search
    time.sleep(0.5)
    _chord(0x11, 0x41)  # Ctrl+A
    time.sleep(0.1)
    if _copy_text(title):
        _chord(0x11, 0x56)  # Ctrl+V
    else:
        import pyautogui

        pyautogui.typewrite(title, interval=0.03)
    time.sleep(1.8)
    _chord(0x10, 0x0D)  # Shift+Enter plays the top result
    time.sleep(0.6)
    import ctypes

    user32 = ctypes.windll.user32
    user32.ShowWindow(hwnd, 6)  # keep Spotify in the background
    jarvis = user32.FindWindowW(None, "J.A.R.V.I.S")
    if jarvis:
        _focus_hwnd(jarvis)

    latest = current
    for _ in range(4):
        latest = media_status()
        if matches_request(title, latest) and latest.get("status") == "playing":
            break
        time.sleep(0.8)
    label = track_label(latest)
    if matches_request(title, latest) and latest.get("status") == "playing":
        source = latest.get("source") or ""
        if source:
            return f"Playing {label} on {source}."
        return f"Playing {label}."
    if matches_request(title, latest):
        return f"Spotify found {label}, but it is not playing."
    if label and label != track_label(current):
        return f"That did not start. The current track is {label}."
    return f"I could not start {title}."

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