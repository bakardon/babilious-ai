"""Milestone 4: tools, permissions, routing and the approval flow.

Side effects that would touch the real desktop (launching apps, the Recycle
Bin, the clipboard, media keys) are replaced with recorders. File operations
run for real inside pytest's temp folders.
"""
import json
import sys
import types
from pathlib import Path

import pytest

from babilious import db, memory
from babilious.tools import actions, places, router
from babilious.tools.terminal import classify
from conftest import H


@pytest.fixture(autouse=True)
def sandbox(monkeypatch, tmp_path):
    monkeypatch.setattr(places, "FORBIDDEN_PARTS", [])          # temp dirs live under AppData
    actions.init()
    launched, trashed, clip, keys = [], [], {"text": "def add(a, b):\n    return a - b  # bug?"}, []
    from babilious.tools import apps, wintools
    monkeypatch.setattr(apps, "launch", lambda target, args=None: launched.append((target, args or [])))
    monkeypatch.setattr(apps, "available", lambda target: target != "firefox.exe")
    monkeypatch.setitem(sys.modules, "send2trash", types.SimpleNamespace(
        send2trash=lambda p: (trashed.append(p), Path(p).unlink() if Path(p).is_file() else None)))
    monkeypatch.setitem(sys.modules, "pyperclip", types.SimpleNamespace(
        paste=lambda: clip["text"], copy=lambda t: clip.__setitem__("text", t)))
    monkeypatch.setattr(wintools, "press", lambda key, times=1: keys.append((key, times)))
    import babilious.tools.files as files_mod
    monkeypatch.setattr(files_mod, "_startfile", lambda target: launched.append(("startfile", [target])))
    import webbrowser
    monkeypatch.setattr(webbrowser, "open", lambda url: launched.append(("browser", [url])))
    db.set_setting("default_folder", str(tmp_path))
    return {"launched": launched, "trashed": trashed, "clip": clip, "keys": keys, "tmp": tmp_path}


# ---------------------------------------------------------------- command classification

@pytest.mark.parametrize("cmd, level", [
    ("git status", "safe"), ("git log --oneline -5", "safe"), ("dir", "safe"), ("python --version", "safe"),
    ("pip list", "safe"), ("echo hello", "safe"), ("ollama list", "safe"),
    ("npm test", "confirm"), ("python script.py", "confirm"), ("git status; echo hi", "confirm"),
    ("git push origin main", "danger"), ("git reset --hard HEAD~1", "danger"), ("Remove-Item notes.txt", "danger"),
    ("pip install requests", "danger"), ("echo hi > out.txt", "danger"), ("shutdown /s /t 0", "danger"),
    ("Invoke-WebRequest https://x.org -OutFile a.zip", "danger"), ("git status | Out-File s.txt", "danger"),
    ("format c:", "blocked"), ("diskpart", "blocked"), ("rd /s /q C:\\", "blocked"), ("bcdedit /set x", "blocked"),
])
def test_command_classification(cmd, level):
    assert classify(cmd).level == level


# ---------------------------------------------------------------- routing

