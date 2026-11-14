"""Milestone 6: music (Windows media session + Spotify PKCE), git/GitHub, calendar feed, vision."""
import json
import shutil
import subprocess
import time
from datetime import date, datetime
from urllib.parse import parse_qs, urlparse

import pytest

from conftest import FakeOllama
from babilious import db, memory
from babilious.integrations import commands as music_commands
from babilious.integrations import github, ics, music, vision
from babilious.tools import actions, router


@pytest.fixture(autouse=True)
def tools_ready(monkeypatch):
    from babilious.tools import load_all, places, wintools
    monkeypatch.setattr(places, "FORBIDDEN_PARTS", [])     # pytest's temp folders live under AppData
    monkeypatch.setattr(wintools, "press", lambda key, times=1: None)   # never press real media keys
    load_all()
    actions.init()


# ---------------------------------------------------------------- music: Windows media session
@pytest.fixture
def media(monkeypatch):
    """A fake Windows media session: Spotify playing a song."""
    state = {"session": True, "ok": True, "app": "SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify",
             "title": "Bohemian Rhapsody", "artist": "Queen", "album": "A Night at the Opera", "status": "Playing"}
    calls = []

    def run(self, action):
        calls.append(action)
        if action in ("pause", "toggle") and state["status"] == "Playing":
            state["status"] = "Paused"
        elif action in ("play", "toggle"):
            state["status"] = "Playing"
        elif action == "next":
            state["title"] = "Under Pressure"
        return dict(state)
    monkeypatch.setattr(music.SystemMedia, "_run", run)
    music.SYSTEM._cache = (0.0, None)
    state["calls"] = calls
    return state


def test_now_playing_from_windows(media):
    r = music_commands.handle("What's playing?")
    assert r.action == "music_now"
    assert r.text == "Playing: **Bohemian Rhapsody** by **Queen** (Spotify)."
    assert music_commands.handle("what song is this").action == "music_now"


def test_now_playing_when_silent():
    r = music_commands.handle("what's playing right now?")
    assert "Nothing's playing" in r.text


def test_media_control_tool_reports_what_happened(media):
    a = actions.request("media_control", {"action": "play_pause"}, "command")
    assert a["status"] == "completed" and a["summary"] == "Paused: Bohemian Rhapsody by Queen (Spotify)."
    a = actions.request("media_control", {"action": "next"}, "command")
    assert "Under Pressure" in a["summary"]


def test_media_control_falls_back_to_keys(monkeypatch):
    from babilious.tools import wintools
    keys = []
    monkeypatch.setattr(wintools, "press", lambda key, times=1: keys.append(key))
    a = actions.request("media_control", {"action": "next"}, "command")
    assert a["status"] == "completed" and keys == ["next"]


def test_play_without_spotify_api_opens_search(monkeypatch):
    opened = []
    monkeypatch.setattr(music, "open_search", lambda q: opened.append(q))
    r = music_commands.handle("play bohemian rhapsody on spotify")
    assert opened == ["bohemian rhapsody"] and r.action == "music_search"
    assert "Press play" in r.text and "Connect Spotify" in r.text


@pytest.mark.parametrize("text", ["play the music", "play", "pause the music", "play a game with me",
                                  "play the video on youtube", "play it again"])
def test_play_phrases_left_to_other_handlers(text):
    assert music_commands.handle(text) is None


def test_pause_for_voice_only_touches_what_it_paused(media):
    assert music.pause_for_voice() is True and media["status"] == "Paused"
    assert music.resume_after_voice() is True and media["status"] == "Playing"
    assert music.resume_after_voice() is False            # nothing left to resume
    media["status"] = "Paused"                            # you paused it yourself
    assert music.pause_for_voice() is False
    assert music.resume_after_voice() is False and media["status"] == "Paused"
    db.set_setting("music_pause_for_voice", False)
    media["status"] = "Playing"
    assert music.pause_for_voice() is False and media["status"] == "Playing"


