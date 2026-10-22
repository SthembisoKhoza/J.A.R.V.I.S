"""JARVIS window styled after the circular HUD live wallpaper."""

from __future__ import annotations

import math
import os
import queue
import subprocess
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from datetime import datetime

from input_engine import listen, use_queue
from jarvis import WishMe, handle_query
from memory import init_memory
from speech_engine import add_listener, speak

BG = "#08080a"
PANEL = "#141518"
ICE = "#e8f1ff"
MUTE = "#8a8d94"
TEXT = "#ececef"
WARN = "#ffb347"
OK = "#4dffb0"
ERR = "#ff6b7d"
LINE = "#2c2e33"
FIELD = "#17181b"
CYAN = ICE
DIM = MUTE
SOFT = TEXT
INK = TEXT
OFF = ERR
_STATUS_SPEED = {"READY": 0.45, "LISTENING": 1.5, "THINKING": 1.05, "SPEAKING": 1.7}


def _mix(amount: float) -> str:
    """Blend the dim ring color toward cyan. amount is 0 to 1."""
    amount = max(0.0, min(1.0, amount))
    dim = (11, 96, 116)
    lit = (62, 242, 255)
    parts = [int(dim[i] + (lit[i] - dim[i]) * amount) for i in range(3)]
    return f"#{parts[0]:02x}{parts[1]:02x}{parts[2]:02x}"


def _load_color(percent: float | None) -> str:
    if percent is None:
        return MUTE
    if percent >= 90:
        return ERR
    if percent >= 75:
        return WARN
    return ICE


def _load_fonts() -> None:
    """Register the design fonts for this process only."""
    if os.name != "nt":
        return
    import ctypes

    folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
    if not os.path.isdir(folder):
        return
    gdi = ctypes.windll.gdi32
    gdi.AddFontResourceExW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_void_p]
    gdi.AddFontResourceExW.restype = ctypes.c_int
    for name in os.listdir(folder):
        if name.lower().endswith((".ttf", ".otf")):
            gdi.AddFontResourceExW(os.path.join(folder, name), 0x10, None)


_load_fonts()


_ERROR_MARKS = (
    "could not",
    "couldn't",
    "did not catch",
    "something went wrong",
    "not allowed",
    "not installed",
    "reinstall",
    "did not start",
    "language model",
    "windows blocked",
)


def _probe_links() -> dict[str, str]:
    """Live checks. ON only when that part is actually available."""
    links = {"SPOTIFY": "OFF", "VOICE": "OFF", "SYSTEM": "OFF"}
    try:
        import psutil

        running = False
        for proc in psutil.process_iter(["name"]):
            if "spotify" in (proc.info.get("name") or "").lower():
                running = True
                break
        links["SPOTIFY"] = "ON" if running else "OFF"
        psutil.virtual_memory()
        import ctypes

        ctypes.windll.user32.GetDesktopWindow()
        links["SYSTEM"] = "ON"
    except Exception:
        links["SYSTEM"] = "OFF"
    try:
        from input_engine import voice_link

        links["VOICE"] = voice_link()
    except Exception:
        links["VOICE"] = "OFF"
    return links


def _rate(num_bytes: float | None) -> str:
    if num_bytes is None:
        return "--"
    amount = max(0.0, float(num_bytes))
    if amount < 1024:
        return f"{int(amount)} B/s"
    if amount < 1024 * 1024:
        return f"{amount / 1024:.1f} KB/s"
    return f"{amount / (1024 * 1024):.1f} MB/s"


def _local_ip() -> str:
    import socket

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(0.4)
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
        sock.close()
    except Exception:
        return ""
    if not ip or ip.startswith("127."):
        return ""
    return ip


def _wifi_name() -> str:
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        completed = subprocess.run(
            ["netsh", "wlan", "show", "interfaces"],
            capture_output=True,
            text=True,
            timeout=3,
            creationflags=flags,
        )
    except Exception:
        return ""
    ssid = ""
    connected = False
    for line in completed.stdout.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().lower()
        value = value.strip()
        if key == "state" and value.lower().startswith("connected"):
            connected = True
        elif key == "ssid" and value:
            ssid = value
    if connected and ssid:
        return ssid
    return ""


def _adapter_name(ip: str) -> str:
    wifi = _wifi_name()
    if wifi:
        return wifi
    if not ip:
        return "OFFLINE"
    try:
        import psutil

        for name, addresses in psutil.net_if_addrs().items():
            for address in addresses:
                if getattr(address, "address", "") == ip:
                    return name
    except Exception:
        pass
    return "ETHERNET"


def _wan_ip() -> str:
    try:
        import urllib.request

        with urllib.request.urlopen("https://api.ipify.org", timeout=3) as response:
            text = response.read().decode("ascii", "ignore").strip()
    except Exception:
        return ""
    if text.count(".") == 3 and len(text) < 16:
        return text
    return ""


class _MicControl:
    """Stand-in for the old Listen button so voice can disable the mic."""

    def __init__(self, owner: "JarvisWindow") -> None:
        self.owner = owner
        self.state = "normal"

    def configure(self, **kwargs) -> None:
        if "state" in kwargs:
            self.state = kwargs["state"]


