"""Chat replies for JARVIS.

Uses NVIDIA's OpenAI-compatible API when NVIDIA_API_KEY is set in .env.
No local model is downloaded unless JARVIS_LLM_MODE=local.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from dataclasses import dataclass

import config  # noqa: F401  loads .env

_LLM_MODE = os.getenv("JARVIS_LLM_MODE", "nim").lower()
_NIM_MODEL_NAME = os.getenv("JARVIS_NIM_MODEL", "mistralai/mistral-7b-instruct-v0.3")
_NIM_URL = "https://integrate.api.nvidia.com/v1/chat/completions"

_SYSTEM_PROMPT = (
    "You are JARVIS, a concise and practical AI assistant for desktop automation. "
    "Prefer short, clear answers in plain text. Avoid markdown decoration and asterisks."
)


@dataclass
class _RateConfig:
    requests_per_minute: int = int(os.getenv("JARVIS_NIM_REQUESTS_PER_MIN", "60"))
    max_response_tokens: int = int(os.getenv("JARVIS_NIM_MAX_OUTPUT_TOKENS", "400"))


class _RateLimiter:
    def __init__(self, config_: _RateConfig) -> None:
        self.config = config_
        self._request_times: deque[float] = deque()

    def allow(self) -> bool:
        import time

        now = time.time()
        cutoff = now - 60.0
        while self._request_times and self._request_times[0] < cutoff:
            self._request_times.popleft()
        if len(self._request_times) >= self.config.requests_per_minute:
            return False
        self._request_times.append(now)
        return True


_rate_limiter = _RateLimiter(_RateConfig())
_nim_blocked = False


def _api_key() -> str:
    return os.getenv("NVIDIA_API_KEY", "").strip() or os.getenv("JARVIS_NVIDIA_API_KEY", "").strip()


def _shorten(text: str, limit: int = 420) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    if "." in cut:
        return cut[: cut.rfind(".") + 1]
    return cut


def _math_reply(user_query: str) -> str:
    text = user_query.lower()
    for phrase in ("what is", "what's", "whats", "calculate", "compute"):
        text = text.replace(phrase, "")
    text = (
        text.replace("multiplied by", "*")
        .replace("divided by", "/")
        .replace("plus", "+")
        .replace("minus", "-")
        .replace("times", "*")
        .replace("x", "*")
    )
    text = text.strip(" ?.")
    if not text or not re.fullmatch(r"[0-9.\s+\-*/()]+", text) or not re.search(r"\d", text):
        return ""
    try:
        value = eval(text, {"__builtins__": {}}, {})  # noqa: S307 - digits and arithmetic only
    except Exception:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return f"The answer is {value}."


def _chat_reply(user_query: str) -> str:
    """Answer greetings locally so they are not looked up as song or page titles."""
    text = re.sub(r"[^a-z\s']", " ", user_query.lower())
    text = re.sub(r"\s+", " ", text).strip()
    if not text or len(text.split()) > 8:
        return ""

    replies = (
        ("how are you", "Systems are online and ready. How can I help?"),
        ("how's it going", "Systems are online and ready. How can I help?"),
        ("hows it going", "Systems are online and ready. How can I help?"),
        ("how are things", "Systems are online and ready. How can I help?"),
        ("how do you feel", "All systems are functioning."),
        ("who are you", "I am JARVIS, your desktop assistant."),
        ("what is your name", "I am JARVIS, your desktop assistant."),
        ("what's your name", "I am JARVIS, your desktop assistant."),
        ("whats your name", "I am JARVIS, your desktop assistant."),
        ("what can you do", "I can tell the time, check the weather, open programs, play music, and answer questions."),
        ("thank you", "You are welcome."),
        ("thanks", "You are welcome."),
        ("good morning", "Good morning."),
        ("good afternoon", "Good afternoon."),
        ("good evening", "Good evening."),
        ("good night", "Good night."),
        ("hello", "Hello. I am online and ready."),
        ("hi", "Hello. I am online and ready."),
        ("hey", "Hello. I am online and ready."),
    )
    for phrase, reply in replies:
        if text == phrase or text.startswith(phrase + " ") or text.endswith(" " + phrase):
            return reply
    return ""


def _should_lookup(user_query: str) -> bool:
    text = user_query.lower().strip()
    if text.startswith("how are"):
        return False
    starters = (
        "what ",
        "who ",
        "where ",
        "when ",
        "why ",
        "which ",
        "how ",
        "define ",
        "tell me about ",
    )
    return text.startswith(starters)


def _web_reply(user_query: str) -> str:
    if not _should_lookup(user_query):
        return ""
    headers = {"User-Agent": "JARVIS/1.0 (desktop assistant)"}
    quoted = urllib.parse.quote(user_query)
    try:
        request = urllib.request.Request(
            f"https://api.duckduckgo.com/?q={quoted}&format=json&no_html=1&skip_disambig=1",
            headers=headers,
        )
        with urllib.request.urlopen(request, timeout=12) as response:
            payload = json.loads(response.read().decode("utf-8"))
        abstract = payload.get("AbstractText") or payload.get("Abstract") or ""
        if abstract:
            return _shorten(abstract)
    except Exception as exc:
        print(f"[Answers] {exc}")

    try:
        request = urllib.request.Request(
            "https://en.wikipedia.org/w/api.php?action=opensearch&limit=1&namespace=0&format=json&search="
            + quoted,
            headers=headers,
        )
        with urllib.request.urlopen(request, timeout=12) as response:
            payload = json.loads(response.read().decode("utf-8"))
        titles = payload[1] if len(payload) > 1 else []
        if not titles:
            return ""
        summary_url = "https://en.wikipedia.org/api/rest_v1/page/summary/" + urllib.parse.quote(
            titles[0].replace(" ", "_")
        )
        request = urllib.request.Request(summary_url, headers=headers)
        with urllib.request.urlopen(request, timeout=12) as response:
            summary = json.loads(response.read().decode("utf-8"))
        extract = summary.get("extract") or ""
        return _shorten(extract)
    except Exception as exc:
        print(f"[Answers] {exc}")
        return ""


def _offline_reply(user_query: str) -> str:
    text = user_query.lower().strip()
    if any(phrase in text for phrase in ("your name", "who are you")):
        return "I am JARVIS, your desktop assistant."
    if "how are you" in text:
        return "Systems are online and ready."
    if not _api_key():
        return (
            "I can run your computer commands, but open chat needs an NVIDIA API key. "
            "Add NVIDIA_API_KEY to the .env file in this folder."
        )
    return "I could not reach the language model. Check your network and try again."


def _nim_reply(user_query: str, system_prompt: str) -> str:
    global _nim_blocked
    if _nim_blocked:
        return ""
    if not _rate_limiter.allow():
        return "I am getting too many questions at once. Give me a moment and ask again."

    body = {
        "model": _NIM_MODEL_NAME,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_query},
        ],
        "temperature": 0.6,
        "top_p": 0.95,
        "max_tokens": _rate_limiter.config.max_response_tokens,
    }
    request = urllib.request.Request(
        _NIM_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {_api_key()}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
        content = payload["choices"][0]["message"].get("content") or ""
        text = content.replace("*", "").strip()
        return text
    except urllib.error.HTTPError as exc:
        if exc.code in {401, 403, 410}:
            _nim_blocked = True
            print("[LLM] NVIDIA chat is unavailable. Answering locally.")
        else:
            print(f"[LLM] NVIDIA API returned HTTP {exc.code}")
        return ""
    except Exception as exc:
        print(f"[LLM] {exc}")
        return ""


def get_nim_response(user_query: str, memory_context: str = "") -> str:
    system_prompt = _SYSTEM_PROMPT
    if memory_context:
        system_prompt = f"{_SYSTEM_PROMPT}\n\n{memory_context}"

    chat = _chat_reply(user_query)
    if chat:
        return chat

    if _LLM_MODE == "local":
        try:
            return _local_reply(user_query, system_prompt)
        except Exception as exc:
            print(f"[LLM] Local model failed: {exc}")

    if _api_key():
        cloud = _nim_reply(user_query, system_prompt)
        if cloud:
            return cloud

    calculated = _math_reply(user_query)
    if calculated:
        return calculated
    looked_up = _web_reply(user_query)
    if looked_up:
        return looked_up
    return _offline_reply(user_query)


def _local_reply(user_query: str, system_prompt: str) -> str:
    from transformers import AutoModelForCausalLM, AutoTokenizer

    model_name = os.getenv("JARVIS_LOCAL_MODEL", "Qwen/Qwen2.5-0.5B-Instruct")
    tokenizer = AutoTokenizer.from_pretrained(model_name)
    model = AutoModelForCausalLM.from_pretrained(model_name)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_query},
    ]
    text = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    model_inputs = tokenizer([text], return_tensors="pt").to(model.device)
    generated = model.generate(**model_inputs, max_new_tokens=200, do_sample=True, temperature=0.7, top_p=0.9)
    trimmed = [
        output[len(incoming) :]
        for incoming, output in zip(model_inputs.input_ids, generated)
    ]
    return tokenizer.batch_decode(trimmed, skip_special_tokens=True)[0].strip()
