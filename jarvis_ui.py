"""HTML window for JARVIS. The page is interface.html; this file feeds it live data."""

from __future__ import annotations

import base64
import json
import os
import queue
import threading
import time

from input_engine import _listen_voice, listen, use_queue
from interface import (
    _adapter_name,
    _local_ip,
    _probe_links,
    _rate,
    _wan_ip,
    _windows_app_id,
)
from jarvis import WishMe, handle_query
from memory import init_memory
from speech_engine import add_listener, is_speaking, speak


_WINDOW = None


class HudApi:
    def __init__(self) -> None:
        self.closing = False
        self.queue: queue.Queue[str] = queue.Queue()
        self._listening = False
        self._art_key = ""
        self._art_url = ""
        use_queue(self.queue)
        add_listener(self._on_line)

    def attach(self, window) -> None:
        global _WINDOW
        _WINDOW = window

    def push(self, message: dict) -> None:
        window = _WINDOW
        if window is None or self.closing:
            return
        try:
            payload = json.dumps(message, ensure_ascii=True)
            window.evaluate_js("window.__jarvis=1;try{HUD.push(" + payload + ")}catch(e){}")
        except Exception as exc:
            print(f"[UI] {exc}")

    def send(self, text: str) -> None:
        command = (text or "").strip()
        if not command or self.closing:
            return
        self.push({"t": "line", "who": "You", "text": command})
        self.queue.put(command)

    def listen_voice(self) -> None:
        if self._listening or self.closing:
            return
        threading.Thread(target=self._listen_work, daemon=True).start()

    def media(self, action: str) -> None:
        threading.Thread(target=self._media_work, args=(action,), daemon=True).start()

    def stop(self) -> None:
        if self.closing:
            return
        self.closing = True
        self.queue.put("exit")

    def _on_line(self, text: str) -> None:
        clean = (text or "").strip()
        if clean:
            self.push({"t": "line", "who": "JARVIS", "text": clean})

    def _listen_work(self) -> None:
        self._listening = True
        self.push({"t": "mic", "busy": True})
        self.push({"t": "status", "v": "LISTENING"})
        try:
            heard = _listen_voice()
        except Exception as exc:
            print(f"[Voice] {exc}")
            heard = ""
        self.push({"t": "mic", "busy": False})
        self._listening = False
        if self.closing:
            return
        if heard and heard not in {"none", ""}:
            self.push({"t": "line", "who": "You", "text": heard})
            self.push({"t": "status", "v": "THINKING"})
            self.queue.put(heard)
            return
        self.push({"t": "line", "who": "JARVIS", "text": "I did not catch that. Type the command, or try the microphone again."})
        self.push({"t": "status", "v": "READY"})

    def _media_work(self, action: str) -> None:
        try:
            from playback import transport

            info = transport(action)
        except Exception as exc:
            print(f"[Playback] {exc}")
            return
        self.push(self._track_message(info))

    def _track_message(self, info: dict) -> dict:
        art_path = info.get("art") or ""
        return {
            "t": "track",
            "title": info.get("title") or "",
            "artist": info.get("artist") or "",
            "state": info.get("state") or "",
            "position": float(info.get("position") or 0),
            "duration": float(info.get("duration") or 0),
            "art": self._cover_url(art_path),
        }

    def _cover_url(self, path: str) -> str:
        if not path or not os.path.isfile(path):
            self._art_key = ""
            self._art_url = ""
            return ""
        try:
            key = f"{path}:{os.path.getmtime(path)}:{os.path.getsize(path)}"
        except OSError:
            return ""
        if key == self._art_key:
            return self._art_url
        mime = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
            ".bmp": "image/bmp",
        }.get(os.path.splitext(path)[1].lower(), "image/jpeg")
        try:
            with open(path, "rb") as handle:
                raw = handle.read(1_500_000)
        except OSError:
            return ""
        self._art_key = key
        self._art_url = f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"
        return self._art_url

    def run_assistant(self) -> None:
        init_memory()
        self.push({"t": "status", "v": "READY"})
        WishMe()
        from reminders import start_reminders

        start_reminders()
        while not self.closing:
            query = listen()
            if self.closing or query == "exit":
                break
            if query == "none":
                continue
            self.push({"t": "status", "v": "THINKING"})
            try:
                result = handle_query(query)
            except Exception as exc:
                speak("Something went wrong while running that command.")
                print(f"[UI] {exc}")
                result = ""
            if self.closing:
                break
            self.push({"t": "status", "v": "READY"})
            if result == "exit":
                break
        self._close_window()

    def watch_speech(self) -> None:
        last = None
        while not self.closing:
            now = is_speaking()
            if now != last:
                last = now
                self.push({"t": "speaking", "v": now})
            time.sleep(0.2)

    def watch_system(self) -> None:
        try:
            import psutil

            psutil.cpu_percent(interval=None)
        except Exception:
            psutil = None
        tick = 0
        ip = "…"
        wan = "…"
        name = "…"
        online = None
        while not self.closing:
            cpu = None
            mem = None
            down = None
            up = None
            started = time.perf_counter()
            if psutil is not None:
                try:
                    before = psutil.net_io_counters()
                    cpu = float(psutil.cpu_percent(interval=1))
                    mem = float(psutil.virtual_memory().percent)
                    after = psutil.net_io_counters()
                    elapsed = max(time.perf_counter() - started, 0.2)
                    down = max(0.0, (after.bytes_recv - before.bytes_recv) / elapsed)
                    up = max(0.0, (after.bytes_sent - before.bytes_sent) / elapsed)
                except Exception:
                    cpu = None
                    mem = None
            else:
                time.sleep(1)
            tick += 1
            if tick == 1 or tick % 5 == 0:
                ip = _local_ip()
                online = bool(ip)
                ip = ip or "OFFLINE"
                name = _adapter_name("" if ip == "OFFLINE" else ip)
            if tick == 1 or tick % 30 == 0:
                found = _wan_ip()
                wan = found or "OFFLINE"
            links = _probe_links() if tick == 1 or tick % 5 == 0 else None
            try:
                from playback import track_view

                info = track_view()
            except Exception:
                info = {}
            try:
                from reminders import screen_state

                reminder = screen_state()
            except Exception:
                reminder = {"items": [], "alert": ""}
            self.push(self._track_message(info))
            self.push({
                "t": "stats",
                "cpu": cpu,
                "mem": mem,
                "links": links,
                "net": {
                    "ip": ip,
                    "wan": wan,
                    "name": name,
                    "online": online,
                    "down": down or 0,
                    "up": up or 0,
                    "down_text": _rate(down),
                    "up_text": _rate(up),
                },
            })
            self.push({"t": "rem", "alert": reminder.get("alert") or "", "items": reminder.get("items") or []})

    def _close_window(self) -> None:
        self.closing = True
        window = _WINDOW
        if window is None:
            return
        try:
            window.destroy()
        except Exception:
            pass