@pytest.mark.parametrize("text, tool, args", [
    ("Open VS Code.", "open_app", {"app": "vs code"}),
    ("Babilious, open PowerShell", "open_app", {"app": "powershell"}),
    ("Open Chrome and navigate to GitHub", "open_url", {"url": "github", "browser": "chrome"}),
    ("Open my Babilious project", "open_path", {"path": "babilious", "app": ""}),
    ("open my babilious project in vs code", "open_path", {"path": "babilious", "app": "vscode"}),
    ("Go to github", "open_url", {"url": "github"}),
    ("Create a folder called Research", "create_folder", {"name": "Research", "location": ""}),
    ("create a new folder named Thesis on my desktop", "create_folder", {"name": "Thesis", "location": "desktop"}),
    ("Create a file called todo.txt with buy milk", "create_file", {"name": "todo.txt", "location": "", "content": "buy milk"}),
    ("What's in my Downloads folder?", "list_folder", {"path": "Downloads"}),
    ("Run the tests for my project", "run_tests", {"project": "babilious"}),
    ("run the tests for ParkSight", "run_tests", {"project": "parksight"}),
    ("Run `git log -3`", "run_command", {"command": "git log -3", "cwd": ""}),
    ('run `python -c "print(6*7)"`', "run_command", {"command": 'python -c "print(6*7)"', "cwd": ""}),
    ("run 'git diff --stat' in my babilious project", "run_command", {"command": "git diff --stat", "cwd": "babilious"}),
    ("git status", "git_status", {"project": "babilious"}),
    ("Take a screenshot", "take_screenshot", {"delay": 3}),
    ("What's on my screen?", "read_screen", {"delay": 3}),
    ("Explain this error", "read_screen", {"delay": 3}),
    ("what's in my clipboard", "read_clipboard", {}),
    ("Pause the music", "media_control", {"action": "play_pause"}),
    ("skip this song", "media_control", {"action": "next"}),
    ("turn the volume down a lot", "volume", {"action": "down", "steps": 10}),
    ("Lock my PC", "lock_screen", {}),
    ("Search for python files containing parse_signal", None, None),   # handled by the second brain
    ("search C:\\code for python files containing parse_signal", "search_folder",
     {"folder": "c:\\code", "pattern": "*.py", "contains": "parse_signal"}),
    ("delete the file C:\\temp\\old.txt", "delete_path", {"path": "C:\\temp\\old.txt"}),
    ("read https://example.com", "read_webpage", {"url": "https://example.com"}),
])
def test_router(text, tool, args):
    r = router.route(text)
    if tool is None:
        assert r is None or r.tool != "search_folder"
        return
    assert r and r.tool == tool, (text, r)
    for k, v in args.items():
        assert str(r.args.get(k, "")).lower() == str(v).lower(), (text, k, r.args)


@pytest.mark.parametrize("text", ["How do I open a file in Python?", "What are we doing today?", "Explain recursion",
                                  "delete the task about groceries", "I love opening presents",
                                  "can you explain what a decorator is"])
def test_router_ignores_normal_chat(text):
    assert router.route(text) is None


def test_voice_screenshot_has_no_delay():
    assert router.route("what's on my screen", via="voice").args["delay"] == 0


# ---------------------------------------------------------------- permission flow

def test_safe_tool_runs_immediately(sandbox):
    a = actions.request("open_app", {"app": "vs code"}, "command")
    assert a["status"] == "completed" and sandbox["launched"] == [("code", [])]


def test_model_origin_always_needs_approval(sandbox):
    a = actions.request("open_app", {"app": "vs code"}, "model")
    assert a["status"] == "pending" and not sandbox["launched"]
    done = actions.approve(a["id"])
    assert done["status"] == "completed" and sandbox["launched"] == [("code", [])]
    with pytest.raises(ValueError):
        actions.approve(a["id"])  # can't run twice


def test_unknown_app_is_blocked_not_guessed(sandbox):
    a = actions.request("open_app", {"app": "definitely not an app"}, "command")
    assert a["status"] == "blocked" and not sandbox["launched"]
    assert actions.request("open_app", {"app": "firefox"}, "command")["status"] == "failed"  # not installed


def test_folders_and_files(sandbox):
    tmp = sandbox["tmp"]
    a = actions.request("create_folder", {"name": "Research"}, "command")
    assert a["status"] == "completed" and (tmp / "Research").is_dir()
    assert "already exists" in actions.request("create_folder", {"name": "Research"}, "command")["summary"]
    a = actions.request("create_file", {"name": "todo.txt", "location": str(tmp), "content": "buy milk"}, "command")
    assert a["status"] == "completed" and (tmp / "todo.txt").read_text() == "buy milk"
    # Overwriting needs approval, with a warning.
    a = actions.request("create_file", {"name": "todo.txt", "location": str(tmp), "content": "new"}, "command")
    assert a["status"] == "pending" and a["risk"] == "danger"
    assert (tmp / "todo.txt").read_text() == "buy milk"
    actions.approve(a["id"])
    assert (tmp / "todo.txt").read_text() == "new"
    assert actions.request("create_folder", {"name": "bad|name"}, "command")["status"] == "blocked"