def test_music_api(client, media):
    h = {"X-Babilious": "1"}
    s = client.get("/api/music").get_json()
    assert s["now_playing"]["title"] == "Bohemian Rhapsody" and s["spotify_connected"] is False
    assert client.post("/api/music/next", headers=h).get_json()["title"] == "Under Pressure"
    assert client.post("/api/music/explode", headers=h).status_code == 404
    assert client.post("/api/music-voice", json={"phase": "start"}, headers=h).get_json()["changed"] is True
    assert client.post("/api/music-voice", json={"phase": "end"}, headers=h).get_json()["changed"] is True
    assert client.post("/api/music-voice", json={"phase": "start"}).status_code == 403   # header guard


# ---------------------------------------------------------------- Spotify Web API (PKCE)
class _R:
    def __init__(self, status=200, js=None):
        self.status_code, self._js = status, js or {}
        self.content = json.dumps(self._js).encode() if js is not None else b""
        self.text = self.content.decode()

    def json(self):
        return self._js


CID = "0123456789abcdef0123456789abcdef"


def test_spotify_pkce_login_and_refresh(monkeypatch):
    db.set_setting("spotify_client_id", CID)
    url = music.SPOTIFY.login_url("127.0.0.1:5000")
    q = parse_qs(urlparse(url).query)
    assert q["client_id"] == [CID] and q["code_challenge_method"] == ["S256"]
    assert q["redirect_uri"] == ["http://127.0.0.1:5000/api/spotify/callback"]
    assert "client_secret" not in url
    posted = []

    def post(u, data=None, timeout=None):
        posted.append(data)
        return _R(js={"access_token": "A1", "refresh_token": "R1", "expires_in": 3600, "scope": "x"})
    monkeypatch.setattr(music.requests, "post", post)
    with pytest.raises(music.MusicError):
        music.SPOTIFY.finish_login("code", "wrong-state")       # forged callback
    music.SPOTIFY.finish_login("code", q["state"][0], "127.0.0.1:5000")
    assert posted[-1]["code_verifier"] and posted[-1]["client_id"] == CID and "client_secret" not in posted[-1]
    assert music.SPOTIFY.connected() and music.SPOTIFY._token() == "A1"
    with pytest.raises(music.MusicError):
        music.SPOTIFY.finish_login("code", q["state"][0])        # a state works only once
    # Expired → refreshed with the refresh token, still without a secret.
    tok = json.loads(music.TOKEN_FILE.read_text())
    tok["expires_at"] = time.time() - 10
    music.TOKEN_FILE.write_text(json.dumps(tok))
    monkeypatch.setattr(music.requests, "post", lambda u, data=None, timeout=None: (
        posted.append(data), _R(js={"access_token": "A2", "expires_in": 3600}))[1])
    assert music.SPOTIFY._token() == "A2" and posted[-1]["grant_type"] == "refresh_token"
    assert json.loads(music.TOKEN_FILE.read_text())["refresh_token"] == "R1"
    music.SPOTIFY.disconnect()
    assert not music.SPOTIFY.connected()


def _connect(monkeypatch, responses):
    db.set_setting("spotify_client_id", CID)
    music.TOKEN_FILE.write_text(json.dumps({"access_token": "A", "refresh_token": "R", "expires_at": time.time() + 999}))
    calls = []

    def request(method, url, headers=None, timeout=None, **kw):
        calls.append((method, url.replace(music.API, ""), kw))
        return responses(method, url.replace(music.API, ""), kw)
    monkeypatch.setattr(music.requests, "request", request)
    return calls


def test_spotify_play_search_and_now_playing(monkeypatch):
    def responses(method, path, kw):
        if path == "/search":
            return _R(js={"tracks": {"items": [{"uri": "spotify:track:1", "name": "Bohemian Rhapsody",
                                                "artists": [{"name": "Queen"}]}]}})
        if path == "/me/player/currently-playing":
            return _R(js={"is_playing": True, "item": {"name": "Bohemian Rhapsody", "artists": [{"name": "Queen"}],
                                                       "album": {"name": "Opera", "images": [{"url": "big"}, {"url": "mid"}]}}})
        return _R(204)
    calls = _connect(monkeypatch, responses)
    r = music_commands.handle("play bohemian rhapsody")
    assert r.text == "Playing **Bohemian Rhapsody** by Queen on Spotify."
    assert ("PUT", "/me/player/play", {"json": {"uris": ["spotify:track:1"]}}) in calls
    np = music.status()["now_playing"]
    assert np["source"] == "spotify" and np["art"] == "big" and np["playing"]


