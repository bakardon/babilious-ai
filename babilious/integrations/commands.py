"""Chat commands for music: "what's playing?", "play Bohemian Rhapsody on Spotify".

Pause/skip/volume stay in the tool router (media_control), which now goes through the same
music backends.
"""
from __future__ import annotations

import re
import webbrowser
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

from .. import activity
from . import music

NOW = re.compile(r"^(?:what'?s|what is)\s+(?:this\s+)?(?:song|track|playing|music)(?:\s+(?:playing|called|is this|on))?\s*(?:now|right now)?$|"
                 r"^(?:what|which)\s+song\s+(?:is\s+)?(?:this|playing)(?:\s+now)?$|^now playing$|"
                 r"^who(?:'s| is)?\s+(?:singing|sings)(?:\s+this)?(?:\s+song)?$|^what am i listening to$", re.I)
PLAY = re.compile(r"^(?:play|put on)\s+(?P<q>.+?)(?P<sp>\s+on\s+spotify)?$", re.I)
NOT_A_QUERY = re.compile(r"^(?:the\s+)?(?:music|song|track|spotify|playback|media|video|it|that|again|pause|something)"
                         r"(?:\s+again)?$|"
                         r"youtube|netflix|\bgame\b|\bvideo\b", re.I)
KIND = [(re.compile(r"^(?:the\s+)?album\s+", re.I), "album"), (re.compile(r"^(?:the\s+)?playlist\s+", re.I), "playlist"),
        (re.compile(r"^(?:some\s+)?(?:music|songs)\s+by\s+|^(?:the\s+)?artist\s+", re.I), "artist")]


@dataclass
class Reply:
    text: str
    mood: str = "happy"
    action: str = "music"
    data: dict[str, Any] = field(default_factory=dict)


def handle(text: str) -> Reply | None:
    t = re.sub(r"^(?:hey\s+)?(?:babilious[,\s]+)?(?:please\s+|can you\s+|could you\s+)*", "", text.strip(), flags=re.I)
    t = t.rstrip(" ?.!")
    if NOW.match(t):
        return _now()
    m = PLAY.match(t)
    if m and not NOT_A_QUERY.search(m.group("q")):
        return _play(m.group("q").strip(), bool(m.group("sp")))
    return None


def _now() -> Reply:
    try:
        st = music.status()
    except music.MusicError as exc:
        return Reply(f"I couldn't check: {exc}", "error", "music_now")
    np = st["now_playing"]
    return Reply(music.describe(np), "happy" if np else "curious", "music_now", {"now_playing": np})


def _play(query: str, said_spotify: bool) -> Reply:
    kind = "track"
    for pat, k in KIND:
        if pat.search(query):
            kind, query = k, pat.sub("", query)
            break
    query = re.sub(r"^(?:some|the song|the track|a song called)\s+", "", query, flags=re.I).strip()
    if not query:
        return Reply("Play *what*, exactly?", "confused", "music_play")
    if music.SPOTIFY.connected():
        try:
            found = music.SPOTIFY.play_search(query, kind)
            activity.record("music", f"Played {found['name']} on Spotify")
            by = f" by {found['artist']}" if found.get("artist") else ""
            return Reply(f"Playing **{found['name']}**{by} on Spotify.", "happy", "music_play", {"found": found})
        except music.MusicError as exc:
            note = f"Spotify wouldn't start it directly ({exc}) "
    else:
        note = ""
    try:
        music.open_search(query)
        where = "the Spotify app"
    except OSError:
        webbrowser.open("https://open.spotify.com/search/" + quote(query))
        where = "Spotify's web player"
    activity.record("music", f"Opened Spotify search for {query}")
    tail = ("" if music.SPOTIFY.connected() else
            " Connect Spotify in Settings > Integrations and I can start songs myself (Spotify allows that for Premium only).")
    return Reply(f"{note}I opened {where} with a search for **{query}**. Press play.{tail}".strip(),
                 "curious", "music_search", {"query": query})
