"""Music: one interface, two backends.

- SystemMedia: the Windows media session (works with the Spotify app, browsers and most players,
  no account needed). Knows what's playing and can play/pause/skip.
- SpotifyWeb: Spotify's official Web API through *your own* free developer app, connected with
  PKCE OAuth (no client secret is stored). Adds "play <song>", artwork and device info.
  Spotify only allows playback control for Premium accounts; now-playing works on Free.

Tokens are kept in data/spotify_token.json on this PC and never sent anywhere except Spotify.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import secrets
import subprocess
import threading
import time
from typing import Any, Protocol
from urllib.parse import urlencode

import requests

from .. import activity, config, db

log = logging.getLogger(__name__)

SCRIPT = os.path.join(os.path.dirname(__file__), "media.ps1")
TOKEN_FILE = config.DATA_DIR / "spotify_token.json"
SCOPES = "user-read-currently-playing user-read-playback-state user-modify-playback-state"
REDIRECT_PATH = "/api/spotify/callback"
API = "https://api.spotify.com/v1"


class MusicError(Exception):
    pass


class Provider(Protocol):
    name: str

    def now_playing(self) -> dict[str, Any] | None: ...
    def control(self, action: str) -> dict[str, Any]: ...


# ---------------------------------------------------------------- Windows media session
class SystemMedia:
    name = "windows"

    def __init__(self):
        self._cache: tuple[float, dict[str, Any] | None] = (0.0, None)
        self._lock = threading.Lock()

    def _run(self, action: str) -> dict[str, Any]:
        if os.name != "nt":  # pragma: no cover
            raise MusicError("Windows only")
        try:
            out = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", SCRIPT, action],
                                 capture_output=True, text=True, timeout=12,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired as exc:
            raise MusicError("Windows didn't answer about media in time.") from exc
        line = (out.stdout or "").strip().splitlines()[-1:] or [""]
        try:
            return json.loads(line[0])
        except ValueError as exc:
            raise MusicError((out.stderr or "Couldn't read the media session.").strip()[:200]) from exc

    def now_playing(self, max_age: float = 4.0) -> dict[str, Any] | None:
        with self._lock:
            at, data = self._cache
            if time.time() - at < max_age:
                return data
            d = self._run("status")
            data = None if not d.get("session") else {
                "title": d.get("title") or "", "artist": d.get("artist") or "", "album": d.get("album") or "",
                "app": _app_name(d.get("app") or ""), "playing": d.get("status") == "Playing", "source": "windows"}
            self._cache = (time.time(), data)
            return data

    def control(self, action: str) -> dict[str, Any]:
        if action not in ("play", "pause", "toggle", "next", "previous"):
            raise MusicError(f"Unknown action {action}")
        d = self._run(action)
        if not d.get("session"):
            raise MusicError("Nothing is playing in any media app right now.")
        self._cache = (0.0, None)
        return {"ok": bool(d.get("ok")), "title": d.get("title") or "", "artist": d.get("artist") or "",
                "app": _app_name(d.get("app") or ""), "playing": d.get("status") == "Playing"}


def _app_name(aumid: str) -> str:
    low = aumid.lower()
    for key, name in (("spotify", "Spotify"), ("chrome", "Chrome"), ("msedge", "Edge"), ("firefox", "Firefox"),
                      ("zune", "Media Player"), ("vlc", "VLC"), ("music", "Media Player")):
        if key in low:
            return name
    return aumid.split("!")[0][:40]


# ---------------------------------------------------------------- Spotify Web API (PKCE)
class SpotifyWeb:
    name = "spotify"

    def __init__(self):
        self._pending: dict[str, tuple[str, float]] = {}   # state -> (code_verifier, created)
        self._lock = threading.Lock()

    # ---- connection
    @staticmethod
    def client_id() -> str:
        return str(db.get_setting("spotify_client_id", "") or "").strip()

    @staticmethod
    def redirect_uri(host: str = "") -> str:
        # Spotify only accepts loopback redirects as an explicit IP, not "localhost".
        port = (host.rsplit(":", 1)[1] if ":" in host else "") or os.environ.get("BABILIOUS_PORT", "5000")
        return f"http://127.0.0.1:{port}{REDIRECT_PATH}"

    def connected(self) -> bool:
        return TOKEN_FILE.is_file() and bool(self.client_id())

    def login_url(self, host: str = "") -> str:
        cid = self.client_id()
        if not cid:
            raise MusicError("Add your Spotify app's Client ID in Settings > Integrations first.")
        verifier = secrets.token_urlsafe(64)[:96]
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        state = secrets.token_urlsafe(16)
        with self._lock:
            self._pending = {k: v for k, v in self._pending.items() if time.time() - v[1] < 600}
            self._pending[state] = (verifier, time.time())
        return "https://accounts.spotify.com/authorize?" + urlencode({
            "client_id": cid, "response_type": "code", "redirect_uri": self.redirect_uri(host),
            "code_challenge_method": "S256", "code_challenge": challenge, "state": state, "scope": SCOPES})

    def finish_login(self, code: str, state: str, host: str = "") -> None:
        with self._lock:
            verifier, _ = self._pending.pop(state, ("", 0))
        if not verifier:
            raise MusicError("That Spotify login link expired or didn't come from this app. Try Connect again.")
        r = requests.post("https://accounts.spotify.com/api/token", timeout=15, data={
            "grant_type": "authorization_code", "code": code, "redirect_uri": self.redirect_uri(host),
            "client_id": self.client_id(), "code_verifier": verifier})
        if r.status_code >= 400:
            raise MusicError(f"Spotify refused the login: {r.json().get('error_description', r.text[:120])}")
        self._save(r.json())
        activity.record("integration", "Connected Spotify")

    def disconnect(self) -> None:
        TOKEN_FILE.unlink(missing_ok=True)
        activity.record("integration", "Disconnected Spotify")

    def _save(self, tok: dict[str, Any]) -> None:
        old = self._load() or {}
        data = {"access_token": tok["access_token"], "refresh_token": tok.get("refresh_token") or old.get("refresh_token"),
                "expires_at": time.time() + int(tok.get("expires_in", 3600)) - 60, "scope": tok.get("scope", "")}
        TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        TOKEN_FILE.write_text(json.dumps(data), encoding="utf-8")

    @staticmethod
    def _load() -> dict[str, Any] | None:
        try:
            return json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _token(self) -> str:
        tok = self._load()
        if not tok:
            raise MusicError("Spotify isn't connected.")
        if time.time() < tok["expires_at"]:
            return tok["access_token"]
        r = requests.post("https://accounts.spotify.com/api/token", timeout=15, data={
            "grant_type": "refresh_token", "refresh_token": tok["refresh_token"], "client_id": self.client_id()})
        if r.status_code >= 400:
            self.disconnect()
            raise MusicError("Spotify's login expired. Connect again in Settings > Integrations.")
        self._save(r.json())
        return r.json()["access_token"]

    def _call(self, method: str, path: str, **kw) -> requests.Response:
        try:
            r = requests.request(method, API + path, headers={"Authorization": f"Bearer {self._token()}"}, timeout=10, **kw)
        except requests.ConnectionError as exc:
            raise MusicError("Can't reach Spotify (no internet?).") from exc
        if r.status_code in (401,):
            raise MusicError("Spotify rejected the login. Reconnect in Settings > Integrations.")
        if r.status_code == 403:
            raise MusicError("Spotify only lets Premium accounts be controlled through its API. "
                             "Pause/skip still work through Windows media controls.")
        if r.status_code == 404:
            raise MusicError("Spotify has no active device. Open Spotify and play something once, then ask again.")
        if r.status_code == 429:
            raise MusicError("Spotify says I'm asking too often. Give it a minute.")
        if r.status_code >= 400:
            raise MusicError(f"Spotify error {r.status_code}.")
        return r

    # ---- player
    def now_playing(self) -> dict[str, Any] | None:
        r = self._call("GET", "/me/player/currently-playing", params={"additional_types": "episode"})
        if r.status_code == 204 or not r.content:
            return None
        d = r.json()
        item = d.get("item") or {}
        if not item:
            return None
        artists = ", ".join(a["name"] for a in item.get("artists", [])) or (item.get("show") or {}).get("name", "")
        images = (item.get("album") or {}).get("images") or item.get("images") or []
        return {"title": item.get("name", ""), "artist": artists, "album": (item.get("album") or {}).get("name", ""),
                "art": images[-2]["url"] if len(images) > 1 else (images[0]["url"] if images else ""),
                "url": (item.get("external_urls") or {}).get("spotify", ""), "playing": bool(d.get("is_playing")),
                "progress_ms": d.get("progress_ms"), "duration_ms": item.get("duration_ms"),
                "app": "Spotify", "source": "spotify"}

    def control(self, action: str) -> dict[str, Any]:
        if action == "toggle":
            np = self.now_playing()
            action = "pause" if np and np["playing"] else "play"
        method, path = {"play": ("PUT", "/me/player/play"), "pause": ("PUT", "/me/player/pause"),
                        "next": ("POST", "/me/player/next"), "previous": ("POST", "/me/player/previous")}[action]
        self._call(method, path)
        time.sleep(0.4)
        np = self.now_playing() or {}
        return {"ok": True, **np}

    def search(self, query: str, kind: str = "track") -> dict[str, Any] | None:
        r = self._call("GET", "/search", params={"q": query, "type": kind, "limit": 1})
        items = (r.json().get(kind + "s") or {}).get("items") or []
        if not items:
            return None
        it = items[0]
        artists = ", ".join(a["name"] for a in it.get("artists", []))
        return {"uri": it["uri"], "name": it["name"], "artist": artists or (it.get("owner") or {}).get("display_name", "")}

    def play_search(self, query: str, kind: str = "track") -> dict[str, Any]:
        found = self.search(query, kind)
        if not found:
            raise MusicError(f"Spotify found nothing for \"{query}\".")
        body = {"uris": [found["uri"]]} if kind == "track" else {"context_uri": found["uri"]}
        self._call("PUT", "/me/player/play", json=body)
        return found


SYSTEM = SystemMedia()
SPOTIFY = SpotifyWeb()


# ---------------------------------------------------------------- the facade the rest of the app uses
def status() -> dict[str, Any]:
    out: dict[str, Any] = {"spotify_configured": bool(SPOTIFY.client_id()), "spotify_connected": SPOTIFY.connected(),
                           "now_playing": None, "error": ""}
    try:
        if SPOTIFY.connected():
            out["now_playing"] = SPOTIFY.now_playing()
        if not out["now_playing"]:
            out["now_playing"] = SYSTEM.now_playing()
    except MusicError as exc:
        out["error"] = str(exc)
        try:
            out["now_playing"] = SYSTEM.now_playing()
        except MusicError:
            pass
    return out


def now_playing() -> dict[str, Any] | None:
    return status()["now_playing"]


def control(action: str) -> dict[str, Any]:
    """Spotify API when connected (and allowed), otherwise the Windows media session, then media keys."""
    if SPOTIFY.connected():
        try:
            return {**SPOTIFY.control(action), "via": "spotify"}
        except MusicError as exc:
            log.info("Spotify control failed, using Windows media session: %s", exc)
    try:
        return {**SYSTEM.control(action), "via": "windows"}
    except MusicError:
        from ..tools.wintools import press
        press({"play": "play_pause", "pause": "play_pause", "toggle": "play_pause"}.get(action, action))
        return {"ok": True, "via": "keys"}


def open_search(query: str) -> None:
    """No API connection: open the Spotify app's own search (the user presses play)."""
    os.startfile("spotify:search:" + query)  # noqa: S606  (Windows protocol handler, opens the app)


