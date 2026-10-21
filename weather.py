"""Weather lookup. Set OPENWEATHER_API_KEY in the .env file."""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request

import config  # noqa: F401
from input_engine import listen
from speech_engine import speak


def weather(city_name: str | None = None) -> None:
    api_key = os.getenv("OPENWEATHER_API_KEY", "").strip()
    if not api_key:
        speak("Weather needs an OpenWeather API key in the .env file.")
        return

    if not city_name or city_name == "none":
        speak("Which city?")
        city_name = listen()
    if not city_name or city_name == "none":
        speak("I did not catch the city.")
        return

    query = urllib.parse.urlencode({"q": city_name, "appid": api_key, "units": "metric"})
    url = "https://api.openweathermap.org/data/2.5/weather?" + query
    try:
        with urllib.request.urlopen(url, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        print(f"[Weather] {exc}")
        speak("I could not reach the weather service.")
        return

    if str(payload.get("cod")) == "404":
        speak("I could not find that city.")
        return

    main = payload.get("main", {})
    description = payload.get("weather", [{}])[0].get("description", "unknown conditions")
    place = payload.get("name") or city_name
    report = (
        f"In {place} it is {description}. "
        f"The temperature is {round(main.get('temp', 0))} degrees celsius, "
        f"humidity is {main.get('humidity', 'unknown')} percent."
    )
    print(report)
    speak(report)
