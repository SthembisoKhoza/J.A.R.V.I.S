"""Applications JARVIS is allowed to open.

The list is database/apps.json next to this file. Say "allow Steam" or
"remove Steam" to change it, or edit the file.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

DEFAULT_APPS = [
    {
        "name": "Spotify",
        "match": ["spotify"],
        "process": ["Spotify.exe"],
        "app_id": "SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify",
    },
    {
        "name": "Notepad",
        "match": ["notepad"],
        "process": ["notepad.exe"],
        "paths": [r"C:\Windows\System32\notepad.exe"],
    },
    {
        "name": "Calculator",
        "match": ["calculator", "calc"],
        "process": ["CalculatorApp.exe", "calc.exe"],
        "paths": ["calc.exe"],
    },
    {
        "name": "File Explorer",
        "match": ["file explorer", "explorer", "files"],
        "process": ["explorer.exe"],
        "paths": ["explorer.exe"],
    },
    {
        "name": "Microsoft Edge",
        "match": ["edge", "microsoft edge", "browser"],
        "process": ["msedge.exe"],
        "paths": [
            r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
            r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe",
        ],
    },
    {
        "name": "Google Chrome",
        "match": ["chrome", "google chrome"],
        "process": ["chrome.exe"],
        "paths": [
            r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
            r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
            r"%LocalAppData%\Google\Chrome\Application\chrome.exe",
        ],
    },
    {
        "name": "Discord",
        "match": ["discord"],
        "process": ["Discord.exe"],
        "paths": [r"%LocalAppData%\Discord\Discord.exe"],
    },
    {
        "name": "Visual Studio Code",
        "match": ["visual studio code", "vs code", "vscode", "code"],
        "process": ["Code.exe"],
        "paths": [
            r"%LocalAppData%\Programs\Microsoft VS Code\Code.exe",
            r"%ProgramFiles%\Microsoft VS Code\Code.exe",
        ],
    },
    {
        "name": "Task Manager",
        "match": ["task manager", "taskmanager"],
        "process": ["Taskmgr.exe"],
        "paths": ["taskmgr.exe"],
    },
    {
        "name": "Outlook",
        "match": ["outlook", "email", "emails", "e-mail", "inbox", "mail"],
        "process": ["olk.exe"],
        "app_id": "Microsoft.OutlookForWindows_8wekyb3d8bbwe!Microsoft.OutlookforWindows",
    },
    {
        "name": "WhatsApp",
        "match": ["whatsapp", "whats app"],
        "process": ["WhatsApp.Root.exe"],
        "app_id": "5319275A.WhatsAppDesktop_cv1g1gvanyjgm!App",
    },
]


def apps_path() -> Path:
    override = os.environ.get("JARVIS_APPS_PATH", "").strip()
    if override:
        return Path(override)
    return Path(__file__).resolve().parent / "database" / "apps.json"


def _normalize(entry: dict) -> dict | None:
    name = str(entry.get("name") or "").strip()
    if not name:
        return None
    match = entry.get("match") or []
    if isinstance(match, str):
        match = [match]
    aliases = []
    for item in [name, *match]:
        text = " ".join(str(item).lower().split())
        if text and text not in aliases:
            aliases.append(text)
    process = entry.get("process") or []
    if isinstance(process, str):
        process = [process]
    paths = entry.get("paths") or []
    if isinstance(paths, str):
        paths = [paths]
    cleaned = {
        "name": name,
        "match": aliases,
        "process": [str(item) for item in process if str(item).strip()],
        "paths": [str(item) for item in paths if str(item).strip()],
    }
    if entry.get("app_id"):
        cleaned["app_id"] = str(entry["app_id"])
    if entry.get("target"):
        cleaned["target"] = str(entry["target"])
        cleaned["kind"] = str(entry.get("kind") or "")
    return cleaned


def load_apps() -> list[dict]:
    path = apps_path()
    if not path.exists():
        save_apps([item for item in map(_normalize, DEFAULT_APPS) if item])
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[Apps] Could not read {path.name}: {exc}")
        return [item for item in (_normalize(entry) for entry in DEFAULT_APPS) if item]
    rows = payload.get("apps") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        return []
    return [item for item in (_normalize(row) for row in rows if isinstance(row, dict)) if item]


def save_apps(apps: list[dict]) -> None:
    path = apps_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"apps": apps}, indent=2), encoding="utf-8")


def find_allowed(request: str) -> dict | None:
    query = " ".join(request.lower().split())
    if not query:
        return None
    exact = None
    partial = None
    for app in load_apps():
        aliases = app["match"]
        if query in aliases:
            exact = app
            break
        if len(query) >= 4 and any(alias.startswith(query) or query.startswith(alias) for alias in aliases):
            partial = partial or app
    return exact or partial


def allowed_sentence() -> str:
    names = [app["name"] for app in load_apps()]
    if not names:
        return "I am not allowed to open any apps."
    if len(names) == 1:
        return f"I can open {names[0]}."
    return "I can open " + ", ".join(names[:-1]) + f", and {names[-1]}."


def allow_app(request: str) -> str:
    request = " ".join(request.split())
    if not request:
        return "Which app should I allow?"
    existing = find_allowed(request)
    if existing is not None:
        return f"{existing['name']} is already allowed."
    try:
        from pcautomati0n import _match_app
    except Exception as exc:
        print(f"[Apps] {exc}")
        return "I could not check the installed apps."
    match = _match_app(request)
    if match is None:
        return f"I could not find {request}, so I did not allow it."
    name, target, kind = match
    apps = load_apps()
    apps.append(
        {
            "name": name,
            "match": [name.lower(), request.lower()] if request.lower() != name.lower() else [name.lower()],
            "process": [],
            "paths": [],
            "target": target,
            "kind": kind,
        }
    )
    save_apps(apps)
    return f"{name} is now allowed."


def block_app(request: str) -> str:
    request = " ".join(request.split())
    if not request:
        return "Which app should I remove?"
    query = request.lower()
    apps = load_apps()
    kept = []
    removed = ""
    for app in apps:
        aliases = app["match"]
        if query in aliases or query == app["name"].lower():
            removed = app["name"]
            continue
        kept.append(app)
    if not removed:
        return f"{request} is not on the list."
    save_apps(kept)
    return f"{removed} is no longer allowed."