# ---- pause around voice conversations
_paused_for_voice = False


def pause_for_voice() -> bool:
    """Pause music while you talk to Babilious, but only if something is actually playing."""
    global _paused_for_voice
    if not db.get_setting("music_pause_for_voice", True):
        return False
    try:
        np = SYSTEM.now_playing(max_age=0)
        if np and np["playing"]:
            SYSTEM.control("pause")
            _paused_for_voice = True
            return True
    except MusicError as exc:
        log.info("Couldn't pause for voice: %s", exc)
    return False


def resume_after_voice() -> bool:
    """Resume only what *we* paused, never something you paused yourself."""
    global _paused_for_voice
    if not _paused_for_voice:
        return False
    _paused_for_voice = False
    try:
        SYSTEM.control("play")
        return True
    except MusicError as exc:
        log.info("Couldn't resume after voice: %s", exc)
        return False


def describe(np: dict[str, Any] | None) -> str:
    if not np or not np.get("title"):
        return "Nothing's playing right now. Blessed silence."
    who = f" by **{np['artist']}**" if np.get("artist") else ""
    state = "Playing" if np.get("playing") else "Paused"
    app = f" ({np['app']})" if np.get("app") else ""
    return f"{state}: **{np['title']}**{who}{app}."


def valid_client_id(value: Any) -> str:
    v = str(value or "").strip()
    if v and not re.fullmatch(r"[0-9a-fA-F]{32}", v):
        raise ValueError("A Spotify Client ID is 32 letters/digits (from developer.spotify.com > your app > Settings).")
    return v