def test_delete_goes_to_recycle_bin_after_approval(sandbox):
    f = sandbox["tmp"] / "old.txt"
    f.write_text("x")
    a = actions.request("delete_path", {"path": str(f)}, "command")
    assert a["status"] == "pending" and a["risk"] == "danger" and "Recycle Bin" in a["reason"]
    assert f.exists()
    actions.reject(a["id"])
    assert f.exists() and actions.get_action(a["id"])["status"] == "rejected"
    a = actions.request("delete_path", {"path": str(f)}, "command")
    actions.approve(a["id"])
    assert sandbox["trashed"] == [str(f)] and not f.exists()


@pytest.mark.parametrize("path", ["C:\\Windows\\System32\\drivers", "C:\\Program Files", str(Path.home()), "C:\\"])
def test_protected_paths_are_blocked(sandbox, path):
    if not Path(path).exists():
        pytest.skip("path not on this machine")
    assert actions.request("delete_path", {"path": path}, "command")["status"] == "blocked"


def test_babilious_own_data_is_protected(sandbox):
    from babilious import config
    assert places.write_guard(config.DATA_DIR / "babilious.db")


def test_move_never_overwrites(sandbox):
    tmp = sandbox["tmp"]
    (tmp / "a.txt").write_text("a")
    (tmp / "b.txt").write_text("b")
    assert actions.request("move_path", {"source": str(tmp / "a.txt"), "destination": str(tmp / "b.txt")},
                           "command")["status"] == "blocked"
    a = actions.request("move_path", {"source": str(tmp / "a.txt"), "destination": str(tmp / "c.txt")}, "command")
    assert a["status"] == "pending"
    actions.approve(a["id"])
    assert (tmp / "c.txt").read_text() == "a" and not (tmp / "a.txt").exists()


def test_list_read_and_search(sandbox):
    tmp = sandbox["tmp"]
    (tmp / "proj").mkdir()
    (tmp / "proj" / "signal.py").write_text("def parse_signal(x):\n    return x\n")
    (tmp / "proj" / "notes.md").write_text("radar notes")
    a = actions.request("list_folder", {"path": str(tmp / "proj")}, "command")
    assert a["status"] == "completed" and "signal.py" in a["output"] and "1 folders" not in a["summary"]
    a = actions.request("read_file", {"path": str(tmp / "proj" / "notes.md")}, "command")
    assert a["output"].strip() == "radar notes" and a["followup"]
    a = actions.request("search_folder", {"folder": str(tmp / "proj"), "pattern": "*.py", "contains": "parse_signal"},
                        "command")
    assert "signal.py" in a["output"] and "1:" in a["output"]


def test_real_powershell_commands(sandbox):
    a = actions.request("run_command", {"command": "echo hello-babilious", "cwd": str(sandbox["tmp"])}, "command")
    assert a["status"] == "completed" and "hello-babilious" in a["output"]
    a = actions.request("run_command", {"command": "python -c \"print(6*7)\"", "cwd": str(sandbox["tmp"])}, "command")
    assert a["status"] == "pending"
    done = actions.approve(a["id"])
    assert done["status"] == "completed" and "42" in done["output"]
    a = actions.request("run_command", {"command": "python -c \"import sys; sys.exit(3)\""}, "command")
    done = actions.approve(a["id"])
    assert done["status"] == "failed" and done["data"]["exit_code"] == 3
    assert actions.last_failed_output() is None  # no output to explain
    assert actions.request("run_command", {"command": "format c:"}, "command")["status"] == "blocked"


