"""Timers and clock reminders.

Saved in database/reminders.json so a reminder still fires if JARVIS
was closed and opened again before it was due.
"""

from __future__ import annotations

import datetime
import json
import os
import re
import threading
import uuid
from pathlib import Path

from input_engine import listen
from speech_engine import speak

_LOCK = threading.Lock()
_WAKE = threading.Event()
_ITEMS: list[dict] = []
_STARTED = False
_STARTED_AT: datetime.datetime | None = None
_TIME_SOURCE = None
_ALERT_TEXT = ""
_ALERT_UNTIL: datetime.datetime | None = None

_WORDS = {
    "a": 1,
    "an": 1,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "ninety": 90,
}
_UNIT_SECONDS = {
    "hour": 3600,
    "hours": 3600,
    "hr": 3600,
    "hrs": 3600,
    "minute": 60,
    "minutes": 60,
    "min": 60,
    "mins": 60,
    "second": 1,
    "seconds": 1,
    "sec": 1,
    "secs": 1,
}
_MAX_SECONDS = 7 * 24 * 3600
_NUMBER = r"(\d+(?:\.\d+)?|" + "|".join(_WORDS) + r")"
_DURATION = re.compile(
    rf"\b{_NUMBER}\s*(hours?|hrs?|minutes?|mins?|seconds?|secs?)\b",
    re.I,
)


def _now() -> datetime.datetime:
    if _TIME_SOURCE is not None:
        return _TIME_SOURCE()
    return datetime.datetime.now()


def _path() -> Path:
    override = os.environ.get("JARVIS_REMINDERS_PATH", "").strip()
    if override:
        return Path(override)
    return Path(__file__).resolve().parent / "database" / "reminders.json"


def _load() -> None:
    global _ITEMS
    path = _path()
    if not path.exists():
        _ITEMS = []
        return
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
        _ITEMS = list(loaded.get("items", []))
    except Exception:
        _ITEMS = []


def _save() -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"items": _ITEMS}, indent=2), encoding="utf-8")


def _parse_due(value: str) -> datetime.datetime:
    return datetime.datetime.fromisoformat(value)


def _clock_text(moment: datetime.datetime) -> str:
    text = moment.strftime("%I:%M %p")
    if text.startswith("0"):
        text = text[1:]
    return text


def _when_phrase(due: datetime.datetime, now: datetime.datetime) -> str:
    clock = _clock_text(due)
    if due.date() == now.date():
        return f"at {clock}"
    if due.date() == now.date() + datetime.timedelta(days=1):
        return f"tomorrow at {clock}"
    return f"on {due.strftime('%A')} at {clock}"


def _human_seconds(seconds: int) -> str:
    seconds = int(seconds)
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    parts: list[str] = []
    if hours:
        parts.append(f"{hours} hour" + ("s" if hours != 1 else ""))
    if minutes:
        parts.append(f"{minutes} minute" + ("s" if minutes != 1 else ""))
    if secs or not parts:
        parts.append(f"{secs} second" + ("s" if secs != 1 else ""))
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return f"{parts[0]} and {parts[1]}"
    return f"{parts[0]}, {parts[1]}, and {parts[2]}"


