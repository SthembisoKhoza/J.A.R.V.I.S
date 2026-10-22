"""Play, pause, next, and the current track from the Windows media session.

The session reports whether the command was accepted and what is actually
playing. Spotify's window title is only a fallback when that session is empty.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path

_SCRIPT = r"""
param([Parameter(Position=0)][string]$Action = "status")
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false
$OutputEncoding = [Console]::OutputEncoding

function Emit($ok, $status, $title, $artist, $app, $error, $position, $duration, $art) {
  $payload = [ordered]@{
    ok = [bool]$ok
    status = [string]$status
    title = [string]$title
    artist = [string]$artist
    app = [string]$app
    error = [string]$error
    position = [double]$position
    duration = [double]$duration
    art = [string]$art
  }
  Write-Output ($payload | ConvertTo-Json -Compress)
}

try {
  Add-Type -AssemblyName System.Runtime.WindowsRuntime
  $method = @([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq "AsTask" -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq "IAsyncOperation``1"
  })[0]
  function Await($task, $type) {
    $net = $method.MakeGenericMethod($type).Invoke($null, @($task))
    if (-not $net.Wait(5000)) { throw "The media session timed out." }
    $net.Result
  }
  $null = [Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager, Windows.Media.Control, ContentType = WindowsRuntime]
  $manager = Await ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager]::RequestAsync()) ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager])
  $spotify = $null
  $playingSpotify = $null
  $playingOther = $null
  foreach ($item in @($manager.GetSessions())) {
    $isSpotify = [string]$item.SourceAppUserModelId -like "*Spotify*"
    $state = $item.GetPlaybackInfo().PlaybackStatus.ToString().ToLower()
    if ($isSpotify) { $spotify = $item }
    if ($state -eq "playing" -and $isSpotify) { $playingSpotify = $item }
    elseif ($state -eq "playing" -and -not $playingOther) { $playingOther = $item }
  }
  if ($playingSpotify) { $session = $playingSpotify }
  elseif ($playingOther) { $session = $playingOther }
  elseif ($spotify) { $session = $spotify }
  else { $session = $manager.GetCurrentSession() }
  if (-not $session) {
    Emit $false "none" "" "" "" "" 0 0 ""
    exit 0
  }
  $accepted = $true
  if ($Action -eq "play") {
    $accepted = [bool](Await ($session.TryPlayAsync()) ([bool]))
  } elseif ($Action -eq "pause") {
    $accepted = [bool](Await ($session.TryPauseAsync()) ([bool]))
  } elseif ($Action -eq "next") {
    $accepted = [bool](Await ($session.TrySkipNextAsync()) ([bool]))
  } elseif ($Action -eq "previous") {
    $accepted = [bool](Await ($session.TrySkipPreviousAsync()) ([bool]))
  }
  if ($Action -ne "status") { Start-Sleep -Milliseconds 400 }
  $info = Await ($session.TryGetMediaPropertiesAsync()) ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionMediaProperties])
  $playback = $session.GetPlaybackInfo()
  $position = 0.0
  $duration = 0.0
  try {
    $timeline = $session.GetTimelineProperties()
    $duration = [double]$timeline.EndTime.Subtract($timeline.StartTime).TotalSeconds
    $position = [double]$timeline.Position.Subtract($timeline.StartTime).TotalSeconds
    if ($position -lt 0) { $position = 0 }
    if ($duration -lt 0) { $duration = 0 }
  } catch {}
  $art = ""
  try {
    $thumb = $info.Thumbnail
    if ($null -ne $thumb) {
      $stream = Await ($thumb.OpenReadAsync()) ([Windows.Storage.Streams.IRandomAccessStreamWithContentType])
      $size64 = [int64]$stream.Size
      if ($size64 -gt 32 -and $size64 -lt 6000000) {
        $size = [uint32]$size64
        $reader = [Windows.Storage.Streams.DataReader, Windows.Storage.Streams, ContentType=WindowsRuntime]::new($stream)
        $loaded = [uint32](Await ($reader.LoadAsync($size)) ([uint32]))
        $bytes = New-Object byte[] $loaded
        $reader.ReadBytes($bytes)
        $folder = Join-Path $env:TEMP "JARVIS"
        New-Item -ItemType Directory -Force -Path $folder | Out-Null
        $artFile = Join-Path $folder "album-art"
        $tempFile = "$artFile.tmp"
        [System.IO.File]::WriteAllBytes($tempFile, $bytes)
        Move-Item -Force $tempFile $artFile
        $art = $artFile
        $reader.Dispose()
        $stream.Dispose()
      }
    }
  } catch {}
  Emit $accepted $playback.PlaybackStatus.ToString().ToLower() $info.Title $info.Artist $session.SourceAppUserModelId "" $position $duration $art
} catch {
  Emit $false "error" "" "" "" $_.Exception.Message 0 0 ""
}
"""

_SKIP_WORDS = {"the", "a", "an", "by", "and", "feat", "ft", "featuring", "on", "song"}
_SOURCE_NAMES = (
    ("spotify", "Spotify"),
    ("msedge", "Edge"),
    ("chrome", "Chrome"),
    ("firefox", "Firefox"),
    ("music.ui", "Media Player"),
)


def _script_path() -> Path:
    folder = Path(os.environ.get("TEMP", ".")) / "JARVIS"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "media_session.ps1"
    if not path.exists() or path.read_text(encoding="utf-8") != _SCRIPT:
        path.write_text(_SCRIPT, encoding="utf-8")
    return path


def _blank(status: str = "error", error: str = "") -> dict:
    return {
        "ok": False,
        "status": status,
        "title": "",
        "artist": "",
        "source": "",
        "error": error,
        "position": 0.0,
        "duration": 0.0,
        "art": "",
    }


def _friendly_source(app_id: str) -> str:
    lowered = (app_id or "").lower()
    for key, name in _SOURCE_NAMES:
        if key in lowered:
            return name
    if "!" in (app_id or ""):
        tail = app_id.split("!")[-1].replace(".exe", "").strip()
        if tail and tail.lower() not in {"app"}:
            return tail
    return ""


def _query(action: str) -> dict:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        completed = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(_script_path()),
                action,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            creationflags=flags,
            check=False,
        )
    except Exception as exc:
        return _blank(error=str(exc))
    raw = ""
    for line in reversed(completed.stdout.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            raw = line
            break
    if not raw:
        detail = (completed.stderr or completed.stdout or "no media session").strip()
        return _blank(error=detail[:240])
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return _blank(error=raw[:240])
    return {
        "ok": bool(payload.get("ok")),
        "status": str(payload.get("status") or "none").lower(),
        "title": str(payload.get("title") or "").strip(),
        "artist": str(payload.get("artist") or "").strip(),
        "source": _friendly_source(str(payload.get("app") or "")),
        "error": str(payload.get("error") or "").strip(),
        "position": _seconds(payload.get("position")),
        "duration": _seconds(payload.get("duration")),
        "art": str(payload.get("art") or "").strip(),
    }


def _seconds(value) -> float:
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    if number < 0 or number > 24 * 3600:
        return 0.0
    return number


def track_label(status: dict) -> str:
    title = (status.get("title") or "").strip()
    artist = (status.get("artist") or "").strip()
    if artist and title:
        return f"{artist} - {title}"
    return title or artist


def media_status() -> dict:
    """What the system media session is doing right now."""
    status = _query("status")
    if status["title"] or status["artist"] or status["status"] in {"playing", "paused"}:
        return status
    window = ""
    try:
        from pcautomati0n import spotify_track

        window = spotify_track()
    except Exception:
        window = ""
    if not window:
        return status
    artist = ""
    title = window
    if " - " in window:
        artist, title = window.split(" - ", 1)
    return {
        "ok": True,
        "status": "unknown",
        "title": title.strip(),
        "artist": artist.strip(),
        "source": "Spotify",
        "error": "",
        "position": 0.0,
        "duration": 0.0,
        "art": "",
    }


def present(status: dict) -> dict:
    """Fields the player bar draws: title, artist, state, progress, and cover."""
    state = str(status.get("state") or status.get("status") or "")
    title = str(status.get("title") or "")
    artist = str(status.get("artist") or "")
    if state not in {"playing", "paused"}:
        state = "unknown" if (title or artist) else ""
    return {
        "title": title,
        "artist": artist,
        "state": state,
        "source": status.get("source") or "",
        "position": _seconds(status.get("position")),
        "duration": _seconds(status.get("duration")),
        "art": status.get("art") or "",
    }


def track_view() -> dict:
    try:
        return present(media_status())
    except Exception:
        return present(_blank())


def transport(action: str) -> dict:
    """Button control. play, pause, toggle, next, or previous. Does not speak."""
    if action == "toggle":
        current = _query("status")
        action = "pause" if current.get("status") == "playing" else "play"
    if action not in {"play", "pause", "next", "previous"}:
        return present(media_status())
    result = _query(action)
    if action in {"next", "previous"}:
        time.sleep(0.6)
        result = media_status()
    elif result.get("status") not in {"playing", "paused"}:
        result = media_status()
    return present(result)


def _with_source(sentence: str, status: dict) -> str:
    source = status.get("source") or ""
    text = sentence.rstrip(".")
    if source and source.lower() not in text.lower():
        text = f"{text} on {source}"
    return text + "."


def _until(status: dict, wanted: str, attempts: int = 2) -> dict:
    latest = status
    for _ in range(attempts):
        if latest.get("status") == wanted:
            return latest
        time.sleep(0.45)
        latest = media_status()
    return latest


def media_play() -> str:
    current = media_status()
    label = track_label(current)
    if current["status"] == "playing" and label:
        return _with_source(f"Already playing {label}", current)
    if current["status"] == "error" and not label:
        return "I could not reach the media controls."
    result = _until(_query("play"), "playing")
    label = track_label(result) or label
    if result["status"] == "playing":
        if label:
            return _with_source(f"Playing {label}", result)
        return "Playing."
    if result.get("ok") is False:
        return "I could not start playback."
    return "I sent play, but playback did not start."


def media_pause() -> str:
    current = media_status()
    label = track_label(current)
    if current["status"] == "paused":
        if label:
            return f"Already paused on {label}."
        return "Already paused."
    if current["status"] in {"none", "stopped", "closed"} or (current["status"] == "error" and not label):
        return "Nothing is playing."
    result = _until(_query("pause"), "paused")
    label = track_label(result) or label
    if result["status"] == "paused":
        if label:
            return f"Paused on {label}."
        return "Paused."
    return "I could not pause playback."


def media_next() -> str:
    before = media_status()
    previous = track_label(before)
    if before["status"] in {"none", "error"} and not previous:
        return "Nothing is playing."
    result = _query("next")
    latest = result
    for _ in range(3):
        if track_label(latest) and track_label(latest) != previous:
            break
        time.sleep(0.45)
        latest = media_status()
    new = track_label(latest)
    if new and new != previous:
        if latest["status"] == "playing":
            return _with_source(f"Next track is {new}", latest)
        return f"The track changed to {new}, but it is not playing."
    if result.get("ok") is False:
        return "I could not skip to the next track."
    return "I could not confirm the next track."


def media_previous() -> str:
    before = media_status()
    previous = track_label(before)
    if before["status"] in {"none", "error"} and not previous:
        return "Nothing is playing."
    result = _query("previous")
    latest = result
    for _ in range(3):
        if track_label(latest) and track_label(latest) != previous:
            break
        time.sleep(0.45)
        latest = media_status()
    new = track_label(latest)
    if new and new != previous:
        if latest["status"] == "playing":
            return _with_source(f"Previous track is {new}", latest)
        return f"The track changed to {new}, but it is not playing."
    if result.get("ok") is False:
        return "I could not go back to the previous track."
    return "I could not confirm the previous track."


def current_track_sentence() -> str:
    status = media_status()
    label = track_label(status)
    if status["status"] == "playing" and label:
        return _with_source(f"Playing {label}", status)
    if status["status"] == "paused" and label:
        return f"Paused on {label}."
    if label:
        return f"The current track is {label}."
    if status["status"] == "error":
        return "I could not read the current track."
    return "Nothing is playing."


def _tokens(text: str) -> set[str]:
    return {word for word in re.findall(r"[a-z0-9]+", text.lower()) if word not in _SKIP_WORDS and len(word) >= 3}


def _same_word(left: str, right: str) -> bool:
    if left == right:
        return True
    if len(left) >= 5 and len(right) >= 5 and left[:4] == right[:4]:
        return True
    return False


def matches_request(request: str, status: dict) -> bool:
    """True when the requested song and the session share a real word."""
    wanted = _tokens(request)
    heard = _tokens(track_label(status))
    return any(_same_word(word, other) for word in wanted for other in heard)