def test_run_tests_detects_pytest(sandbox):
    proj = sandbox["tmp"] / "demo"
    (proj / "tests").mkdir(parents=True)
    (proj / "tests" / "test_x.py").write_text("def test_ok():\n    assert 1 + 1 == 2\n\ndef test_bad():\n    assert 2 + 2 == 5\n")
    a = actions.request("run_tests", {"project": str(proj)}, "command")
    assert a["status"] == "pending" and "pytest" in a["preview"]
    done = actions.approve(a["id"])
    assert done["status"] == "failed" and "1 failed, 1 passed" in done["summary"]
    assert actions.last_failed_output()["id"] == done["id"]
    assert actions.request("run_tests", {"project": "no-such-project-xyz"}, "command")["status"] == "blocked"


def test_media_volume_clipboard(sandbox):
    actions.request("media_control", {"action": "play_pause"}, "command")
    actions.request("volume", {"action": "up", "steps": 3}, "command")
    assert sandbox["keys"] == [("play_pause", 1), ("volume_up", 3)]
    assert actions.request("lock_screen", {}, "command")["status"] == "pending"
    a = actions.request("read_clipboard", {}, "command")
    assert "return a - b" in a["output"]
    actions.request("write_clipboard", {"text": "copied!"}, "command")
    assert sandbox["clip"]["text"] == "copied!"


def test_web_url_rules():
    from babilious.tools.browser import _click_risk, _fill_risk, normalise_url
    assert normalise_url("github") == "https://github.com"
    assert normalise_url("example.org/docs") == "https://example.org/docs"
    assert normalise_url("best pizza lahore").startswith("https://duckduckgo.com/?q=best+pizza")
    assert actions.request("open_url", {"url": "file:///C:/Windows/win.ini"}, "command")["status"] == "blocked"
    assert _click_risk({"target": "Submit order"}).level == "danger"
    assert _click_risk({"target": "Next page"}).level == "confirm"
    assert _fill_risk({"field": "Password"}).level == "blocked"


def test_actions_expire(sandbox):
    a = actions.request("lock_screen", {}, "command")
    with db.connect() as conn:
        conn.execute("UPDATE actions SET created_at = '2000-01-01T00:00:00+00:00' WHERE id = ?", (a["id"],))
    with pytest.raises(ValueError, match="expired"):
        actions.approve(a["id"])


# ---------------------------------------------------------------- through the chat

def _chat(client, text, cid=None, via="text"):
    r = client.post("/api/chat/stream", json={"message": text, "conversation_id": cid, "via": via}, headers=H)
    events = [json.loads(line) for line in r.get_data(as_text=True).splitlines()]
    return events[0]["id"], events[-1]


def test_chat_runs_safe_action_and_reports_truthfully(client, sandbox):
    _, done = _chat(client, "Open VS Code")
    assert done["source"] == "tool" and done["text"] == "Done. Opened VS Code."
    assert done["actions"][0]["status"] == "completed"
    _, done = _chat(client, "open firefox")
    assert done["text"].startswith("That didn't work") and done["mood"] == "error"


def test_chat_pending_card_and_spoken_approval(client, sandbox):
    cid, done = _chat(client, "lock my pc")
    assert done["actions"][0]["status"] == "pending" and "needs your OK" in done["text"]
    cid, done = _chat(client, "yes, do it", cid)
    assert done["actions"][0]["status"] == "completed"


def test_bare_yes_does_not_approve_stale_actions(client, sandbox):
    cid, _ = _chat(client, "lock my pc")
    cid, _ = _chat(client, "tell me a joke", cid)          # conversation moved on
    cid, done = _chat(client, "yes", cid)
    assert done["source"] == "llm"                         # just chat
    assert actions.list_actions("pending")                 # still waiting for a click


def test_danger_needs_a_click_not_a_word(client, sandbox):
    f = sandbox["tmp"] / "keep.txt"
    f.write_text("x")
    cid, done = _chat(client, f"delete the file {f}")
    assert done["actions"][0]["risk"] == "danger"
    cid, done = _chat(client, "go ahead", cid)
    assert "needs a click" in done["text"] and f.exists()