def test_spotify_free_account_falls_back_honestly(monkeypatch, media):
    def responses(method, path, kw):
        if path == "/search":
            return _R(js={"tracks": {"items": [{"uri": "spotify:track:1", "name": "Song", "artists": []}]}})
        return _R(403, {"error": {"reason": "PREMIUM_REQUIRED"}})
    _connect(monkeypatch, responses)
    opened = []
    monkeypatch.setattr(music, "open_search", lambda q: opened.append(q))
    r = music_commands.handle("play shape of you")
    assert "Premium" in r.text and opened == ["shape of you"] and "Press play" in r.text
    # pause/skip go through Windows instead
    res = music.control("pause")
    assert res["via"] == "windows" and media["status"] == "Paused"


def test_spotify_settings_and_login_routes(client):
    h = {"X-Babilious": "1"}
    assert client.get("/api/spotify/login").status_code == 400            # no client id yet
    assert client.patch("/api/settings", json={"spotify_client_id": "nope"}, headers=h).status_code == 400
    assert client.patch("/api/settings", json={"spotify_client_id": CID}, headers=h).status_code == 200
    r = client.get("/api/spotify/login")
    assert r.status_code == 302 and r.headers["Location"].startswith("https://accounts.spotify.com/authorize?")
    page = client.get("/api/spotify/callback?code=x&state=forged").data.decode()
    assert "come from this app" in page
    assert "Spotify said: access_denied" in client.get("/api/spotify/callback?error=access_denied").data.decode()
    i = client.get("/api/integrations").get_json()
    assert i["spotify"]["client_id"] == CID and i["spotify"]["connected"] is False
    assert "access_token" not in json.dumps(client.get("/api/settings").get_json())


# ---------------------------------------------------------------- git & GitHub
needs_git = pytest.mark.skipif(not shutil.which("git"), reason="git not installed")


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "demo"
    r.mkdir()
    run = lambda *a: subprocess.run(["git", *a], cwd=r, capture_output=True, check=True)  # noqa: E731
    run("init", "-q", "-b", "main")
    run("config", "user.email", "t@example.com")
    run("config", "user.name", "Test")
    (r / "a.txt").write_text("one\n")
    run("add", "-A")
    run("commit", "-q", "-m", "First commit")
    db.set_setting("projects", {"demo": str(r)})
    return r


@needs_git
def test_git_status_and_diff(repo):
    a = actions.request("git_status", {"project": "demo"}, "command")
    assert a["status"] == "completed" and "no uncommitted changes" in a["summary"] and "never pushed" in a["summary"]
    (repo / "a.txt").write_text("one\ntwo\n")
    (repo / "b.txt").write_text("new\n")
    a = actions.request("git_status", {"project": "demo"}, "command")
    assert "2 changed files (1 modified, 1 new (untracked))" in a["summary"]
    assert "First commit" in a["output"]
    d = actions.request("git_diff", {"project": "demo"}, "command")
    assert d["status"] == "completed" and d["followup"] and "+two" in d["output"] and "b.txt" in d["output"]


@needs_git
def test_git_commit_needs_approval_and_push_is_dangerous(repo):
    a = actions.request("git_commit", {"project": "demo", "message": "Nothing"}, "command")
    assert a["status"] == "blocked" and "nothing to commit" in a["summary"].lower()
    (repo / "a.txt").write_text("changed\n")
    a = actions.request("git_commit", {"project": "demo", "message": "Change a"}, "command")
    assert a["status"] == "pending" and a["risk"] == "confirm" and "Nothing is pushed" in a["reason"]
    done = actions.approve(a["id"])
    assert done["status"] == "completed" and "Not pushed" in done["summary"]
    log = subprocess.run(["git", "log", "-1", "--pretty=%s"], cwd=repo, capture_output=True, text=True).stdout
    assert log.strip() == "Change a"
    p = actions.request("git_push", {"project": "demo"}, "command")
    assert p["status"] == "pending" and p["risk"] == "danger"
    # A spoken "yes" can't approve a dangerous action (covered in assistant), and nothing ran here.
    assert actions.list_actions("pending")[0]["tool"] == "git_push"


