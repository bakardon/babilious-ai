"""Basic Windows control: media keys (works with Spotify), volume, lock screen, settings pages."""
from __future__ import annotations

import ctypes
import os
from typing import Any

from . import Risk, Tool, ToolResult, confirm, register, safe

VK = {"play_pause": 0xB3, "next": 0xB0, "previous": 0xB1, "stop": 0xB2,
      "volume_up": 0xAF, "volume_down": 0xAE, "mute": 0xAD}
KEYEVENTF_KEYUP = 0x0002


def press(key: str, times: int = 1) -> None:
    if os.name != "nt":  # pragma: no cover
        raise OSError("Windows only")
    user32 = ctypes.windll.user32
    for _ in range(max(1, min(times, 50))):
        user32.keybd_event(VK[key], 0, 0, 0)
        user32.keybd_event(VK[key], 0, KEYEVENTF_KEYUP, 0)


def _media(args: dict[str, Any]) -> ToolResult:
    action = args["action"]
    if action not in ("play_pause", "next", "previous", "stop"):
        return ToolResult(False, f"Unknown media action {action}.")
    said = {"play_pause": "Toggled play/pause", "next": "Skipped to the next track",
            "previous": "Went back a track", "stop": "Stopped playback"}[action]
    if action == "stop":
        press(action)
        return ToolResult(True, f"{said}.")
    # The Windows media session (or Spotify's API when connected) can say what happened; keys are the fallback.
    from ..integrations import music
    res = music.control({"play_pause": "toggle"}.get(action, action))
    if res.get("via") == "keys" or not res.get("title"):
        return ToolResult(True, f"{said} (works with Spotify and most media players).", data={"via": res.get("via")})
    if action == "play_pause":
        said = "Playing" if res.get("playing") else "Paused"
    elif action == "next":
        said = "Skipped. Now"
    else:
        said = "Back a track. Now"
    by = f" by {res['artist']}" if res.get("artist") else ""
    app = f" ({res['app']})" if res.get("app") else ""
    return ToolResult(True, f"{said}: {res['title']}{by}{app}.", data={"via": res.get("via")})


register(Tool("media_control", "Play/pause, next, previous or stop whatever media is playing (Spotify, YouTube...).",
              {"action": {"type": "string", "enum": ["play_pause", "next", "previous", "stop"]}}, ["action"],
              _media, lambda a: {"play_pause": "Play/pause media", "next": "Next track", "previous": "Previous track",
                                 "stop": "Stop media"}.get(a.get("action"), "Media control"), safe))


def _volume(args: dict[str, Any]) -> ToolResult:
    action = args["action"]
    steps = int(args.get("steps") or 5)
    if action == "mute":
        press("mute")
        return ToolResult(True, "Toggled mute.")
    if action not in ("up", "down"):
        return ToolResult(False, "Volume action must be up, down or mute.")
    press("volume_up" if action == "up" else "volume_down", steps)
    return ToolResult(True, f"Turned the volume {action} ({steps * 2}%).")


register(Tool("volume", "Change the system volume.",
              {"action": {"type": "string", "enum": ["up", "down", "mute"]},
               "steps": {"type": "integer", "description": "Each step is 2%"}}, ["action"],
              _volume, lambda a: f"Volume {a.get('action')}", safe))


def _lock(_args: dict[str, Any]) -> ToolResult:
    ctypes.windll.user32.LockWorkStation()
    return ToolResult(True, "Locked the PC.")


register(Tool("lock_screen", "Lock the PC (asks first).", {}, [], _lock, lambda a: "Lock your PC",
              confirm("Locks your PC; anything unsaved stays open but you'll need to sign in again.")))

SETTINGS_PAGES = {"display": "ms-settings:display", "sound": "ms-settings:sound", "bluetooth": "ms-settings:bluetooth",
                  "wifi": "ms-settings:network-wifi", "network": "ms-settings:network", "battery": "ms-settings:batterysaver",
                  "apps": "ms-settings:appsfeatures", "updates": "ms-settings:windowsupdate", "privacy": "ms-settings:privacy",
                  "microphone": "ms-settings:privacy-microphone", "storage": "ms-settings:storagesense", "": "ms-settings:"}


def _settings_risk(args: dict[str, Any]) -> Risk:
    return Risk("safe") if args.get("page", "") in SETTINGS_PAGES else Risk("blocked", "Unknown settings page.")


def _settings(args: dict[str, Any]) -> ToolResult:
    os.startfile(SETTINGS_PAGES[args.get("page", "")])  # noqa: S606
    return ToolResult(True, f"Opened Windows Settings{' > ' + args['page'] if args.get('page') else ''}. "
                            "I only open the page; any change is yours to make.")


register(Tool("open_settings", "Open a Windows Settings page (only opens it; changes nothing).",
              {"page": {"type": "string", "enum": list(SETTINGS_PAGES)}}, [], _settings,
              lambda a: f"Open Settings{' > ' + a['page'] if a.get('page') else ''}", _settings_risk))