def test_followup_tools_feed_the_model(client, sandbox):
    (sandbox["tmp"] / "plan.md").write_text("Step 1: calibrate the FMCW radar. Step 2: write chapter 3.")
    _, done = _chat(client, f"read the file {sandbox['tmp'] / 'plan.md'}")
    assert done["source"] == "llm" and done["actions"][0]["tool"] == "read_file"
    ctx = client.fake.last_messages[-2]["content"]
    assert "calibrate the FMCW radar" in ctx
    _, done = _chat(client, "explain what I copied")
    assert "return a - b" in client.fake.last_messages[-2]["content"]


def test_why_did_it_fail_uses_last_output(client, sandbox):
    a = actions.request("run_command", {"command": "python -c \"raise ValueError('calibration offset missing')\""},
                        "command")
    actions.approve(a["id"])
    _, done = _chat(client, "why did that fail?")
    assert done["source"] == "llm"
    assert "calibration offset missing" in client.fake.last_messages[-2]["content"]


def test_screen_reading_with_real_ocr(client, sandbox, monkeypatch):
    from PIL import Image, ImageDraw, ImageFont
    from babilious.tools import screen
    shots = sandbox["tmp"] / "shots"
    shots.mkdir()

    def fake_capture(delay=0):
        img = Image.new("RGB", (900, 200), "white")
        try:
            font = ImageFont.truetype("C:/Windows/Fonts/consola.ttf", 24)
        except OSError:
            font = None
        ImageDraw.Draw(img).text((20, 60), "ModuleNotFoundError: No module named 'flask_sock'", fill="black", font=font)
        p = shots / "screen-test.png"
        img.save(p)
        return p
    monkeypatch.setattr(screen, "capture", fake_capture)
    _, done = _chat(client, "explain this error", via="voice")
    assert done["source"] == "llm" and done["actions"][0]["data"]["screenshot"] == "screen-test.png"
    assert "ModuleNotFoundError" in client.fake.last_messages[-2]["content"]


def test_model_tool_routing_needs_approval(client, sandbox, monkeypatch):
    def chat_tools(model, messages, tools, options=None):
        assert any(t["function"]["name"] == "open_app" for t in tools)
        return {"role": "assistant", "content": "",
                "tool_calls": [{"function": {"name": "open_app", "arguments": {"app": "notepad"}}}]}
    client.fake.chat_tools = chat_tools
    _, done = _chat(client, "launch the text editor thing for me")
    assert done["actions"][0]["origin"] == "model" and done["actions"][0]["status"] == "pending"
    assert not sandbox["launched"]
    db.set_setting("model_tool_routing", False)
    _, done = _chat(client, "launch the text editor thing for me")
    assert done["source"] == "llm"


def test_actions_api(client, sandbox):
    a = actions.request("lock_screen", {}, "command")
    assert client.post(f"/api/actions/{a['id']}/approve").status_code == 403       # header guard
    assert client.get("/api/actions?status=pending").get_json()["actions"][0]["id"] == a["id"]
    assert client.post(f"/api/actions/{a['id']}/reject", headers=H).get_json()["status"] == "rejected"
    assert client.post(f"/api/actions/{a['id']}/approve", headers=H).status_code == 400
    for bad in ("..%2Fbabilious.db", "screen-x.png", "x.png"):
        assert client.get(f"/api/screenshots/{bad}").status_code == 404
    info = client.get("/api/automation").get_json()
    assert info["projects"]["babilious"] and any(t["name"] == "run_tests" for t in info["tools"])
    r = client.patch("/api/settings", json={"projects": {"radar": str(sandbox["tmp"])}}, headers=H)
    assert r.status_code == 200 and places.find_project("radar") == sandbox["tmp"]
    assert client.patch("/api/settings", json={"custom_apps": {"evil": "C:\\nope.exe"}}, headers=H).status_code == 400