class JarvisWindow:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.queue: queue.Queue[str] = queue.Queue()
        self.ui_queue: queue.Queue[tuple] = queue.Queue()
        self._closing = False
        self._spin = 0.0
        self._spin_ticks = 0.0
        self._pulse = 0.0
        self._a1 = 0.0
        self._a2 = 0.0
        self._a3 = 0.0
        self._spark: list[float] = []
        self._mode = "READY"
        self._cpu = None
        self._mem = None
        self._links = {"SPOTIFY": "…", "VOICE": "…", "SYSTEM": "…"}
        self._net = {"ip": "…", "wan": "…", "name": "…", "down": None, "up": None, "online": None}
        self._track = ""
        self._track_state = ""
        self._track_source = ""
        self._media = {
            "title": "",
            "artist": "",
            "state": "",
            "source": "",
            "position": 0.0,
            "duration": 0.0,
            "art": "",
        }
        self._media_at = 0.0
        self._art_image = None
        self._art_key = ""
        self._player_hover = ""
        use_queue(self.queue)
        add_listener(self._on_assistant_line)

        self.root.title("J.A.R.V.I.S")
        icon = os.path.join(os.path.dirname(os.path.abspath(__file__)), "jarvis.ico")
        if os.path.exists(icon):
            try:
                self.root.iconbitmap(icon)
            except tk.TclError:
                pass
        self.root.minsize(1020, 680)
        self.root.update_idletasks()
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        width = min(1160, max(1020, screen_w - 40))
        height = min(740, max(680, screen_h - 140))
        origin_x = max(0, (screen_w - width) // 2)
        self.root.geometry(f"{width}x{height}+{origin_x}+16")
        self.root.configure(bg=BG)
        self.root.protocol("WM_DELETE_WINDOW", self._close)

        self._build()
        self._animate()
        self._poll()
        threading.Thread(target=self._watch_system, daemon=True).start()
        threading.Thread(target=self._run_assistant, daemon=True).start()

    def _build(self) -> None:
        families = set(tkfont.families(self.root))
        self.font_body = "Rajdhani" if "Rajdhani" in families else "Segoe UI"
        self.font_display = "Orbitron" if "Orbitron" in families else "Segoe UI"
        self.font_mono = "JetBrains Mono" if "JetBrains Mono" in families else "Consolas"
        self.f_body = tkfont.Font(family=self.font_body, size=12)
        self.f_mono = tkfont.Font(family=self.font_mono, size=10)

        self.hud = tk.Canvas(self.root, bg=BG, highlightthickness=0, bd=0)
        self.hud.pack(fill="both", expand=True)
        self.chat = tk.Canvas(self.root, bg=BG, highlightthickness=0, bd=0)
        self.ask = tk.Canvas(self.root, bg=BG, highlightthickness=0, bd=0)
        for tag, action in (("prev", "previous"), ("toggle", "toggle"), ("next", "next")):
            self.hud.tag_bind(tag, "<Button-1>", lambda _event, action=action: self._media_action(action))
        for tag in ("prev", "toggle", "next"):
            self.hud.tag_bind(tag, "<Enter>", lambda _event, tag=tag: self._hover_player(tag))
            self.hud.tag_bind(tag, "<Leave>", lambda _event: self._hover_player(""))
        self.hud.bind("<Button-1>", self._press_reactor)

        self._chat: list[tuple[str, str, bool]] = []
        self._chat_offset = 0.0
        self._chat_follow = True
        self._chat_limit = 0.0
        self._chat_geom = None
        self._ask_geom = None
        self._entry_id = None
        self._field_focus = False
        self._last_user = ""
        self._reactor_hit = (0.0, 0.0, 0.0)
        self._bg_photo = None
        self._bg_pil = None
        self._bg_size = (0, 0)
        self._placeholder = "Ask JARVIS anything…"
        self._entry_empty = True
        self.entry = tk.Entry(
            self.ask,
            bg=FIELD,
            fg=MUTE,
            insertbackground=ICE,
            relief="flat",
            highlightthickness=0,
            borderwidth=0,
            font=(self.font_body, 13),
        )
        self.entry.insert(0, self._placeholder)
        self.entry.bind("<Return>", lambda _event: self._send())
        self.entry.bind("<FocusIn>", self._clear_placeholder)
        self.entry.bind("<FocusOut>", self._restore_placeholder)
        self.listen_button = _MicControl(self)
        self.ask.tag_bind("send", "<Button-1>", lambda _event: self._send())
        self.ask.tag_bind("mic", "<Button-1>", lambda _event: self._listen())
        self.chat.tag_bind("retry", "<Button-1>", lambda _event: self._retry())
        for canvas in (self.ask, self.chat):
            for tag in ("send", "mic", "retry"):
                canvas.tag_bind(tag, "<Enter>", lambda _event, tag=tag: self._hover_player(tag))
                canvas.tag_bind(tag, "<Leave>", lambda _event: self._hover_player(""))
        self.chat.bind("<Enter>", lambda _event: self.chat.bind_all("<MouseWheel>", self._scroll_chat))
        self.chat.bind("<Leave>", lambda _event: self.chat.unbind_all("<MouseWheel>"))

    def _button(self, parent, label: str, command) -> tk.Button:
        return tk.Button(
            parent,
            text=label,
            command=command,
            bg=BG,
            fg=CYAN,
            activebackground="#062833",
            activeforeground=CYAN,
            relief="flat",
            highlightthickness=1,
            highlightbackground=DIM,
            padx=16,
            pady=8,
            cursor="hand2",
            font=("Segoe UI", 11),
        )

    def _ring(self, cx: float, cy: float, radius: float, color: str, width: int) -> None:
        self.hud.create_oval(
            cx - radius,
            cy - radius,
            cx + radius,
            cy + radius,
            outline=color,
            width=width,
        )

    def _arc(self, cx: float, cy: float, radius: float, start: float, extent: float, color: str, width: int) -> None:
        self.hud.create_arc(
            cx - radius,
            cy - radius,
            cx + radius,
            cy + radius,
            start=start,
            extent=extent,
            style="arc",
            outline=color,
            width=width,
        )

    def _reminder_view(self) -> dict:
        try:
            from reminders import screen_state

            return screen_state()
        except Exception:
            return {"items": [], "alert": ""}

    def _layout(self, width: float, height: float, alert: bool) -> dict:
        side = 14
        panel_w = 210 if width >= 1000 else 172
        player_band = 52
        origin = 44 if alert else 6
        circle_top = origin + player_band
        bottom = height - 10
        left = (side, circle_top, side + panel_w, bottom)
        right = (width - side - panel_w, circle_top, width - side, bottom)
        avail_w = max(80, right[0] - left[2])
        avail_h = max(80, bottom - circle_top)
        radius = min(avail_w / 2 - 18, avail_h / 2 - 4)
        radius = max(72, radius)
        cy = (circle_top + bottom) / 2
        return {
            "left": left,
            "right": right,
            "cx": width / 2,
            "cy": cy,
            "radius": radius,
            "alert_y": 22,
            "player_y": origin + 4,
        }

    def _corners(self, x1: float, y1: float, x2: float, y2: float, length: float = 12) -> None:
        segments = (
            (x1, y1, x1 + length, y1),
            (x1, y1, x1, y1 + length),
            (x2, y1, x2 - length, y1),
            (x2, y1, x2, y1 + length),
            (x1, y2, x1 + length, y2),
            (x1, y2, x1, y2 - length),
            (x2, y2, x2 - length, y2),
            (x2, y2, x2, y2 - length),
        )
        for xa, ya, xb, yb in segments:
            self.hud.create_line(xa, ya, xb, yb, fill=CYAN, width=1)

    def _panel(self, box: tuple, title: str) -> None:
        x1, y1, x2, y2 = box
        self.hud.create_rectangle(x1, y1, x2, y2, outline="#083040", fill="#04141c")
        self._corners(x1, y1, x2, y2, 14)
        self.hud.create_text(
            x1 + 16,
            y1 + 18,
            text=title,
            anchor="w",
            fill=CYAN,
            font=("Segoe UI", 9, "bold"),
        )
        self.hud.create_line(x1 + 16, y1 + 32, x2 - 16, y1 + 32, fill="#0b3c4c")

    def _draw_hud(self) -> None:
        self._paint()

    def _draw_ring(self, cx: float, cy: float, radius: float) -> None:
        for index in range(16):
            start = self._spin + index * 22.5
            bright = index % 4 == int(self._spin / 30) % 4
            self._arc(cx, cy, radius, start, 14, CYAN if bright else DIM, 3 if bright else 2)

        tick_radius = radius * 0.84
        for index in range(72):
            angle = math.radians(self._spin_ticks + index * 5)
            inner = tick_radius - (10 if index % 6 == 0 else 5)
            self.hud.create_line(
                cx + math.cos(angle) * inner,
                cy - math.sin(angle) * inner,
                cx + math.cos(angle) * tick_radius,
                cy - math.sin(angle) * tick_radius,
                fill=CYAN if index % 6 == 0 else DIM,
                width=2 if index % 6 == 0 else 1,
            )

        dot_radius = radius * 0.70
        for index in range(48):
            angle = math.radians(-self._spin * 0.7 + index * 7.5)
            x = cx + math.cos(angle) * dot_radius
            y = cy - math.sin(angle) * dot_radius
            size = 2.2 if index % 4 == 0 else 1.1
            self.hud.create_oval(x - size, y - size, x + size, y + size, fill=CYAN, outline="")

        self._arc(cx, cy, radius * 0.58, self._spin * 1.4, 70, CYAN, 2)
        self._arc(cx, cy, radius * 0.58, self._spin * 1.4 + 180, 40, DIM, 2)
        self._ring(cx, cy, radius * 0.50, DIM, 1)
        self._ring(cx, cy, radius * 0.34, "#083848", 8)
        self._draw_motion(cx, cy, radius)
        self._ring(cx, cy, radius * 0.30, CYAN, 2)

        self.hud.create_text(
            cx,
            cy - 6,
            text="J.A.R.V.I.S",
            fill=CYAN,
            font=("Segoe UI", 18, "bold"),
        )
        status = self._shown_status()
        self.hud.create_text(
            cx,
            cy + 16,
            text=status,
            fill=CYAN if status in {"LISTENING", "SPEAKING"} else SOFT,
            font=("Segoe UI", 10, "bold"),
        )

    def _shown_status(self) -> str:
        try:
            from speech_engine import is_speaking

            if is_speaking():
                return "SPEAKING"
        except Exception:
            pass
        if self._mode in {"LISTENING", "THINKING"}:
            return self._mode
        return "READY"

    def _draw_motion(self, cx: float, cy: float, radius: float) -> None:
        status = self._shown_status()
        pulse = 0.5 + 0.5 * math.sin(self._pulse)
        if status == "READY":
            pulse *= 0.55
        color = _mix(0.35 + 0.65 * pulse)
        self._arc(cx, cy, radius * 0.93, -self._spin * 1.35, 58, color, 3)
        self._arc(cx, cy, radius * 0.93, -self._spin * 1.35 + 140, 28, DIM, 2)
        self._arc(cx, cy, radius * 0.93, -self._spin * 1.35 + 250, 16, color, 2)
        breathe = radius * (0.22 + 0.02 * pulse)
        self._ring(cx, cy, breathe, _mix(0.2 + 0.5 * pulse), 2)
        for index in range(8):
            angle = math.radians(self._spin * 2 + index * 45)
            orbit = radius * 0.42
            x = cx + math.cos(angle) * orbit
            y = cy - math.sin(angle) * orbit
            bright = (index + int(self._pulse)) % 2 == 0
            size = (2.2 + 3.4 * pulse) if bright else 1.4
            self.hud.create_oval(
                x - size,
                y - size,
                x + size,
                y + size,
                fill=color if bright else DIM,
                outline="",
            )

    def _draw_status_panel(self, box: tuple) -> None:
        x1, y1, x2, y2 = box
        self._panel(box, "STATUS")
        meter_r = min(34, (x2 - x1 - 56) / 4)
        meter_r = max(24, meter_r)
        cluster_h = meter_r * 2 + 26 + 22 * 3
        body_top = y1 + 44
        body_bottom = y2 - 18
        origin = (x1 + x2) / 2
        y = body_top + max(8, (body_bottom - body_top - cluster_h) / 2) + meter_r
        gap = meter_r + 14
        self._meter(origin - gap, y, meter_r, self._cpu, "CPU")
        self._meter(origin + gap, y, meter_r, self._mem, "MEM")
        link_y = y + meter_r + 28
        for name in ("SPOTIFY", "VOICE", "SYSTEM"):
            self._link(x1 + 22, link_y, name, self._links.get(name, "…"))
            link_y += 24

    def _draw_network_panel(self, box: tuple, view: dict) -> None:
        x1, y1, x2, y2 = box
        self._panel(box, "NETWORK")
        net = self._net
        online = net.get("online")
        ink = CYAN if online else (OFF if online is False else DIM)
        rows = (
            ("LINK", net.get("name") or "…", ink),
            ("LAN", net.get("ip") or "…", ink),
            ("WAN", net.get("wan") or "…", SOFT),
            ("DOWN", _rate(net.get("down")), CYAN),
            ("UP", _rate(net.get("up")), CYAN),
        )
        row_y = y1 + 52
        for label, value, color in rows:
            shown = str(value)
            if len(shown) > 18:
                shown = shown[:17] + "…"
            self.hud.create_text(x1 + 16, row_y, text=label, anchor="w", fill=DIM, font=("Segoe UI", 8))
            self.hud.create_text(
                x2 - 16,
                row_y,
                text=shown,
                anchor="e",
                fill=color,
                font=("Consolas", 10, "bold"),
            )
            row_y += 26
        self.hud.create_line(x1 + 16, row_y + 2, x2 - 16, row_y + 2, fill="#0b3c4c")
        self._draw_timer(x1, row_y + 12, x2, y2 - 12, view)

    def _draw_timer(self, x1: float, y1: float, x2: float, y2: float, view: dict) -> None:
        items = view.get("items") or []
        cx = (x1 + x2) / 2
        room_w = max(40, x2 - x1)
        room_h = max(40, y2 - y1)
        ring_r = min(46, room_w * 0.30, room_h * 0.34)
        ring_r = max(28, ring_r)
        oy = y1 + max(ring_r + 2, (room_h - ring_r) / 2)
        self._ring(cx, oy, ring_r, DIM, 2)
        if not items:
            self.hud.create_text(cx, oy - 8, text="TIMER", fill=DIM, font=("Segoe UI", 8, "bold"))
            self.hud.create_text(cx, oy + 10, text="STANDBY", fill=SOFT, font=("Segoe UI", 9, "bold"))
            return

        item = items[0]
        total = max(1, int(item["total"]))
        remaining = max(0.0, min(1.0, item["left"] / total))
        if remaining > 0:
            self._arc(cx, oy, ring_r - 4, 90, -remaining * 360, CYAN, 4)
        if item["kind"] == "timer":
            caption = "TIMER"
            readout = _countdown_text(item["left"])
        else:
            caption = "REMINDER"
            readout = item["when"]
        self.hud.create_text(cx, oy - 8, text=caption, fill=SOFT, font=("Segoe UI", 7))
        self.hud.create_text(cx, oy + 8, text=readout, fill=CYAN, font=("Consolas", 11, "bold"))
        label = item["label"].upper()
        if len(label) > 18:
            label = label[:17] + "..."
        self.hud.create_text(
            cx,
            min(y2, oy + ring_r + 16),
            text=label,
            fill=SOFT,
            font=("Segoe UI", 8),
            width=room_w - 16,
        )

    def _hover_player(self, tag: str) -> None:
        self._player_hover = tag
        self.hud.configure(cursor="hand2" if tag else "")

    def _media_action(self, action: str) -> None:
        def work() -> None:
            try:
                from playback import transport

                self.ui_queue.put(("track", transport(action)))
            except Exception as exc:
                print(f"[Playback] {exc}")

        threading.Thread(target=work, daemon=True).start()

    def _cover(self, path: str):
        key = ""
        if path and os.path.isfile(path):
            try:
                key = f"{path}:{os.path.getmtime(path)}:{os.path.getsize(path)}"
            except OSError:
                key = ""
        if key and key == self._art_key and self._art_image is not None:
            return self._art_image
        self._art_key = key
        self._art_image = None
        if not key:
            return None
        try:
            from PIL import Image, ImageDraw, ImageTk

            image = Image.open(path).convert("RGBA")
            image = image.resize((34, 34), Image.Resampling.LANCZOS)
            mask = Image.new("L", (34, 34), 0)
            ImageDraw.Draw(mask).ellipse((0, 0, 33, 33), fill=255)
            image.putalpha(mask)
            self._art_image = ImageTk.PhotoImage(image)
        except Exception as exc:
            print(f"[Playback] Cover: {exc}")
            self._art_image = None
        return self._art_image

    def _fit(self, canvas: tk.Canvas, text: str, font: tuple, width: int) -> str:
        if not text:
            return ""
        import tkinter.font as tkfont

        measure = tkfont.Font(font=font)
        shown = text
        while shown and measure.measure(shown) > width:
            shown = shown[:-2].rstrip() + "…"
        return shown

    def _player_button(self, canvas: tk.Canvas, tag: str, x: float, y: float, kind: str, fill: str) -> None:
        color = CYAN if self._player_hover == tag else INK
        canvas.create_rectangle(x - 11, y - 12, x + 11, y + 12, fill=fill, outline="", tags=tag)
        if kind == "previous":
            canvas.create_rectangle(x - 7, y - 4, x - 5, y + 4, fill=color, outline="", tags=tag)
            canvas.create_polygon(x - 4, y, x + 1, y - 4, x + 1, y + 4, fill=color, outline="", tags=tag)
            canvas.create_polygon(x + 2, y, x + 7, y - 4, x + 7, y + 4, fill=color, outline="", tags=tag)
        elif kind == "next":
            canvas.create_polygon(x - 7, y - 4, x - 7, y + 4, x - 2, y, fill=color, outline="", tags=tag)
            canvas.create_polygon(x - 1, y - 4, x - 1, y + 4, x + 4, y, fill=color, outline="", tags=tag)
            canvas.create_rectangle(x + 5, y - 4, x + 7, y + 4, fill=color, outline="", tags=tag)
        elif kind == "pause":
            canvas.create_rectangle(x - 4, y - 5, x - 1, y + 5, fill=color, outline="", tags=tag)
            canvas.create_rectangle(x + 1, y - 5, x + 4, y + 5, fill=color, outline="", tags=tag)
        else:
            canvas.create_polygon(x - 3, y - 5, x - 3, y + 5, x + 5, y, fill=color, outline="", tags=tag)

    def _draw_player(self, cx: float, top: float, circle_top: float) -> None:
        canvas = self.hud
        media = self._media
        title = media.get("title") or ""
        artist = media.get("artist") or ""
        playing = media.get("state") == "playing"
        pill_h = 40
        pill_w = 280
        x1 = cx - pill_w / 2
        y1 = min(top, circle_top - pill_h - 10)
        y1 = max(6, y1)
        y2 = y1 + pill_h
        x2 = x1 + pill_w
        cap = (y2 - y1) / 2
        pill = "#102833"
        canvas.create_oval(x1, y1, x1 + cap * 2, y2, fill=pill, outline="")
        canvas.create_oval(x2 - cap * 2, y1, x2, y2, fill=pill, outline="")
        canvas.create_rectangle(x1 + cap, y1, x2 - cap, y2, fill=pill, outline="")
        canvas.create_arc(x1, y1, x1 + cap * 2, y2, start=90, extent=180, style="arc", outline="#1c4d5e")
        canvas.create_arc(x2 - cap * 2, y1, x2, y2, start=270, extent=180, style="arc", outline="#1c4d5e")
        canvas.create_line(x1 + cap, y1, x2 - cap, y1, fill="#1c4d5e")
        canvas.create_line(x1 + cap, y2, x2 - cap, y2, fill="#1c4d5e")

        mid = (y1 + y2) / 2
        self._player_button(canvas, "prev", x1 + 18, mid, "previous", pill)
        self._player_button(canvas, "toggle", x1 + 42, mid, "pause" if playing else "play", pill)
        self._player_button(canvas, "next", x1 + 66, mid, "next", pill)

        text_x = x1 + 84
        art = self._cover(media.get("art") or "")
        if art is not None:
            canvas.create_image(text_x, mid - 2, image=art, anchor="w")
            text_x += 28
        text_w = max(36, x2 - text_x - 14)
        shown_title = self._fit(canvas, title or "Nothing playing", ("Segoe UI", 9, "bold"), text_w)
        shown_artist = self._fit(canvas, artist, ("Segoe UI", 8), text_w)
        canvas.create_text(
            text_x,
            mid - 8,
            text=shown_title,
            anchor="w",
            fill=INK if title else DIM,
            font=("Segoe UI", 9, "bold"),
        )
        if shown_artist:
            canvas.create_text(text_x, mid + 5, text=shown_artist, anchor="w", fill=SOFT, font=("Segoe UI", 8))

        duration = float(media.get("duration") or 0)
        if duration > 0 and title:
            elapsed = max(0.0, time.time() - self._media_at) if playing else 0.0
            position = min(duration, float(media.get("position") or 0) + elapsed)
            fraction = max(0.0, min(1.0, position / duration))
            line_y = y2 - 6
            line_x2 = x2 - 14
            canvas.create_line(text_x, line_y, line_x2, line_y, fill="#1c3d4a", width=2)
            played = text_x + (line_x2 - text_x) * fraction
            if played > text_x + 1:
                canvas.create_line(text_x, line_y, played, line_y, fill=INK, width=2)

    def _meter(self, x: float, y: float, radius: float, percent: float | None, caption: str) -> None:
        self._ring(x, y, radius, DIM, 2)
        if percent is None:
            label = "--"
            extent = 0.0
            color = DIM
        else:
            value = max(0.0, min(100.0, float(percent)))
            label = f"{int(round(value))}%"
            extent = value / 100 * 360
            color = _load_color(value)
        if extent > 0:
            self._arc(x, y, radius - 3, 90, -extent, color, 3)
        self.hud.create_text(x, y - 8, text=caption, fill=SOFT, font=("Segoe UI", 7))
        self.hud.create_text(x, y + 8, text=label, fill=color, font=("Consolas", 11, "bold"))

    def _link(self, x: float, y: float, name: str, state: str) -> None:
        if state == "ON":
            color = CYAN
        elif state == "OFF":
            color = OFF
        else:
            color = DIM
        self.hud.create_oval(x, y - 4, x + 8, y + 4, fill=color, outline="")
        self.hud.create_text(
            x + 14,
            y,
            text=f"{name}  {state}",
            fill=color,
            anchor="w",
            font=("Segoe UI", 9),
        )

    def _blend(self, color: str, amount: float) -> str:
        amount = max(0.0, min(1.0, amount))
        fg = tuple(int(color[index : index + 2], 16) for index in (1, 3, 5))
        bg = tuple(int(BG[index : index + 2], 16) for index in (1, 3, 5))
        parts = [int(bg[i] + (fg[i] - bg[i]) * amount) for i in range(3)]
        return f"#{parts[0]:02x}{parts[1]:02x}{parts[2]:02x}"

    def _round_rect(self, canvas, x1, y1, x2, y2, radius, fill, outline="", width=1, tags=("ui",)) -> None:
        radius = min(radius, max(0, (x2 - x1) / 2), max(0, (y2 - y1) / 2))
        if radius < 1:
            canvas.create_rectangle(x1, y1, x2, y2, fill=fill, outline=outline, width=width, tags=tags)
            return
        canvas.create_arc(x1, y1, x1 + 2 * radius, y1 + 2 * radius, start=90, extent=90, fill=fill, outline="", tags=tags)
        canvas.create_arc(x2 - 2 * radius, y1, x2, y1 + 2 * radius, start=0, extent=90, fill=fill, outline="", tags=tags)
        canvas.create_arc(x1, y2 - 2 * radius, x1 + 2 * radius, y2, start=180, extent=90, fill=fill, outline="", tags=tags)
        canvas.create_arc(x2 - 2 * radius, y2 - 2 * radius, x2, y2, start=270, extent=90, fill=fill, outline="", tags=tags)
        canvas.create_rectangle(x1 + radius, y1, x2 - radius, y2, fill=fill, outline="", tags=tags)
        canvas.create_rectangle(x1, y1 + radius, x2, y2 - radius, fill=fill, outline="", tags=tags)
        if not outline:
            return
        canvas.create_arc(x1, y1, x1 + 2 * radius, y1 + 2 * radius, start=90, extent=90, style="arc", outline=outline, width=width, tags=tags)
        canvas.create_arc(x2 - 2 * radius, y1, x2, y1 + 2 * radius, start=0, extent=90, style="arc", outline=outline, width=width, tags=tags)
        canvas.create_arc(x1, y2 - 2 * radius, x1 + 2 * radius, y2, start=180, extent=90, style="arc", outline=outline, width=width, tags=tags)
        canvas.create_arc(x2 - 2 * radius, y2 - 2 * radius, x2, y2, start=270, extent=90, style="arc", outline=outline, width=width, tags=tags)
        canvas.create_line(x1 + radius, y1, x2 - radius, y1, fill=outline, width=width, tags=tags)
        canvas.create_line(x1 + radius, y2, x2 - radius, y2, fill=outline, width=width, tags=tags)
        canvas.create_line(x1, y1 + radius, x1, y2 - radius, fill=outline, width=width, tags=tags)
        canvas.create_line(x2, y1 + radius, x2, y2 - radius, fill=outline, width=width, tags=tags)

    def _square_corner(self, canvas, x1, y1, x2, y2, fill, outline, corner: str) -> None:
        size = 16
        if corner == "tl":
            box = (x1, y1, x1 + size, y1 + size)
            edges = ((x1, y1, x1, y1 + size), (x1, y1, x1 + size, y1))
        else:
            box = (x2 - size, y1, x2, y1 + size)
            edges = ((x2, y1, x2, y1 + size), (x2 - size, y1, x2, y1))
        canvas.create_rectangle(*box, fill=fill, outline="", tags="ui")
        for xa, ya, xb, yb in edges:
            canvas.create_line(xa, ya, xb, yb, fill=outline, tags="ui")

    def _tracked(self, canvas, x, y, text, font, fill, anchor="w", spacing=2.0) -> None:
        measure = tkfont.Font(font=font)
        glyphs = [(ch, measure.measure(ch)) for ch in text]
        total = sum(width for _ch, width in glyphs) + spacing * max(0, len(glyphs) - 1)
        if anchor == "center":
            cursor = x - total / 2
        elif anchor == "e":
            cursor = x - total
        else:
            cursor = x
        for ch, width in glyphs:
            canvas.create_text(cursor, y, text=ch, anchor="w", fill=fill, font=font, tags="ui")
            cursor += width + spacing

    def _fit(self, canvas: tk.Canvas, text: str, font: tuple, width: int) -> str:
        if not text:
            return ""
        measure = tkfont.Font(font=font)
        shown = text
        while shown and measure.measure(shown) > width:
            shown = shown[:-2].rstrip() + "…"
        return shown

    def _wrap(self, text: str, font: tkfont.Font, width: int) -> list[str]:
        words = text.split()
        if not words:
            return [""]
        lines = []
        current = words[0]
        for word in words[1:]:
            trial = f"{current} {word}"
            if font.measure(trial) <= width:
                current = trial
            else:
                lines.append(current)
                current = word
        lines.append(current)
        return lines

    def _render_bg(self, width: int, height: int):
        import numpy as np
        from PIL import Image, ImageTk

        yy, xx = np.mgrid[0:height, 0:width]
        cx, cy = width / 2, height * 0.38
        dist = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
        reach = 0.62 * max(width, height)
        t = np.clip(dist / reach, 0, 1)
        t = t * t * (3 - 2 * t)
        c0 = np.array([32, 34, 39], dtype=np.float32)
        c1 = np.array([18, 19, 22], dtype=np.float32)
        c2 = np.array([8, 8, 10], dtype=np.float32)
        mid = 0.45
        rgb = np.empty((height, width, 3), dtype=np.float32)
        left = t <= mid
        blend = np.zeros_like(t)
        blend[left] = t[left] / mid
        blend[~left] = (t[~left] - mid) / (1 - mid)
        rgb[left] = c0 + (c1 - c0) * blend[left][..., None]
        rgb[~left] = c1 + (c2 - c1) * blend[~left][..., None]
        grid = ((xx % 56) == 0) | ((yy % 56) == 0)
        mask = np.clip(1 - dist / (0.72 * max(width, height)), 0, 1) * 0.09
        rgb[grid] = rgb[grid] * (1 - mask[grid][..., None]) + 255 * mask[grid][..., None]
        image = Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8), "RGB")
        return ImageTk.PhotoImage(image), image

    def _ensure_bg(self, width: int, height: int) -> None:
        if self._bg_size == (width, height) and self._bg_photo is not None:
            return
        try:
            photo, pil = self._render_bg(width, height)
        except Exception as exc:
            print(f"[UI] Background: {exc}")
            return
        self._bg_photo = photo
        self._bg_pil = pil
        self._bg_size = (width, height)
        self._chat_geom = None
        self._ask_geom = None
        self.hud.delete("bg")
        self.hud.create_image(0, 0, image=photo, anchor="nw", tags=("bg",))
        self.hud.tag_lower("bg")

    def _crop_under(self, canvas, key: str, x: int, y: int, width: int, height: int) -> None:
        if self._bg_pil is None or width < 2 or height < 2:
            return
        box = (max(0, x), max(0, y), max(0, x) + width, max(0, y) + height)
        if getattr(canvas, key, None) == box:
            return
        try:
            from PIL import ImageTk

            crop = self._bg_pil.crop(box)
            photo = ImageTk.PhotoImage(crop)
        except Exception:
            return
        setattr(canvas, key, box)
        canvas._crop_photo = photo
        canvas.delete("bg")
        canvas.create_image(0, 0, image=photo, anchor="nw", tags=("bg",))

    def _paint(self) -> None:
        width = self.hud.winfo_width()
        height = self.hud.winfo_height()
        if width < 400 or height < 400:
            return
        self._ensure_bg(width, height)
        view = self._reminder_view()
        alert = view.get("alert") or ""
        pad_x, pad_y, gap = 22, 18, 14
        input_h = 54
        alert_h = 28 if alert else 0
        main_top = pad_y + 52 + gap
        input_y = height - pad_y - input_h
        content_w = max(80, width - pad_x * 2)
        natural = self._chat_content_height(content_w)
        chat_h = min(172, natural)
        chat_y = input_y - (10 + chat_h if chat_h else 0)
        alert_y = chat_y - alert_h - (6 if alert else 0)
        main_bottom = (alert_y if alert else chat_y if chat_h else input_y) - gap
        if main_bottom - main_top < 280 and chat_h > 72:
            chat_h = max(72, chat_h - (280 - (main_bottom - main_top)))
            chat_y = input_y - 10 - chat_h
            alert_y = chat_y - alert_h - (6 if alert else 0)
            main_bottom = alert_y - gap
        panel_w = 270 if width >= 1180 else (230 if width >= 1060 else 200)
        left = (pad_x, main_top, pad_x + panel_w, main_bottom)
        right = (width - pad_x - panel_w, main_top, width - pad_x, main_bottom)
        core_x1 = left[2] + 22
        core_x2 = right[0] - 22
        side = min(core_x2 - core_x1, main_bottom - main_top, 460, height * 0.52)
        side = max(180, side)
        cx = (core_x1 + core_x2) / 2
        cy = (main_top + main_bottom) / 2
        self._reactor_hit = (cx, cy, side / 2)

        self.hud.delete("ui")
        self._draw_brand(width, pad_y)
        self._draw_now(width / 2, pad_y + 26)
        self._draw_clock(width - pad_x, pad_y + 26)
        self._draw_status_card(left)
        self._draw_network_card(right, view)
        self._draw_reactor(cx, cy, side / 2)
        if alert:
            self.hud.create_text(
                width / 2,
                alert_y + alert_h / 2,
                text=alert,
                fill=WARN,
                font=(self.font_body, 12, "bold"),
                tags="ui",
            )
        content_w = max(80, width - pad_x * 2)
        if chat_h >= 24:
            self._place_layer(self.chat, "_chat_geom", pad_x, chat_y, content_w, chat_h)
            self._crop_under(self.chat, "_crop_box", pad_x, chat_y, content_w, chat_h)
            self._draw_messages(content_w, chat_h)
        elif self._chat_geom is not None:
            self.chat.place_forget()
            self._chat_geom = None
        self._place_layer(self.ask, "_ask_geom", pad_x, input_y, content_w, input_h)
        self._crop_under(self.ask, "_crop_box", pad_x, input_y, content_w, input_h)
        self._draw_composer(content_w, input_h)

    def _chat_content_height(self, width: int) -> int:
        if not self._chat:
            return 0
        y = 2.0
        inner = min(620, int(width * 0.86)) - 28
        for _speaker, text, error in self._chat:
            lines = self._wrap(text, self.f_body, max(40, inner))
            y += 18 + 20 * len(lines) + (20 if error else 0) + 8 + 8
        return int(max(0, y - 8))

    def _place_layer(self, canvas, attr: str, x: int, y: int, width: int, height: int) -> None:
        geom = (int(x), int(y), int(width), int(height))
        if getattr(self, attr) == geom:
            return
        setattr(self, attr, geom)
        canvas.place(x=geom[0], y=geom[1], width=geom[2], height=geom[3])
        self.root.tk.call("raise", canvas._w)
        canvas._crop_box = None

    def _draw_brand(self, width: float, top: float) -> None:
        self._tracked(self.hud, 22, top + 26, "STARK  ·  OS", (self.font_display, 9), MUTE, spacing=1.2)

    def _draw_clock(self, x: float, y: float) -> None:
        now = datetime.now()
        self.hud.create_text(x, y - 8, text=now.strftime("%H:%M"), anchor="e", fill=TEXT, font=(self.font_mono, 15, "bold"), tags="ui")
        self._tracked(self.hud, x, y + 12, now.strftime("%a · %d %b").upper(), (self.font_mono, 8), MUTE, anchor="e", spacing=1.1)

    def _draw_now(self, cx: float, cy: float) -> None:
        width = min(360, max(240, self.hud.winfo_width() * 0.46))
        height = 52
        x1, y1 = cx - width / 2, cy - height / 2
        x2, y2 = x1 + width, y1 + height
        self._round_rect(self.hud, x1, y1, x2, y2, height / 2, "#1a1c20", LINE, 1)
        self._draw_disc(x1 + 25, cy)
        media = self._media
        title = media.get("title") or "Nothing playing"
        artist = media.get("artist") or ""
        text_x = x1 + 50
        text_r = x2 - 96
        text_w = max(40, int(text_r - text_x))
        self.hud.create_text(
            text_x,
            y1 + 15,
            text=self._fit(self.hud, title, (self.font_body, 11, "bold"), text_w),
            anchor="w",
            fill=TEXT if media.get("title") else MUTE,
            font=(self.font_body, 11, "bold"),
            tags="ui",
        )
        if artist:
            self.hud.create_text(
                text_x,
                y1 + 31,
                text=self._fit(self.hud, artist, (self.font_body, 8), text_w),
                anchor="w",
                fill=MUTE,
                font=(self.font_body, 8),
                tags="ui",
            )
        bar_y = y2 - 9
        self.hud.create_line(text_x, bar_y, text_r, bar_y, fill="#34373d", width=2, tags="ui")
        duration = float(media.get("duration") or 0)
        if duration > 0 and media.get("title"):
            playing = media.get("state") == "playing"
            elapsed = max(0.0, time.time() - self._media_at) if playing else 0.0
            position = min(duration, float(media.get("position") or 0) + elapsed)
            fraction = max(0.0, min(1.0, position / duration))
            played = text_x + (text_r - text_x) * fraction
            if played > text_x + 1:
                self.hud.create_line(text_x, bar_y, played, bar_y, fill=ICE, width=2, tags="ui")
        playing = media.get("state") == "playing"
        self._transport(x2 - 78, cy, "prev", "previous")
        self._transport(x2 - 48, cy, "toggle", "pause" if playing else "play")
        self._transport(x2 - 18, cy, "next", "next")

    def _draw_disc(self, cx: float, cy: float) -> None:
        art = self._cover(self._media.get("art") or "")
        if art is not None:
            self.hud.create_image(cx, cy, image=art, tags="ui")
            return
        self.hud.create_oval(cx - 17, cy - 17, cx + 17, cy + 17, fill="#2a2d33", outline="", tags="ui")
        self.hud.create_arc(cx - 14, cy - 14, cx + 14, cy + 14, start=self._a3, extent=210, style="arc", outline=ICE, width=3, tags="ui")
        self.hud.create_arc(cx - 14, cy - 14, cx + 14, cy + 14, start=self._a3 + 220, extent=70, style="arc", outline="#50545b", width=3, tags="ui")
        self.hud.create_oval(cx - 5, cy - 5, cx + 5, cy + 5, fill=BG, outline="", tags="ui")

    def _transport(self, x: float, y: float, tag: str, kind: str) -> None:
        color = ICE
        plate = "#2c313a" if self._player_hover == tag else "#1a1c20"
        self.hud.create_oval(x - 14, y - 14, x + 14, y + 14, fill=plate, outline="", tags=("ui", tag))
        if kind == "previous":
            self.hud.create_rectangle(x - 6, y - 5, x - 4, y + 5, fill=color, outline="", tags=("ui", tag))
            self.hud.create_polygon(x - 3, y, x + 2, y - 5, x + 2, y + 5, fill=color, outline="", tags=("ui", tag))
            self.hud.create_polygon(x + 2, y, x + 7, y - 5, x + 7, y + 5, fill=color, outline="", tags=("ui", tag))
        elif kind == "next":
            self.hud.create_polygon(x - 7, y - 5, x - 7, y + 5, x - 2, y, fill=color, outline="", tags=("ui", tag))
            self.hud.create_polygon(x - 2, y - 5, x - 2, y + 5, x + 3, y, fill=color, outline="", tags=("ui", tag))
            self.hud.create_rectangle(x + 4, y - 5, x + 6, y + 5, fill=color, outline="", tags=("ui", tag))
        elif kind == "pause":
            self.hud.create_rectangle(x - 4, y - 5, x - 1, y + 5, fill=color, outline="", tags=("ui", tag))
            self.hud.create_rectangle(x + 1, y - 5, x + 4, y + 5, fill=color, outline="", tags=("ui", tag))
        else:
            self.hud.create_polygon(x - 3, y - 6, x - 3, y + 6, x + 6, y, fill=color, outline="", tags=("ui", tag))

    def _card(self, box: tuple, title: str) -> None:
        x1, y1, x2, y2 = box
        self._round_rect(self.hud, x1, y1, x2, y2, 18, PANEL, LINE, 1)
        self.hud.create_line(x1 + 28, y1 + 1, x2 - 28, y1 + 1, fill="#66707c", tags="ui")
        self.hud.create_line((x1 + x2) / 2 - 46, y1 + 1, (x1 + x2) / 2 + 46, y1 + 1, fill=ICE, tags="ui")
        self._tracked(self.hud, x1 + 18, y1 + 28, title, (self.font_display, 9), MUTE, spacing=2.4)

    def _draw_status_card(self, box: tuple) -> None:
        x1, y1, x2, y2 = box
        self._card(box, "STATUS")
        radius = 36 if (x2 - x1) >= 250 else 30
        origin = (x1 + x2) / 2
        gap = radius + 22
        gy = y1 + 52 + radius
        self._gauge(origin - gap, gy, radius, self._cpu, "CPU")
        self._gauge(origin + gap, gy, radius, self._mem, "MEM")
        chip_y = gy + radius + 20
        chip_x = x1 + 18
        chip_w = x2 - x1 - 36
        for name in ("SPOTIFY", "VOICE", "SYSTEM"):
            if chip_y + 36 > y2 - 12:
                break
            self._chip(chip_x, chip_y, chip_w, name, self._links.get(name, "…"))
            chip_y += 44

    def _gauge(self, cx: float, cy: float, radius: float, percent: float | None, caption: str) -> None:
        color = _load_color(percent)
        self.hud.create_oval(cx - radius, cy - radius, cx + radius, cy + radius, outline="#2a3038", width=6, tags="ui")
        if percent is not None and percent > 0:
            extent = max(0.0, min(100.0, float(percent))) / 100 * 360
            self.hud.create_arc(
                cx - radius,
                cy - radius,
                cx + radius,
                cy + radius,
                start=90,
                extent=-extent,
                style="arc",
                outline=color,
                width=6,
                tags="ui",
            )
        label = "--" if percent is None else f"{int(round(percent))}%"
        self.hud.create_text(cx, cy - 6, text=label, fill=TEXT, font=(self.font_body, 16, "bold"), tags="ui")
        self._tracked(self.hud, cx, cy + 14, caption, (self.font_body, 8), MUTE, anchor="center", spacing=1.8)

    def _chip(self, x: float, y: float, width: float, name: str, state: str) -> None:
        self._round_rect(self.hud, x, y, x + width, y + 36, 10, "#1b1d22", LINE, 1)
        if state == "ON":
            dot = OK
        elif state == "OFF":
            dot = ERR
        else:
            dot = MUTE
        pulse = 0.55 + 0.45 * (0.5 + 0.5 * math.sin(self._pulse))
        if state == "ON":
            dot = self._blend(OK, pulse)
        cy = y + 18
        self.hud.create_oval(x + 14, cy - 4, x + 22, cy + 4, fill=dot, outline="", tags="ui")
        self.hud.create_text(x + 32, cy, text=name, anchor="w", fill=TEXT, font=(self.font_body, 11), tags="ui")
        self.hud.create_text(x + width - 12, cy, text=state, anchor="e", fill=MUTE, font=(self.font_body, 9), tags="ui")

    def _draw_network_card(self, box: tuple, view: dict) -> None:
        x1, y1, x2, y2 = box
        self._card(box, "NETWORK")
        net = self._net
        online = net.get("online")
        ink = ICE if online else (ERR if online is False else MUTE)
        rows = (
            ("LINK", net.get("name") or "…", ink),
            ("LAN", net.get("ip") or "…", ink),
            ("WAN", net.get("wan") or "…", TEXT),
        )
        y = y1 + 52
        inset = 18
        for label, value, color in rows:
            shown = self._fit(self.hud, str(value), (self.font_mono, 10), int(x2 - x1 - 110))
            self._tracked(self.hud, x1 + inset, y, label, (self.font_body, 9), MUTE, spacing=1.6)
            self.hud.create_text(x2 - inset, y, text=shown, anchor="e", fill=color, font=(self.font_mono, 10), tags="ui")
            self.hud.create_line(x1 + inset, y + 12, x2 - inset, y + 12, fill="#26282d", tags="ui")
            y += 28
        self._sparkline(x1 + inset, y + 4, x2 - inset, y + 44)
        y += 52
        down = _rate(net.get("down"))
        up = _rate(net.get("up"))
        for label, value, color in (("DOWN", down, ICE), ("UP", up, OK)):
            self._tracked(self.hud, x1 + inset, y, label, (self.font_body, 9), MUTE, spacing=1.6)
            self.hud.create_text(x2 - inset, y, text=value, anchor="e", fill=color, font=(self.font_mono, 10), tags="ui")
            self.hud.create_line(x1 + inset, y + 12, x2 - inset, y + 12, fill="#26282d", tags="ui")
            y += 28
        radius = min(52, max(36, (y2 - y - 16) / 2))
        timer_y = y + 16 + radius
        if timer_y + radius > y2 - 8:
            timer_y = y2 - 8 - radius
        self._timer_face((x1 + x2) / 2, timer_y, radius, view)

    def _sparkline(self, x1: float, y1: float, x2: float, y2: float) -> None:
        values = self._spark[-20:] or [0.0, 0.0]
        if len(values) == 1:
            values = [values[0], values[0]]
        low = min(values)
        high = max(values)
        span = max(high - low, 1.0)
        points = []
        last = len(values) - 1
        for index, value in enumerate(values):
            x = x1 + (x2 - x1) * index / last
            y = y2 - (y2 - y1) * ((value - low) / span)
            points.extend((x, y))
        polygon = [x1, y2, *points, x2, y2]
        self.hud.create_polygon(*polygon, fill="#1c1e24", outline="", tags="ui")
        self.hud.create_line(*points, fill=ICE, width=1.5, smooth=True, tags="ui")

    def _timer_face(self, cx: float, cy: float, radius: float, view: dict) -> None:
        self.hud.create_oval(cx - radius + 10, cy - radius + 10, cx + radius - 10, cy + radius - 10, fill="#17191e", outline="", tags="ui")
        self.hud.create_oval(cx - radius, cy - radius, cx + radius, cy + radius, outline=MUTE, width=1, dash=(3, 5), tags="ui")
        items = view.get("items") or []
        if not items:
            self._tracked(self.hud, cx, cy - 8, "TIMER", (self.font_body, 8), MUTE, anchor="center", spacing=1.6)
            self.hud.create_text(cx, cy + 12, text="STANDBY", fill=TEXT, font=(self.font_body, 11), tags="ui")
            return
        item = items[0]
        total = max(1, int(item["total"]))
        remaining = max(0.0, min(1.0, item["left"] / total))
        if remaining > 0:
            self.hud.create_arc(
                cx - radius + 4,
                cy - radius + 4,
                cx + radius - 4,
                cy + radius - 4,
                start=90,
                extent=-remaining * 360,
                style="arc",
                outline=ICE,
                width=3,
                tags="ui",
            )
        caption = "TIMER" if item["kind"] == "timer" else "REMINDER"
        readout = _countdown_text(item["left"]) if item["kind"] == "timer" else str(item["when"])
        label = item["label"].upper()
        self._tracked(self.hud, cx, cy - 16, caption, (self.font_body, 7), MUTE, anchor="center", spacing=1.2)
        self.hud.create_text(cx, cy + 2, text=readout, fill=TEXT, font=(self.font_mono, 11), tags="ui")
        self.hud.create_text(
            cx,
            cy + 18,
            text=self._fit(self.hud, label, (self.font_body, 8), int(radius * 1.6)),
            fill=MUTE,
            font=(self.font_body, 8),
            tags="ui",
        )

    def _draw_reactor(self, cx: float, cy: float, radius: float) -> None:
        status = self._shown_status()
        if status == "LISTENING":
            glow = OK
            state_color = OK
        elif status == "THINKING":
            glow = WARN
            state_color = WARN
        else:
            glow = ICE
            state_color = ICE
        breath = 1.0 + 0.09 * math.sin(self._pulse)
        for scale, amount in ((1.05, 0.08), (0.82, 0.14), (0.62, 0.20)):
            size = radius * 0.72 * breath * scale
            self.hud.create_oval(cx - size, cy - size, cx + size, cy + size, fill=self._blend(glow, amount), outline="", tags="ui")

        def unit(value: float) -> float:
            return radius * value / 200

        self._ticks(cx, cy, unit(188), 120, 2, "#3a3e46", 1, self._a1)
        self._arc_on(cx, cy, unit(188), self._a1, 28, "#d5d8df", 2)
        self._arc_on(cx, cy, unit(188), self._a1 - 150, 14, "#8e939c", 2)
        self._ticks(cx, cy, unit(156), 64, unit(7), "#8a8f98", 2, self._a2)
        self._arc_on(cx, cy, unit(156), self._a2 - 40, 96, "#e8f1ff", 3)
        self.hud.create_oval(cx - unit(122), cy - unit(122), cx + unit(122), cy + unit(122), outline="#3d424a", width=1, tags="ui")
        self._arc_on(cx, cy, unit(122), self._a3, 34, ICE, 2)
        for turn in (self._a3, self._a3 + 180):
            angle = math.radians(turn)
            dx = math.cos(angle) * unit(122)
            dy = -math.sin(angle) * unit(122)
            self.hud.create_oval(cx + dx - 3, cy + dy - 3, cx + dx + 3, cy + dy + 3, fill="#ffffff", outline="", tags="ui")
        disc = unit(92)
        self.hud.create_oval(cx - disc, cy - disc, cx + disc, cy + disc, fill="#0c0c0e", outline="#d7dbe2", width=2, tags="ui")
        inner = unit(80)
        self.hud.create_oval(cx - inner, cy - inner, cx + inner, cy + inner, outline="#4c515a", width=1, tags="ui")
        title_size = max(13, min(20, int(radius * 0.105)))
        self.hud.create_text(cx, cy - 8, text="J.A.R.V.I.S", fill=ICE, font=(self.font_display, title_size, "bold"), tags="ui")
        self._tracked(self.hud, cx, cy + title_size * 0.85, status, (self.font_body, 9), state_color, anchor="center", spacing=2.6)

    def _ticks(self, cx, cy, radius, count, length, color, width, angle0) -> None:
        for index in range(count):
            angle = math.radians(angle0 + index * 360 / count)
            outer_x = cx + math.cos(angle) * radius
            outer_y = cy - math.sin(angle) * radius
            inner = radius - length
            self.hud.create_line(
                cx + math.cos(angle) * inner,
                cy - math.sin(angle) * inner,
                outer_x,
                outer_y,
                fill=color,
                width=width,
                tags="ui",
            )

    def _arc_on(self, cx, cy, radius, start, extent, color, width) -> None:
        self.hud.create_arc(
            cx - radius,
            cy - radius,
            cx + radius,
            cy + radius,
            start=start,
            extent=extent,
            style="arc",
            outline=color,
            width=width,
            tags="ui",
        )

    def _press_reactor(self, event) -> None:
        cx, cy, radius = self._reactor_hit
        if radius <= 0:
            return
        if (event.x - cx) ** 2 + (event.y - cy) ** 2 <= (radius * 0.78) ** 2:
            self._listen()

    def _draw_messages(self, width: int, height: int) -> None:
        canvas = self.chat
        canvas.delete("ui")
        y = 2.0
        blocks = []
        max_w = min(620, int(width * 0.86))
        for speaker, text, error in self._chat:
            mine = speaker.lower() == "you"
            inner = max_w - 28
            lines = self._wrap(text, self.f_body, inner)
            block_h = 18 + 20 * len(lines) + (20 if error else 0) + 8
            blocks.append((mine, error, lines, block_h, max_w))
            y += block_h + 8
        content_h = max(0, y - 8)
        limit = max(0, content_h - height)
        self._chat_limit = float(limit)
        if self._chat_follow:
            self._chat_offset = 0.0
        else:
            self._chat_offset = max(0.0, min(float(limit), self._chat_offset))
        if content_h > height:
            cursor = height - content_h + self._chat_offset
        else:
            cursor = 0.0
        for mine, error, lines, block_h, bubble_w in blocks:
            text_w = max(self.f_body.measure(line) for line in lines) if lines else 40
            if error:
                text_w = max(text_w, self.f_body.measure("RETRY") + 8)
            bubble_w = min(max_w, max(96, int(text_w) + 28))
            x1 = width - bubble_w - 2 if mine else 2
            x2 = x1 + bubble_w
            y1 = cursor
            y2 = cursor + block_h
            cursor += block_h + 8
            if y2 < -8 or y1 > height + 8:
                continue
            overflowing = content_h > height + 8
            mid = (y1 + y2) / 2
            amount = 1.0
            if overflowing and mid < 42:
                amount = max(0.0, mid / 42)
            elif overflowing and self._chat_offset > 2 and mid > height - 30:
                amount = max(0.0, (height - mid) / 30)
            amount = amount * amount * (3 - 2 * amount)
            if amount < 0.08:
                continue
            if error:
                fill, outline, label, label_color = "#2a151b", "#6a3540", "JARVIS", ERR
            elif mine:
                fill, outline, label, label_color = "#2a2d33", "#5c6370", "YOU", MUTE
            else:
                fill, outline, label, label_color = "#1a1c20", LINE, "JARVIS", MUTE
            fill = self._blend(fill, amount)
            outline = self._blend(outline, amount)
            ink = self._blend(TEXT, amount)
            label_color = self._blend(label_color, amount)
            self._round_rect(canvas, x1, y1, x2, y2, 14, fill, outline, 1)
            self._square_corner(canvas, x1, y1, x2, y2, fill, outline, "tr" if mine else "tl")
            canvas.create_text(x1 + 14, y1 + 14, text=label, anchor="w", fill=label_color, font=(self.font_body, 8), tags="ui")
            line_y = y1 + 28
            for line in lines:
                canvas.create_text(x1 + 14, line_y, text=line, anchor="w", fill=ink, font=(self.font_body, 12), tags="ui")
                line_y += 20
            if error:
                canvas.create_text(x1 + 14, line_y + 2, text="RETRY", anchor="w", fill=self._blend(ICE, amount), font=(self.font_body, 9), tags=("ui", "retry"))
                underline = x1 + 14 + self.f_body.measure("RETRY") * 0.75
                canvas.create_line(x1 + 14, line_y + 10, underline, line_y + 10, fill=self._blend(MUTE, amount), tags=("ui", "retry"))
        if limit > 0:
            thumb_h = max(18, height * height / max(content_h, 1))
            travel = max(1, height - thumb_h)
            thumb_y = ((limit - self._chat_offset) / limit) * travel
            canvas.create_line(width - 3, thumb_y, width - 3, thumb_y + thumb_h, fill=MUTE, width=2, tags="ui")

    def _draw_composer(self, width: int, height: int) -> None:
        canvas = self.ask
        canvas.delete("ui")
        mic = 54
        gap = 10
        field_x2 = width - mic - gap
        outline = ICE if self._field_focus else LINE
        if self._field_focus:
            self._round_rect(canvas, -2, -2, field_x2 + 2, height + 2, height / 2, "", "#3a4150", 4, tags=("ui",))
        self._round_rect(canvas, 0, 0, field_x2, height, height / 2, FIELD, outline, 1)
        entry_x = 18
        entry_w = max(40, field_x2 - 56)
        if self._entry_id is None:
            self._entry_id = canvas.create_window(entry_x, height / 2, window=self.entry, anchor="w", width=entry_w, tags=("entry",))
        else:
            canvas.coords(self._entry_id, entry_x, height / 2)
            canvas.itemconfigure(self._entry_id, width=entry_w)
        canvas.tag_raise("entry")
        arrow_x = field_x2 - 24
        arrow_y = height / 2
        plate = "#2c313a" if self._player_hover == "send" else FIELD
        canvas.create_oval(arrow_x - 16, arrow_y - 16, arrow_x + 16, arrow_y + 16, fill=plate, outline="", tags=("ui", "send"))
        canvas.create_line(arrow_x, arrow_y + 7, arrow_x, arrow_y - 6, fill=ICE, width=2, tags=("ui", "send"))
        canvas.create_line(arrow_x - 6, arrow_y - 1, arrow_x, arrow_y - 7, fill=ICE, width=2, tags=("ui", "send"))
        canvas.create_line(arrow_x + 6, arrow_y - 1, arrow_x, arrow_y - 7, fill=ICE, width=2, tags=("ui", "send"))
        mic_x = width - mic / 2
        listening = self.listen_button.state == "disabled" or self._shown_status() == "LISTENING"
        if listening:
            pulse = 0.65 + 0.35 * (0.5 + 0.5 * math.sin(self._pulse * 2))
            mic_fill = self._blend("#7dffc8", pulse)
        else:
            mic_fill = "#f3f4f6"
        canvas.create_oval(mic_x - 27, 0, mic_x + 27, height, fill="#2a2c31", outline="", tags="ui")
        canvas.create_oval(mic_x - 27, 0, mic_x + 27, height, fill=mic_fill, outline="", tags=("ui", "mic"))
        icon = "#0a0a0c"
        canvas.create_oval(mic_x - 5, height / 2 - 10, mic_x + 5, height / 2 + 2, outline=icon, width=2, tags=("ui", "mic"))
        canvas.create_arc(mic_x - 8, height / 2 - 6, mic_x + 8, height / 2 + 8, start=200, extent=140, style="arc", outline=icon, width=2, tags=("ui", "mic"))
        canvas.create_line(mic_x, height / 2 + 8, mic_x, height / 2 + 12, fill=icon, width=2, tags=("ui", "mic"))
        canvas.create_line(mic_x - 5, height / 2 + 12, mic_x + 5, height / 2 + 12, fill=icon, width=2, tags=("ui", "mic"))

    def _animate(self) -> None:
        if self._closing:
            return
        status = self._shown_status()
        if status == "THINKING":
            d1, d2, d3 = 3.0, -3.0, 3.0
        else:
            d1, d2, d3 = 0.20, -0.43, 0.86
        self._a1 = (self._a1 + d1) % 360
        self._a2 = (self._a2 + d2) % 360
        self._a3 = (self._a3 + d3) % 360
        if status == "LISTENING":
            breath = 1.2
        elif status == "SPEAKING":
            breath = 2.0
        else:
            breath = 4.0
        self._pulse = (self._pulse + math.tau / (breath * 30.0)) % math.tau
        self._draw_hud()
        self.root.after(33, self._animate)

    def _set_status(self, text: str) -> None:
        self.ui_queue.put(("status", text.upper()))

    def _poll(self) -> None:
        if self._closing:
            return
        while True:
            try:
                item = self.ui_queue.get_nowait()
            except queue.Empty:
                break
            kind = item[0]
            if kind == "line":
                self._append(item[1], item[2])
            elif kind == "status":
                self._mode = item[1]
            elif kind == "stats":
                self._cpu = item[1]
                self._mem = item[2]
                if item[3] is not None:
                    self._links = item[3]
                if len(item) > 4 and item[4] is not None:
                    self._net = item[4]
                    rate = item[4].get("down") if isinstance(item[4], dict) else None
                    if isinstance(rate, (int, float)):
                        self._spark.append(float(rate))
                        del self._spark[:-24]
            elif kind == "track":
                info = item[1] if isinstance(item[1], dict) else {}
                self._media = {
                    "title": info.get("title") or "",
                    "artist": info.get("artist") or "",
                    "state": info.get("state") or "",
                    "source": info.get("source") or "",
                    "position": float(info.get("position") or 0),
                    "duration": float(info.get("duration") or 0),
                    "art": info.get("art") or "",
                }
                self._media_at = time.time()
                self._track = self._media["title"]
                self._track_state = self._media["state"]
                self._track_source = self._media["source"]
            elif kind == "listen-finished":
                self.listen_button.configure(state="normal")
                heard = item[1]
                self._mode = "THINKING" if heard and heard not in {"none", ""} else "READY"
                if heard and heard not in {"none", ""}:
                    self._send(heard)
                else:
                    self._append("JARVIS", "I did not catch that. Type the command, or try the microphone again.")
            elif kind == "close":
                self._close()
                return
        self.root.after(80, self._poll)

    def _append(self, speaker: str, text: str) -> None:
        clean = text.strip()
        if not clean:
            return
        mine = speaker.lower() == "you"
        error = not mine and any(mark in clean.lower() for mark in _ERROR_MARKS)
        if mine:
            self._last_user = clean
        self._chat.append((speaker, clean, error))
        if len(self._chat) > 80:
            self._chat = self._chat[-80:]
        if self._chat_follow:
            self._chat_offset = 0.0

    def _retry(self) -> None:
        command = self._last_user.strip()
        if not command:
            return
        self._set_status("THINKING")
        self.queue.put(command)

    def _scroll_chat(self, event) -> str:
        self._chat_offset += event.delta / 120 * 42
        self._chat_offset = max(0.0, min(self._chat_limit, self._chat_offset))
        self._chat_follow = self._chat_offset <= 2
        return "break"

    def _chat_rows(self, width: int) -> list[tuple[str, str, int]]:
        inner = max(80, width - 4)
        rows: list[tuple[str, str, int]] = []
        labels = {"you": "#8fd8ff", "jarvis": CYAN}
        for speaker, body in self._chat:
            rows.append((speaker, labels.get(speaker.lower(), SOFT), 18))
            for line in self._wrap_chat(body, inner):
                rows.append((line, INK, 18))
            rows.append(("", INK, 10))
        return rows

    def _wrap_chat(self, text: str, width: int) -> list[str]:
        words = text.split()
        if not words:
            return [""]
        lines = []
        current = words[0]
        for word in words[1:]:
            trial = f"{current} {word}"
            if self._chat_font.measure(trial) <= width:
                current = trial
            else:
                lines.append(current)
                current = word
        lines.append(current)
        return lines

    def _chat_height(self, rows: list[tuple[str, str, int]]) -> int:
        return sum(step for _text, _color, step in rows)

    def _blend_chat(self, color: str, amount: float) -> str:
        amount = max(0.0, min(1.0, amount))
        fg = tuple(int(color[index : index + 2], 16) for index in (1, 3, 5))
        bg = tuple(int(BG[index : index + 2], 16) for index in (1, 3, 5))
        parts = [int(bg[i] + (fg[i] - bg[i]) * amount) for i in range(3)]
        return f"#{parts[0]:02x}{parts[1]:02x}{parts[2]:02x}"

    def _chat_fade(self, y: float, view: int, fade_top: bool, fade_bottom: bool) -> float:
        if fade_top and y < 46:
            amount = y / 46
        elif fade_bottom and y > view - 34:
            amount = (view - y) / 34
        else:
            amount = 1.0
        amount = max(0.0, min(1.0, amount))
        return amount * amount * (3 - 2 * amount)

    def _draw_chat(self) -> None:
        canvas = self.chat
        canvas.delete("all")
        width = max(canvas.winfo_width(), 80)
        view = max(canvas.winfo_height(), 1)
        rows = self._chat_rows(width)
        content_h = self._chat_height(rows)
        limit = max(0, content_h - view)
        if self._chat_follow:
            self._chat_offset = 0.0
        else:
            self._chat_offset = max(0.0, min(float(limit), self._chat_offset))
        fading = content_h > view + 8
        fade_top = fading and self._chat_offset < limit - 2
        fade_bottom = fading and self._chat_offset > 2
        y = view - content_h + self._chat_offset
        for text, color, step in rows:
            if text and -step < y < view:
                amount = self._chat_fade(y + step / 2, view, fade_top, fade_bottom)
                if amount > 0.05:
                    canvas.create_text(
                        0,
                        y,
                        text=text,
                        anchor="nw",
                        fill=self._blend_chat(color, amount),
                        font=self._chat_font,
                    )
            y += step

    def _on_assistant_line(self, text: str) -> None:
        self.ui_queue.put(("line", "JARVIS", text))

    def _clear_placeholder(self, _event=None) -> None:
        if self._entry_empty:
            self.entry.delete(0, "end")
            self.entry.configure(fg=INK)
            self._entry_empty = False

    def _restore_placeholder(self, _event=None) -> None:
        if not self.entry.get().strip():
            self.entry.delete(0, "end")
            self.entry.insert(0, self._placeholder)
            self.entry.configure(fg=DIM)
            self._entry_empty = True

    def _draw_ask(self) -> None:
        canvas = self.ask
        canvas.delete("all")
        width = max(canvas.winfo_width(), 320)
        height = 58
        y1, y2 = 8, height - 8
        mic = 40
        gap = 10
        x2 = width - mic - gap
        x1 = 0
        radius = (y2 - y1) / 2
        field = "#071820"
        canvas.create_oval(x1, y1, x1 + radius * 2, y2, fill=field, outline="")
        canvas.create_oval(x2 - radius * 2, y1, x2, y2, fill=field, outline="")
        canvas.create_rectangle(x1 + radius, y1, x2 - radius, y2, fill=field, outline="")
        canvas.create_arc(x1, y1, x1 + radius * 2, y2, start=90, extent=180, style="arc", outline="#1c4d5e")
        canvas.create_arc(x2 - radius * 2, y1, x2, y2, start=270, extent=180, style="arc", outline="#1c4d5e")
        canvas.create_line(x1 + radius, y1, x2 - radius, y1, fill="#1c4d5e")
        canvas.create_line(x1 + radius, y2, x2 - radius, y2, fill="#1c4d5e")
        canvas.create_window(x1 + 16, (y1 + y2) / 2, window=self.entry, anchor="w", width=max(40, x2 - x1 - 52))
        arrow_x = x2 - 18
        arrow_y = (y1 + y2) / 2
        canvas.create_rectangle(arrow_x - 14, y1 + 4, arrow_x + 14, y2 - 4, fill=field, outline="", tags="send")
        canvas.create_line(arrow_x, arrow_y + 6, arrow_x, arrow_y - 5, fill=SOFT, width=2, tags="send")
        canvas.create_line(arrow_x - 5, arrow_y - 1, arrow_x, arrow_y - 6, fill=SOFT, width=2, tags="send")
        canvas.create_line(arrow_x + 5, arrow_y - 1, arrow_x, arrow_y - 6, fill=SOFT, width=2, tags="send")
        mic_x = width - mic / 2
        listening = self.listen_button.state == "disabled"
        canvas.create_oval(mic_x - 20, y1, mic_x + 20, y2, fill=DIM if listening else CYAN, outline="", tags="mic")
        head_top = arrow_y - 7
        canvas.create_oval(mic_x - 4, head_top, mic_x + 4, head_top + 12, fill="#031018", outline="", tags="mic")
        canvas.create_arc(mic_x - 7, head_top + 4, mic_x + 7, head_top + 16, start=200, extent=140, style="arc", outline="#031018", width=2, tags="mic")
        canvas.create_line(mic_x, head_top + 15, mic_x, head_top + 19, fill="#031018", width=2, tags="mic")
        canvas.create_line(mic_x - 5, head_top + 19, mic_x + 5, head_top + 19, fill="#031018", width=2, tags="mic")

    def _send(self, text: str | None = None) -> None:
        if text is None:
            if self._entry_empty:
                return
            command = self.entry.get().strip()
            if not command or command == self._placeholder:
                return
            self.entry.delete(0, "end")
            self.entry.configure(fg=INK)
            if self.root.focus_get() is not self.entry:
                self._restore_placeholder()
        else:
            command = text.strip()
            if not command:
                return
        self._append("You", command)
        self.queue.put(command)

    def _listen(self) -> None:
        if self.listen_button.state == "disabled":
            return
        self.listen_button.configure(state="disabled")
        self._set_status("Listening")

        def work() -> None:
            from input_engine import _listen_voice

            heard = _listen_voice()
            self.ui_queue.put(("listen-finished", heard))

        threading.Thread(target=work, daemon=True).start()

    def _run_assistant(self) -> None:
        init_memory()
        self._set_status("READY")
        WishMe()
        from reminders import start_reminders

        start_reminders()
        while not self._closing:
            query = listen()
            if self._closing or query == "exit":
                self.ui_queue.put(("close",))
                break
            if query == "none":
                continue
            self._set_status("THINKING")
            try:
                result = handle_query(query)
            except Exception as exc:
                speak("Something went wrong while running that command.")
                print(f"[UI] {exc}")
                result = ""
            self._set_status("READY")
            if result == "exit":
                self.ui_queue.put(("close",))
                break

    def _watch_system(self) -> None:
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
        while not self._closing:
            cpu = None
            mem = None
            down = None
            up = None
            before = None
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
            self.ui_queue.put(("track", info))
            self.ui_queue.put(("stats", cpu, mem, links, {
                "ip": ip,
                "wan": wan,
                "name": name,
                "down": down,
                "up": up,
                "online": online,
            }))

    def _close(self) -> None:
        if self._closing:
            return
        self._closing = True
        self.queue.put("exit")
        self.root.destroy()


def _countdown_text(seconds: int) -> str:
    seconds = max(0, int(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


APP_ID = "Sthembiso.JARVIS"


def _windows_app_id() -> None:
    """Match the Start menu shortcut so a taskbar pin keeps the JARVIS script."""
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except Exception:
        pass


def launch() -> None:
    _windows_app_id()
    root = tk.Tk()
    JarvisWindow(root)
    root.update_idletasks()
    root.lift()
    root.attributes("-topmost", True)
    root.after(800, lambda: root.attributes("-topmost", False))
    root.focus_force()
    root.mainloop()