@needs_git
def test_github_issues_without_remote(repo):
    a = actions.request("github_issues", {"project": "demo"}, "command")
    assert a["status"] == "failed" and "no GitHub remote" in a["summary"]


@needs_git
def test_remote_repo_parsing(repo):
    subprocess.run(["git", "remote", "add", "origin", "git@github.com:bakardon/babilious.git"], cwd=repo, check=True)
    assert github.remote_repo(repo) == "bakardon/babilious"


def test_git_on_unknown_project():
    a = actions.request("git_status", {"project": "no-such-project-xyz"}, "command")
    assert a["status"] == "failed" and "can't find a project" in a["summary"]


@pytest.mark.parametrize("text,tool,args", [
    ("git status of my demo project", "git_status", {"project": "demo"}),
    ("do I have uncommitted changes in demo?", "git_status", {"project": "demo"}),
    ("write a commit message for demo", "git_diff", {"project": "demo"}),
    ('commit demo with message "Fix bug"', "git_commit", {"project": "demo", "message": "Fix bug"}),
    ("push demo to github", "git_push", {"project": "demo"}),
    ("show open issues for demo", "github_issues", {"project": "demo"}),
    ("describe my screen", "describe_screen", {"question": ""}),
])
def test_routes(text, tool, args):
    r = router.route(text)
    assert r and r.tool == tool and r.args == args, r


# ---------------------------------------------------------------- calendar feed (iCal)
ICS = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
SUMMARY:Supervisor meeting
DTSTART:20261116T100000
DTEND:20261116T110000
LOCATION:Room 2\\, Building B
END:VEVENT
BEGIN:VEVENT
SUMMARY:Gym
DTSTART;TZID=Europe/London:20261102T070000
RRULE:FREQ=WEEKLY;BYDAY=MO,WE;COUNT=6
EXDATE;TZID=Europe/London:20261111T070000
END:VEVENT
BEGIN:VEVENT
SUMMARY:Holiday
DTSTART;VALUE=DATE:20261120
DTEND;VALUE=DATE:20261121
END:VEVENT
BEGIN:VEVENT
SUMMARY:Cancelled thing
STATUS:CANCELLED
DTSTART:20261116T120000
END:VEVENT
BEGIN:VEVENT
SUMMARY:A very long title that wraps
  onto the next line