def _screen_origin(width: int, height: int) -> tuple[int, int]:
    try:
        import ctypes

        user32 = ctypes.windll.user32
        screen_w = int(user32.GetSystemMetrics(0))
        screen_h = int(user32.GetSystemMetrics(1))
    except Exception:
        return 80, 16
    return max(0, (screen_w - width) // 2), 16


def _install_crash_guards() -> None:
    """Stop WinForms from showing the unhandled-exception dialog.

    WebView2 raises resource and script callbacks on the UI thread. An exception
    there becomes the Continue / Quit dialog. These wrappers keep that inside Python.
    """
    import clr

    clr.AddReference("System.Windows.Forms")
    from System import Action, Func, Object, String
    from System.Threading.Tasks import Task
    from System.Windows.Forms import Application, UnhandledExceptionMode
    from threading import Semaphore

    from webview.platforms.edgechromium import EdgeChrome
    from webview.platforms.winforms import BrowserView

    def on_thread(sender, args) -> None:
        try:
            text = str(args.Exception)
        except Exception:
            text = "unreadable"
        try:
            with open(os.path.join(os.environ.get("TEMP", "."), "jarvis-ui-error.txt"), "a", encoding="utf-8") as handle:
                handle.write(text + "\n----\n")
        except Exception:
            pass

    try:
        Application.SetUnhandledExceptionMode(UnhandledExceptionMode.CatchException)
    except Exception:
        pass
    try:
        Application.ThreadException += on_thread
    except Exception:
        pass

    original_response = EdgeChrome.on_web_resource_response
    original_request = EdgeChrome.on_web_resource_request

    def safe_response(self, sender, args):
        try:
            if args is None or args.Response is None:
                return
            original_response(self, sender, args)
        except Exception:
            return

    def safe_request(self, sender, args):
        try:
            original_request(self, sender, args)
        except Exception:
            return

    EdgeChrome.on_web_resource_response = safe_response
    EdgeChrome.on_web_resource_request = safe_request

    def safe_theme(self, sender, args):
        def apply():
            try:
                self.update_title_bar_theme()
            except Exception:
                return None
            return None

        try:
            if self.InvokeRequired:
                self.BeginInvoke(Func[Object](apply))
            else:
                apply()
        except Exception:
            return

    BrowserView.BrowserForm.on_system_theme_changed = safe_theme

    def evaluate_js(self, script, parse_json):
        result = None
        semaphore = Semaphore(0)

        def finish(value) -> None:
            nonlocal result
            result = value
            try:
                semaphore.release()
            except Exception:
                pass

        def on_task(task) -> None:
            try:
                raw = json.loads(task.Result)
            except Exception:
                finish(None)
                return
            if parse_json and raw is not None:
                try:
                    finish(json.loads(raw))
                except Exception:
                    finish(raw)
            else:
                finish(raw)

        def start():
            try:
                self.webview.ExecuteScriptAsync(script).ContinueWith(
                    Action[Task[String]](on_task),
                    self.syncContextTaskScheduler,
                )
            except Exception:
                finish(None)
            return None

        try:
            self.webview.Invoke(Func[Object](start))
            semaphore.acquire(timeout=8)
        except Exception:
            finish(None)
        return result

    EdgeChrome.evaluate_js = evaluate_js


def launch() -> None:
    _windows_app_id()
    import webview

    _install_crash_guards()

    api = HudApi()
    here = os.path.dirname(os.path.abspath(__file__))
    page = os.path.join(here, "interface.html")
    width, height = 1160, 720
    origin_x, origin_y = _screen_origin(width, height)
    icon = os.path.join(here, "jarvis.ico")
    window = webview.create_window(
        "J.A.R.V.I.S",
        url=page,
        js_api=api,
        width=width,
        height=height,
        x=origin_x,
        y=origin_y,
        min_size=(1020, 680),
        background_color="#08080a",
        text_select=True,
    )
    api.attach(window)

    def on_loaded() -> None:
        api.push({"t": "status", "v": "READY"})
        threading.Thread(target=api.run_assistant, daemon=True).start()
        threading.Thread(target=api.watch_speech, daemon=True).start()
        threading.Thread(target=api.watch_system, daemon=True).start()

    def on_closing() -> None:
        api.stop()

    window.events.loaded += on_loaded
    window.events.closing += on_closing
    try:
        webview.start(icon=icon if os.path.exists(icon) else None)
    except TypeError:
        webview.start()
