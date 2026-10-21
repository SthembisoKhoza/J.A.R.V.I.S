"""Small on-disk memory for JARVIS.

Facts are stored as JSON in database/memory.json so the assistant works
without Chroma or a downloaded embedding model.
"""

from __future__ import annotations

import datetime
import json
import os
from pathlib import Path
from typing import Optional

_PROJECT_DIR = Path(__file__).resolve().parent
_STORE_PATH = _PROJECT_DIR / "database" / "memory.json"
_DEFAULT_K = int(os.getenv("JARVIS_MEMORY_TOP_K", "3"))
_items: list[dict] = []
_is_initialized = False


def init_memory() -> None:
    global _items, _is_initialized
    if _is_initialized:
        return
    _STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    if _STORE_PATH.exists():
        try:
            loaded = json.loads(_STORE_PATH.read_text(encoding="utf-8"))
            _items = list(loaded.get("items", []))
        except Exception:
            _items = []
    else:
        _items = []
    _is_initialized = True
    print(f"[Memory] Loaded {len(_items)} memories from {_STORE_PATH}")


def _save() -> None:
    _STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _STORE_PATH.write_text(
        json.dumps({"items": _items}, indent=2),
        encoding="utf-8",
    )


def _today() -> str:
    return datetime.date.today().isoformat()


def add_memory(text: str) -> str:
    if not _is_initialized:
        init_memory()
    cleaned = text.strip().rstrip(".")
    bullet = f"- {cleaned} ({_today()})"
    _items.append({"text": bullet, "date": _today(), "raw": text.strip()})
    _save()
    print(f"[Memory] Stored: {bullet}")
    return bullet


def search_memory(query: str, k: Optional[int] = None) -> list[str]:
    if not _is_initialized:
        init_memory()
    k = k or _DEFAULT_K
    if not _items:
        return []

    words = {word for word in query.lower().split() if len(word) > 2}
    scored: list[tuple[int, dict]] = []
    for item in _items:
        haystack = item.get("text", "").lower()
        score = sum(1 for word in words if word in haystack)
        scored.append((score, item))
    scored.sort(key=lambda pair: (pair[0], pair[1].get("date", "")), reverse=True)
    if scored[0][0] == 0:
        return [item["text"] for item in _items[-k:]][::-1]
    return [item["text"] for score, item in scored[:k] if score > 0]


def get_memory_context(query: str, k: Optional[int] = None) -> str:
    memories = search_memory(query, k)
    if not memories:
        return ""
    lines = "\n".join(memories)
    return (
        "Here are relevant things you remember about the user:\n"
        f"{lines}\n"
        "Use these memories to give personalized answers when relevant."
    )


def get_all_memories() -> list[str]:
    if not _is_initialized:
        init_memory()
    return [item.get("text", "") for item in _items if item.get("text")]


def extract_remember_payload(query: str) -> str:
    query = query.strip()
    normalized = query.lower().rstrip("!?.")
    if normalized in {"remember", "remember this", "remember that", "remember it"}:
        return ""

    prefixes = ["remember that ", "remember this ", "remember to ", "remember "]
    for prefix in prefixes:
        if normalized.startswith(prefix):
            query = query[len(prefix) :].strip()
            normalized = query.lower()
            break

    suffixes = [
        " remember it",
        " remember this",
        " remember that",
        " remember",
        " please remember",
        ", remember it",
        ", remember this",
    ]
    for suffix in suffixes:
        if normalized.endswith(suffix):
            query = query[: -len(suffix)].strip()
            break

    return query.strip().rstrip("!?.,")