DTSTART:20261117T090000
END:VEVENT
END:VCALENDAR
"""


def _london_7am_local() -> str:
    from zoneinfo import ZoneInfo
    return datetime(2026, 11, 2, 7, 0, tzinfo=ZoneInfo("Europe/London")).astimezone().strftime("%H:%M")


def test_ics_parse_and_expand():
    ev = ics.expand(ics.parse(ICS), date(2026, 11, 1), date(2026, 12, 1))
    titles = [(e["date"], e["time"], e["title"]) for e in ev]
    assert ("2026-11-16", "10:00", "Supervisor meeting") in titles
    assert ("2026-11-20", "", "Holiday") in titles
    assert not any(t == "Cancelled thing" for *_, t in titles)
    assert ("2026-11-17", "09:00", "A very long title that wraps onto the next line") in titles
    gym = [d for d, _, t in titles if t == "Gym"]
    gym_time = {tm for _, tm, t in titles if t == "Gym"}
    assert gym_time == {_london_7am_local()}   # converted from Europe/London to this PC's time zone
    # 6 occurrences Mon/Wed from 2 Nov, minus the excluded 11 Nov.
    assert gym == ["2026-11-02", "2026-11-04", "2026-11-09", "2026-11-16", "2026-11-18"]
    meeting = next(e for e in ev if e["title"] == "Supervisor meeting")
    assert meeting["end"] == "11:00" and meeting["location"] == "Room 2, Building B"


def test_ics_url_validation():
    assert ics.valid_url("webcal://calendar.google.com/x/basic.ics") == "https://calendar.google.com/x/basic.ics"
    assert ics.valid_url("") == ""
    with pytest.raises(ValueError):
        ics.valid_url("file:///C:/secret.ics")


def test_calendar_events_in_feed_and_briefing(client, monkeypatch):
    from babilious.manager import briefing, reminders
    h = {"X-Babilious": "1"}
    assert client.patch("/api/settings", json={"calendar_ics_url": "https://cal.example/basic.ics"}, headers=h).status_code == 200

    class R:
        text = ICS
        def raise_for_status(self):
            pass
    monkeypatch.setattr(ics.requests, "get", lambda *a, **k: R())
    ics._cache.update(url="", at=0.0, events=[], error="")
    feed = client.get("/api/calendar?from=2026-11-16&to=2026-11-17").get_json()
    assert {(i["kind"], i["title"]) for i in feed["items"] if i["kind"] == "event"} == {
        ("event", "Supervisor meeting"), ("event", "Gym")}
    a = briefing.agenda(datetime(2026, 11, 16, 8, 0))
    text = briefing.agenda_text(a)
    assert "Calendar today: " in text and f"Gym at {_london_7am_local()}" in text and "Supervisor meeting at 10:00" in text


def test_broken_calendar_never_breaks_the_briefing(monkeypatch):
    from babilious.manager import briefing
    db.set_setting("calendar_ics_url", "https://cal.example/basic.ics")
    ics._cache.update(url="", at=0.0, events=[], error="")

    def boom(*a, **k):
        raise ConnectionError("offline")
    monkeypatch.setattr(ics.requests, "get", boom)
    a = briefing.agenda(datetime(2026, 11, 16, 8, 0))
    assert a["events_today"] == [] and "offline" in ics.status()["error"]


# ---------------------------------------------------------------- vision
def test_vision_off_is_honest():
    a = actions.request("describe_screen", {}, "command")
    assert a["status"] == "failed" and "moondream" in a["summary"]


def test_vision_describes_a_screenshot(monkeypatch, tmp_path):
    from PIL import Image
    from babilious.tools import screen
    shot = tmp_path / "screen-20261114-120000-000000.png"
    Image.new("RGB", (1920, 1080), "navy").save(shot)
    monkeypatch.setattr(screen, "capture", lambda delay=0: shot)
    fake = FakeOllama(reply="A blue screen with nothing on it.")
    monkeypatch.setattr(vision, "OllamaClient", lambda: fake)
    db.set_setting("vision_model", "moondream:latest")
    a = actions.request("describe_screen", {"question": "what's this?"}, "command")
    assert a["status"] == "completed" and a["output"].startswith("A blue screen")
    msg = fake.last_messages[0]
    assert msg["content"] == "what's this?" and len(msg["images"]) == 1
    from babilious.assistant import _tool_reply
    text, _ = _tool_reply(a)
    assert text.startswith("A blue screen") and "moondream" in text


def test_vision_model_detection_and_model_switching():
    from babilious import models
    assert vision.is_vision("moondream:latest") and vision.is_vision("qwen2.5vl:3b") and vision.is_vision("llava:7b")
    assert not vision.is_vision("llama3.2:latest") and not vision.is_vision("gemma3:1b")
    fake = FakeOllama()
    fake.models.append({"name": "moondream:latest", "size": 1_700_000_000})
    over = models.overview(fake)
    assert next(m for m in over["installed"] if m["name"] == "moondream:latest")["vision"] is True
    # "the faster model" never picks a picture-describer as the chat model.
    fake.models = [m for m in fake.models if m["name"] != "llama3.2:1b"]
    assert models.resolve_request(fake, "switch to the faster model")[0] != "moondream:latest"


def test_integrations_overview_and_health(client):
    i = client.get("/api/integrations").get_json()
    assert set(i) == {"spotify", "calendar", "github", "vision"}
    names = {c["name"]: c for c in client.get("/api/health").get_json()["checks"]}
    assert names["Spotify"]["status"] == "pending" and names["Vision model"]["status"] == "pending"