def _remaining_phrase(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 45:
        unit = "second" if seconds == 1 else "seconds"
        return f"{seconds} {unit}"
    minutes = int(round(seconds / 60))
    if minutes < 60:
        unit = "minute" if minutes == 1 else "minutes"
        return f"{minutes} {unit}"
    return _human_seconds(minutes * 60)


def _normalize(query: str) -> str:
    text = query.lower().strip()
    text = text.replace("o'clock", " ").replace("oclock", " ")
    text = text.replace(".", "")
    text = re.sub(r"[!?]", "", text)
    text = re.sub(r"^(please|can you|could you|jarvis)\s+", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def parse_duration_seconds(text: str) -> int | None:
    cleaned = text.lower().strip()
    cleaned = re.sub(
        r"\b(an|a|one|\d+)\s+hours?\s+and\s+a\s+half\b",
        r"\1 hour 30 minutes",
        cleaned,
    )
    cleaned = cleaned.replace("half an hour", "30 minutes").replace("half hour", "30 minutes")
    total = 0.0
    found = False
    for match in _DURATION.finditer(cleaned):
        raw = match.group(1).lower()
        amount = float(raw) if raw[:1].isdigit() else float(_WORDS[raw])
        total += amount * _UNIT_SECONDS[match.group(2).lower()]
        found = True
    if not found or total < 1:
        return None
    return int(round(total))


def parse_clock(text: str, now: datetime.datetime) -> datetime.datetime | None:
    cleaned = text.lower().strip()
    hint = ""
    if re.search(r"\b(afternoon|evening|tonight)\b", cleaned):
        hint = "pm"
    elif re.search(r"\bmorning\b", cleaned):
        hint = "am"
    cleaned = re.sub(r"\b(in the )?(morning|afternoon|evening|tonight)\b", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    clock24 = False
    if cleaned in {"noon", "midday"}:
        hour, minute, ampm = 12, 0, "pm"
    elif cleaned == "midnight":
        hour, minute, ampm = 12, 0, "am"
    else:
        match = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", cleaned)
        if not match:
            return None
        hour_token = match.group(1)
        hour = int(hour_token)
        minute = int(match.group(2) or 0)
        ampm = match.group(3) or hint or ""
        clock24 = len(hour_token) == 2 or hour >= 13 or hour == 0
    if minute > 59 or hour > 23:
        return None
    if ampm and hour > 12:
        return None

    if ampm:
        if hour == 0:
            return None
        hour_24 = hour % 12
        if ampm == "pm":
            hour_24 += 12
        candidate = now.replace(hour=hour_24, minute=minute, second=0, microsecond=0)
        if candidate <= now:
            candidate += datetime.timedelta(days=1)
        return candidate

    if clock24:
        candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= now:
            candidate += datetime.timedelta(days=1)
        return candidate

    options = []
    for extra in (0, 12):
        hour_24 = (hour % 12) + extra
        candidate = now.replace(hour=hour_24, minute=minute, second=0, microsecond=0)
        if candidate <= now:
            candidate += datetime.timedelta(days=1)
        options.append(candidate)
    return min(options)


def _flash() -> None:
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        hwnd = user32.FindWindowW(None, "J.A.R.V.I.S")
        if not hwnd:
            return

        class FLASHWINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.UINT),
                ("hwnd", wintypes.HWND),
                ("dwFlags", wintypes.DWORD),
                ("uCount", wintypes.UINT),
                ("dwTimeout", wintypes.DWORD),
            ]

        info = FLASHWINFO(ctypes.sizeof(FLASHWINFO), hwnd, 3, 5, 0)
        user32.FlashWindowEx(ctypes.byref(info))
    except Exception:
        pass


def _announcement(items: list[dict], missed: bool) -> str:
    if len(items) == 1:
        item = items[0]
        if item["kind"] == "timer":
            if item.get("name"):
                line = f"Sir, you have finished {item['name']}."
                if missed:
                    line = f"Sir, your time to finish {item['name']} ended while I was closed."
            else:
                length = item.get("length") or item["label"]
                line = f"Sir, your timer for {length} is done."
                if missed:
                    line = f"Sir, your timer for {length} finished while I was closed."
        else:
            line = f"Sir, this is your reminder to {item['label']}."
            if missed:
                line = f"Sir, this reminder came due while I was closed: {item['label']}."
        return line

    parts = []
    for item in items:
        if item["kind"] == "timer":
            if item.get("name"):
                parts.append(f"you have finished {item['name']}")
            else:
                parts.append(f"your timer for {item.get('length') or item['label']} is done")
        else:
            parts.append(f"reminder to {item['label']}")
    joined = ". ".join(part[0].upper() + part[1:] for part in parts)
    if missed:
        return f"Sir, while I was closed, these came due. {joined}."
    return f"Sir. {joined}."


def _warn_moment(item: dict) -> datetime.datetime | None:
    """When a named timer should say that 2 minutes are left."""
    if item.get("kind") != "timer" or not item.get("name") or item.get("warned"):
        return None
    if not item.get("created"):
        return None
    due = _parse_due(item["due"])
    total = (due - _parse_due(item["created"])).total_seconds()
    if total <= 120:
        return None
    return due - datetime.timedelta(seconds=120)


def _soonest(now: datetime.datetime) -> datetime.datetime | None:
    moments: list[datetime.datetime] = []
    for item in _ITEMS:
        due = _parse_due(item["due"])
        moments.append(due)
        warn_at = _warn_moment(item)
        if warn_at is None or warn_at >= due:
            continue
        moments.append(warn_at if warn_at > now else now)
    if not moments:
        return None
    return min(moments)


def _warning_line(items: list[dict], now: datetime.datetime) -> str:
    parts = []
    for item in items:
        left = max(0, int((_parse_due(item["due"]) - now).total_seconds()))
        amount = "2 minutes" if 90 <= left <= 150 else _remaining_phrase(left)
        parts.append(f"You are left with {amount} to finish {item['name']}, sir.")
    return " ".join(parts)


def _fire_due() -> None:
    now = _now()
    warnings: list[dict] = []
    due: list[dict] = []
    with _LOCK:
        for item in list(_ITEMS):
            when = _parse_due(item["due"])
            if when <= now:
                due.append(item)
                continue
            warn_at = _warn_moment(item)
            if warn_at is not None and warn_at <= now:
                item["warned"] = True
                warnings.append(dict(item))
        if due:
            finished = {item["id"] for item in due}
            _ITEMS[:] = [item for item in _ITEMS if item["id"] not in finished]
        if warnings or due:
            _save()
        started = _STARTED_AT or now
        missed = bool(due) and all(
            _parse_due(item.get("created") or item["due"]) < started - datetime.timedelta(seconds=15)
            for item in due
        )
    if not warnings and not due:
        return
    if warnings and not due:
        names = "  ".join(item["name"].upper() for item in warnings)
        _arm_text(f"2 MINUTES LEFT  {names}")
    elif due:
        _arm_alert(due)
    _flash()
    spoken = []
    if warnings:
        spoken.append(_warning_line(warnings, now))
    if due:
        spoken.append(_announcement(due, missed))
    speak(" ".join(spoken), blocking=False)


def _arm_text(text: str) -> None:
    global _ALERT_TEXT, _ALERT_UNTIL
    _ALERT_TEXT = text
    _ALERT_UNTIL = _now() + datetime.timedelta(seconds=25)


def _arm_alert(items: list[dict]) -> None:
    if len(items) == 1 and items[0]["kind"] == "timer":
        if items[0].get("name"):
            text = "FINISHED  " + items[0]["name"].upper()
        else:
            text = "TIMER DONE"
    elif len(items) == 1:
        text = "REMINDER  " + items[0]["label"].upper()
    else:
        text = "REMINDERS DUE"
    _arm_text(text)


def screen_state() -> dict:
    """What the window should draw: active countdown plus a short on-screen alert."""
    now = _now()
    with _LOCK:
        items = list(_ITEMS)
        alert = _ALERT_TEXT if _ALERT_UNTIL and now < _ALERT_UNTIL else ""
    view = []
    for item in sorted(items, key=lambda entry: entry["due"]):
        due = _parse_due(item["due"])
        created = _parse_due(item["created"]) if item.get("created") else due
        total = max(1, int((due - created).total_seconds()))
        left = max(0, int((due - now).total_seconds()))
        view.append(
            {
                "kind": item["kind"],
                "label": item["label"],
                "left": left,
                "total": total,
                "when": _clock_text(due),
            }
        )
    return {"items": view, "alert": alert}


def _loop() -> None:
    while True:
        with _LOCK:
            future = _soonest(_now())
        if future is None:
            _WAKE.wait()
            _WAKE.clear()
            continue
        delay = (future - _now()).total_seconds()
        if delay > 0 and _WAKE.wait(timeout=delay):
            _WAKE.clear()
            continue
        _fire_due()


def start_reminders() -> None:
    """Load saved reminders and watch the clock. Safe to call more than once."""
    global _STARTED, _STARTED_AT
    with _LOCK:
        if _STARTED:
            _WAKE.set()
            return
        _load()
        _STARTED = True
        _STARTED_AT = _now()
    threading.Thread(target=_loop, name="jarvis-reminders", daemon=True).start()
    _WAKE.set()


def _add(kind: str, label: str, due: datetime.datetime, name: str = "", length: str = "") -> None:
    item = {
        "id": uuid.uuid4().hex[:8],
        "kind": kind,
        "label": label,
        "name": name,
        "length": length,
        "warned": False,
        "due": due.isoformat(timespec="seconds"),
        "created": _now().isoformat(timespec="seconds"),
    }
    with _LOCK:
        _ITEMS.append(item)
        _save()
    start_reminders()


def _sorted_items() -> list[dict]:
    with _LOCK:
        items = list(_ITEMS)
    items.sort(key=lambda item: item["due"])
    return items


def _describe(item: dict, now: datetime.datetime) -> str:
    due = _parse_due(item["due"])
    if item["kind"] == "timer":
        left = (due - now).total_seconds()
        if item.get("name"):
            return f"A timer to finish {item['name']}, with {_remaining_phrase(left)} left."
        length = item.get("length") or item["label"]
        return f"A timer for {length}, with {_remaining_phrase(left)} left."
    phrase = _when_phrase(due, now)
    return f"{phrase[0].upper()}{phrase[1:]}, {item['label']}."


def _list_items() -> None:
    now = _now()
    items = _sorted_items()
    if not items:
        speak("You have no reminders or timers set, sir.")
        return
    lines = [_describe(item, now) for item in items[:5]]
    extra = ""
    if len(items) > 5:
        extra = f" And {len(items) - 5} more."
    count = "1 thing" if len(items) == 1 else f"{len(items)} things"
    speak(f"You have {count} set, sir. " + " ".join(lines) + extra)


def _cancel(kind: str | None, label: str = "") -> None:
    label = label.strip()
    with _LOCK:
        chosen = list(_ITEMS)
        if kind:
            chosen = [item for item in chosen if item["kind"] == kind]
        if label:
            chosen = [
                item
                for item in chosen
                if label in item.get("label", "") or label in item.get("name", "")
            ]
        if not chosen:
            if kind == "timer":
                speak("You have no timer set, sir.")
            else:
                speak("You have no reminder set, sir.")
            return
        if label or len(chosen) == 1:
            remove = chosen
        else:
            remove = [min(chosen, key=lambda item: item["due"])]
        ids = {item["id"] for item in remove}
        _ITEMS[:] = [item for item in _ITEMS if item["id"] not in ids]
        _save()
        left = len(_ITEMS)
    _WAKE.set()
    name = remove[0]["label"]
    if remove[0]["kind"] == "timer":
        message = f"Timer for {name} cancelled, sir."
        clear_word = "timers"
    else:
        message = f"Reminder to {name} cancelled, sir."
        clear_word = "reminders"
    if left and not label and len(chosen) > 1:
        message += f" You still have {left} set. Say cancel all {clear_word} to clear them."
    speak(message)


def _cancel_all(kind: str | None) -> None:
    with _LOCK:
        if kind:
            before = len(_ITEMS)
            _ITEMS[:] = [item for item in _ITEMS if item["kind"] != kind]
            removed = before - len(_ITEMS)
        else:
            removed = len(_ITEMS)
            _ITEMS.clear()
        _save()
    _WAKE.set()
    if not removed:
        speak("There was nothing to cancel, sir.")
        return
    if kind == "timer":
        speak("All timers cancelled, sir." if removed != 1 else "Timer cancelled, sir.")
    elif kind == "reminder":
        speak("All reminders cancelled, sir." if removed != 1 else "Reminder cancelled, sir.")
    else:
        speak("Reminders and timers cancelled, sir.")


def _clean_activity(text: str) -> str:
    cleaned = text.strip(" .")
    cleaned = re.sub(r"^(that\s+)?(i am|i'm|im)\s+", "", cleaned)
    cleaned = re.sub(r"^(to\s+)?finish\s+", "", cleaned)
    cleaned = re.sub(r"^(a|an|the)\s+", "", cleaned)
    return cleaned.strip()


def _split_timer_request(text: str) -> tuple[str, str]:
    named = re.search(r"\s+(?:say|called|named|name)\s+(.+)$", text)
    if named:
        return text[: named.start()].strip(), _clean_activity(named.group(1))
    purpose = re.search(r"\s+(?:for|to)\s+(.+)$", text)
    if purpose:
        left = text[: purpose.start()].strip()
        if parse_duration_seconds(left):
            return left, _clean_activity(purpose.group(1))
    return text.strip(), ""


def _set_timer(duration_text: str) -> bool:
    duration_text, activity = _split_timer_request(duration_text)
    seconds = parse_duration_seconds(duration_text)
    if seconds is None:
        speak("Tell me a length of time, sir, such as 10 minutes.")
        return True
    if seconds > _MAX_SECONDS:
        speak("I can set a timer up to 7 days, sir.")
        return True
    length = _human_seconds(seconds)
    due = _now() + datetime.timedelta(seconds=seconds)
    _add("timer", activity or length, due, name=activity, length=length)
    if activity and seconds > 120:
        speak(
            f"Timer set for {length} to finish {activity}, sir. "
            "I will tell you when 2 minutes are left."
        )
    elif activity:
        speak(f"Timer set for {length} to finish {activity}, sir.")
    else:
        speak(f"Timer set for {length}, sir.")
    return True


def _set_reminder(due: datetime.datetime | None, label: str) -> bool:
    label = re.sub(r"\s+", " ", label).strip(" .")
    if due is None:
        speak("I did not catch the time, sir. Try 3 PM or 15:00.")
        return True
    if not label:
        speak("What should I remind you to do, sir?")
        heard = listen().strip()
        label = re.sub(r"\s+", " ", heard).strip(" .")
        if not label or label == "none":
            speak("I did not catch that.")
            return True
    _add("reminder", label, due)
    speak(f"I will remind you {_when_phrase(due, _now())} to {label}, sir.")
    return True


def handle_reminder(query: str) -> bool:
    """Run a reminder or timer command. Return False when it is something else."""
    text = _normalize(query)
    if text in {
        "what are my reminders",
        "what reminders do i have",
        "my reminders",
        "list reminders",
        "show reminders",
        "what timers do i have",
        "my timers",
        "list timers",
        "what do i have set",
    }:
        _list_items()
        return True

    if text in {
        "how long left",
        "how long is left",
        "how much time is left",
        "time left",
        "time left on the timer",
        "how long left on the timer",
        "how long is left on the timer",
    }:
        now = _now()
        timers = [item for item in _sorted_items() if item["kind"] == "timer"]
        if not timers:
            speak("You have no timer set, sir.")
            return True
        due = _parse_due(timers[0]["due"])
        left = _remaining_phrase((due - now).total_seconds())
        if timers[0].get("name"):
            speak(f"You are left with {left} to finish {timers[0]['name']}, sir.")
        else:
            length = timers[0].get("length") or timers[0]["label"]
            speak(f"{left.capitalize()} left on your timer for {length}, sir.")
        return True

    if text in {"cancel all", "cancel everything", "clear reminders", "cancel all reminders and timers"}:
        _cancel_all(None)
        return True
    if text in {"cancel all timers", "clear timers"}:
        _cancel_all("timer")
        return True
    if text in {"cancel all reminders", "clear all reminders"}:
        _cancel_all("reminder")
        return True

    cancel_timer = re.fullmatch(r"(?:cancel|stop|clear)(?: the| my)? timer(?: for (.+))?", text)
    if cancel_timer:
        _cancel("timer", cancel_timer.group(1) or "")
        return True
    cancel_reminder = re.fullmatch(
        r"(?:cancel|clear)(?: the| my)? reminders?(?: to (.+))?",
        text,
    )
    if cancel_reminder:
        _cancel("reminder", cancel_reminder.group(1) or "")
        return True

    for pattern in (
        r"^(?:set|start)\s+(?:a\s+|an\s+)?timer(?:\s+for)?\s+(.+)$",
        r"^timer(?:\s+for)?\s+(.+)$",
        r"^(?:set|start)\s+(?:a\s+|an\s+)?(.+?)\s+timer$",
        r"^(.+?)\s+timer$",
    ):
        match = re.fullmatch(pattern, text)
        if match and "cancel" not in text and "stop" not in text:
            return _set_timer(match.group(1))

    match = re.fullmatch(r"(?:set|add|create)(?: a)? reminder to\s+(.+)\s+at\s+(.+)", text)
    if match:
        return _set_reminder(parse_clock(match.group(2), _now()), match.group(1))
    match = re.fullmatch(r"(?:set|add|create)(?: a)? reminder at\s+(.+?)\s+to\s+(.+)", text)
    if match:
        return _set_reminder(parse_clock(match.group(1), _now()), match.group(2))
    match = re.fullmatch(r"(?:set|add|create)(?: a)? reminder for\s+(.+)\s+at\s+(.+)", text)
    if match:
        return _set_reminder(parse_clock(match.group(2), _now()), match.group(1))
    match = re.fullmatch(r"reminder to\s+(.+)\s+at\s+(.+)", text)
    if match:
        return _set_reminder(parse_clock(match.group(2), _now()), match.group(1))

    match = re.fullmatch(r"remind me at\s+(.+?)\s+to\s+(.+)", text)
    if match:
        return _set_reminder(parse_clock(match.group(1), _now()), match.group(2))
    match = re.fullmatch(r"remind me in\s+(.+?)\s+to\s+(.+)", text)
    if match:
        seconds = parse_duration_seconds(match.group(1))
        if seconds is None:
            speak("Tell me a length of time, sir, such as 10 minutes.")
            return True
        if seconds > _MAX_SECONDS:
            speak("I can set a reminder up to 7 days ahead, sir.")
            return True
        due = _now() + datetime.timedelta(seconds=seconds)
        return _set_reminder(due, match.group(2))
    match = re.fullmatch(r"remind me to\s+(.+)\s+at\s+(.+)", text)
    if match:
        return _set_reminder(parse_clock(match.group(2), _now()), match.group(1))
    match = re.fullmatch(r"remind me to\s+(.+)\s+in\s+(.+)", text)
    if match:
        seconds = parse_duration_seconds(match.group(2))
        if seconds is None:
            speak("Tell me a length of time, sir, such as 10 minutes.")
            return True
        if seconds > _MAX_SECONDS:
            speak("I can set a reminder up to 7 days ahead, sir.")
            return True
        due = _now() + datetime.timedelta(seconds=seconds)
        return _set_reminder(due, match.group(1))
    match = re.fullmatch(r"remind me at\s+(.+)", text)
    if match:
        return _set_reminder(parse_clock(match.group(1), _now()), "")

    if text.startswith("remind") or "reminder" in text:
        speak("Try remind me to study at 13:00, or set reminder to call Mom at 09:00.")
        return True
    return False
