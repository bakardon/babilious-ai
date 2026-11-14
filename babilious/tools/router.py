"""Map clear requests to tools without asking the model.

Small local models are unreliable at choosing tools, so common phrasings are
matched here. Anything action-like that doesn't match can still be handed to
the model's tool calling (see ``model_route``), and those proposals always need
approval.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Callable

from . import REGISTRY, load_all
from .apps import APPS, custom_apps

log = logging.getLogger(__name__)

POLITE = re.compile(r"^\s*(?:(?:hey|ok(?:ay)?)\s+)?(?:babilious[\s,:!.-]*)?(?:(?:please|can you|could you|would you|"
                    r"will you|go ahead and|i want you to|i need you to)\s+)*", re.I)
SIZE = r"(?:the\s+|my\s+|a\s+)?"


@dataclass
class Route:
    tool: str
    args: dict[str, Any]
    question: str = ""     # for follow-up tools: what to answer from the result


def _clean(text: str) -> str:
    t = POLITE.sub("", text.strip())
    return t.strip().rstrip(".!")


def _app_names() -> str:
    names = sorted(set(APPS) | set(custom_apps()), key=len, reverse=True)
    return "|".join(re.escape(n) for n in names)


LOCATION = r"(?:on|in|inside|under|to)\s+(?:my\s+|the\s+)?(?P<loc>[\w :\\./-]+?)(?:\s+folder)?"


def route(text: str, via: str = "text", last_reply: str = "") -> Route | None:
    load_all()
    t = _clean(text)
    low = t.lower()
    apps = _app_names()

    # ---- apps (optionally with a site): "open chrome and go to github", "launch vs code"
    m = re.match(rf"^(?:open|launch|start|fire up|bring up)\s+{SIZE}(?P<app>{apps})(?:\s+app)?"
                 rf"(?:\s+(?:and|then)\s+(?:go to|navigate to|open|visit|load)\s+(?P<site>.+))?$", low)
    if m:
        if m.group("site"):
            if m.group("app") in ("chrome", "google chrome", "edge", "microsoft edge", "firefox"):
                browser = m.group("app").split()[-1]
                return Route("open_url", {"url": m.group("site"), "browser": browser})
            return Route("open_app", {"app": m.group("app"), "argument": m.group("site")})
        return Route("open_app", {"app": m.group("app")})

    # ---- projects / folders: "open my babilious project (in vs code)"
    m = re.match(r"^open\s+(?:up\s+)?(?:my\s+|the\s+)?(?P<name>[\w .-]+?)\s+(?:project|repo|repository|folder|directory)"
                 r"(?:\s+in\s+(?P<app>vs ?code|visual studio code|explorer|file explorer|(?:a\s+)?terminal|powershell))?$", low)
    if m:
        app = (m.group("app") or "").replace("visual studio code", "vscode").replace("vs code", "vscode")
        app = "terminal" if "terminal" in app else app
        app = "" if "explorer" in app else app
        return Route("open_path", {"path": m.group("name"), "app": app})
    m = re.match(r"^open\s+(?P<path>[a-z]:\\.+?)(?:\s+in\s+(?P<app>vs ?code|terminal|powershell))?$", t, re.I)
    if m:
        app = (m.group("app") or "").lower().replace("vs code", "vscode")
        return Route("open_path", {"path": m.group("path"), "app": app})

    # ---- websites: "go to github", "open https://...", "open youtube"
    from .browser import SITES
    m = re.match(r"^(?:go to|navigate to|visit|browse to|open)\s+(?P<site>https?://\S+|[\w-]+(?:\.[\w-]+)+\S*|"
                 + "|".join(re.escape(s) for s in SITES) + r")(?:\s+website|\s+site)?$", low)
    if m:
        return Route("open_url", {"url": m.group("site")})
    m = re.match(r"^(?:search|google|look up)\s+(?:the web\s+|online\s+|google\s+)?for\s+(?P<q>.+)$|^google\s+(?P<q2>.+)$", low)
    if m:
        return Route("open_url", {"url": m.group("q") or m.group("q2")})

    # ---- read a web page
    m = re.match(r"^(?:read|summari[sz]e|what does|what's on|explain)\s+(?:the\s+|this\s+)?(?:page|website|site|article|link)?\s*"
                 r"(?:at\s+)?(?P<url>https?://\S+)(?:\s+say)?\??$", t, re.I)
    if m:
        return Route("read_webpage", {"url": m.group("url")}, question=t)

    # ---- screen
    delay = 0 if via == "voice" else 3   # typed: give you time to switch to the window you mean
    if re.search(r"\b(take|grab|capture|snap)\s+(?:a\s+)?(?:screenshot|screen ?shot|screen capture)\b", low):
        return Route("take_screenshot", {"delay": delay})
    if re.search(r"what'?s on my screen|what am i looking at|read (?:this|my|the) (?:screen|window)|"
                 r"explain (?:this|the) (?:error|screen|window|page|dialog|message)(?: on (?:my|the) screen)?$|"
                 r"help me understand (?:this|the) (?:website|page|screen|window|error)|look at my screen|"
                 r"(?:find|spot) the error on (?:my|the) screen", low):
        return Route("read_screen", {"delay": delay}, question=t)

    # ---- clipboard
    if re.search(r"what'?s (?:in|on) (?:my|the) clipboard|read (?:my|the) clipboard|"
                 r"explain (?:what i(?:'ve)? copied|my clipboard|the clipboard|this code i copied)", low):
        return Route("read_clipboard", {}, question=t)
    m = re.match(r"^copy\s+(?P<what>that|this|it|the code|your (?:last )?answer|the (?:last )?answer)\s+(?:to|into)\s+(?:my\s+|the\s+)?clipboard$", low)
    if m and last_reply:
        code = re.findall(r"```(?:\w+)?\n([\s\S]*?)```", last_reply)
        text = code[0] if (code and "code" in m.group("what") or code and len(code) == 1) else last_reply
        return Route("write_clipboard", {"text": text.strip()})

    # ---- folders & files
    m = re.match(r"^(?:create|make)\s+(?:a\s+)?(?:new\s+)?(?:folder|directory)\s+(?:called\s+|named\s+)?[\"']?"
                 r"(?P<name>[^\"']+?)[\"']?(?:\s+" + LOCATION + r")?$", t, re.I)
    if m:
        return Route("create_folder", {"name": m.group("name").strip(), "location": (m.group("loc") or "").strip()})
    m = re.match(r"^(?:create|make)\s+(?:a\s+)?(?:new\s+)?(?:text\s+)?file\s+(?:called\s+|named\s+)?[\"']?(?P<name>[^\s\"']+)[\"']?"
                 r"(?:\s+" + LOCATION + r")?(?:\s+(?:with|containing|that says)\s+(?P<content>[\s\S]+))?$", t, re.I)
    if m:
        return Route("create_file", {"name": m.group("name"), "location": (m.group("loc") or "").strip(),
                                     "content": (m.group("content") or "").strip().strip("\"'")})
    m = re.match(r"^(?:delete|remove|trash|bin)\s+(?:the\s+)?(?:file\s+|folder\s+)?(?P<path>.+)$", t, re.I)
    if m and not re.search(r"\b(task|memory|memories|conversation|model|reminder)\b", m.group("path"), re.I):
        return Route("delete_path", {"path": m.group("path").strip().strip("\"'")})
    m = re.match(r"^(?:move|rename)\s+(?P<src>.+?)\s+(?:to|as|into)\s+(?P<dst>.+)$", t, re.I)
    if m and not re.search(r"\btask\b", low):
        return Route("move_path", {"source": m.group("src").strip("\"' "), "destination": m.group("dst").strip("\"' ")})
    m = re.match(r"^(?:what'?s|what is|show(?: me)?|list)\s+(?:in|inside|the contents of|the files in)\s+(?:my\s+|the\s+)?"
                 r"(?P<loc>[\w :\\./-]+?)(?:\s+folder)?\??$", t, re.I)
    if m:
        return Route("list_folder", {"path": m.group("loc").strip()})
    m = re.match(r"^(?:read|open and read|show me)\s+(?:the\s+)?file\s+(?P<path>.+)$|^read\s+(?P<p2>[a-z]:\\.+)$", t, re.I)
    if m:
        return Route("read_file", {"path": (m.group("path") or m.group("p2")).strip("\"' ")}, question=t)
    m = re.match(r"^(?:search|look)\s+(?:in\s+)?(?P<folder>[a-z]:\\.+?|(?:my\s+)?[\w-]+(?:\s+project)?)\s+for\s+"
                 r"(?:(?P<kind>[\w.]+)\s+files?\s+)?(?:containing|with|that contain|mentioning)\s+[\"']?(?P<needle>[^\"']+)[\"']?$", t, re.I)
    if m:
        kind = (m.group("kind") or "").lower()
        pattern = {"python": "*.py", "py": "*.py", "javascript": "*.js", "js": "*.js", "typescript": "*.ts",
                   "markdown": "*.md", "text": "*.txt", "matlab": "*.m", "c": "*.c"}.get(kind, f"*.{kind}" if kind else "*")
        return Route("search_folder", {"folder": m.group("folder"), "pattern": pattern, "contains": m.group("needle")})

    # ---- terminal
    m = re.match(r"^run\s+(?:the\s+|my\s+)?tests?(?:\s+(?:for|in|of|on)\s+(?:my\s+|the\s+)?(?P<proj>[\w .:\\-]+?)(?:\s+project)?)?$", low)
    if m:
        proj = (m.group("proj") or "").strip()
        if proj in ("", "project", "this", "this project", "current project"):
            proj = "babilious"
        return Route("run_tests", {"project": proj})
    # The command is delimited by matching quotes/backticks, so it may contain the other kinds.
    m = re.match(r"^(?:in\s+(?:powershell|the terminal),?\s+)?run\s+(?:the\s+command\s+)?(?P<q>`|\"|')(?P<cmd>.+?)(?P=q)"
                 r"(?:\s+in\s+(?:my\s+|the\s+)?(?P<cwd>[\w .:\\-]+?)(?:\s+project|\s+folder)?)?$", t, re.I)
    if m:
        return Route("run_command", {"command": m.group("cmd"), "cwd": (m.group("cwd") or "").strip()})
    # ---- git & GitHub
    proj = r"(?:\s+(?:of|for|in|on)\s+(?:my\s+|the\s+)?(?P<proj>[\w .-]+?)(?:\s+(?:project|repo|repository))?)?"
    m = (re.match(r"^(?:what'?s\s+the\s+|show(?: me)?\s+(?:the\s+)?|check\s+(?:the\s+)?)?(?:git|repo) status" + proj + r"\??$", low)
         or re.match(r"^(?:did i|have i) (?:commit|push)(?:ted|ed)? (?:everything|my (?:changes|work))" + proj + r"\??$", low)
         or re.match(r"^(?:are there |do i have )?(?:any )?(?:uncommitted|unpushed) (?:changes|commits|work)" + proj + r"\??$", low))
    if m:
        return Route("git_status", {"project": (m.group("proj") or "babilious").strip()})
    m = (re.match(r"^(?:write|draft|suggest|give me)\s+(?:me\s+)?(?:a\s+)?commit message" + proj + r"$", low)
         or re.match(r"^what (?:did i|have i) change(?:d)?" + proj + r"\??$", low)
         or re.match(r"^(?:explain|summari[sz]e|review) my (?:uncommitted )?changes" + proj + r"$", low))
    if m:
        q = ("Write a git commit message for these changes: a short imperative subject line (max 60 chars), a blank "
             "line, then 2-5 bullet points about what the code changes do, covering the whole list of changed and "
             "new files, not just the first one. Don't write bullets about the message itself. "
             "Output only the message in a code block."
             if "commit message" in low else t)
        return Route("git_diff", {"project": (m.group("proj") or "babilious").strip()}, question=q)
    m = re.match(r"^commit\s+(?:all\s+)?(?:my\s+)?(?:changes\s+|everything\s+)?(?:in\s+|for\s+|on\s+)?(?:my\s+|the\s+)?"
                 r"(?P<proj>[\w .-]+?)?(?:\s+project)?\s+(?:with\s+(?:the\s+)?message|saying|as|-m)\s+(?P<q>[\"'`])(?P<msg>.+)(?P=q)$", t, re.I)
    if m:
        return Route("git_commit", {"project": (m.group("proj") or "babilious").strip(), "message": m.group("msg")})
    m = re.match(r"^push\s+(?:my\s+|the\s+)?(?:changes|commits|code)?\s*(?:(?:of|for|in|on|to)\s+)?(?:my\s+|the\s+)?"
                 r"(?P<proj>[\w.-]+?)?(?:\s+(?:project|repo))?(?:\s+to\s+(?:github|origin|the remote))?$", low)
    if m and low != "push":
        return Route("git_push", {"project": (m.group("proj") or "babilious").strip()})
    m = (re.match(r"^(?:show|list|what are|check)(?:\s+me)?\s+(?:the\s+|my\s+)?(?:open\s+)?(?:github\s+)?issues" + proj + r"\??$", low)
         or re.match(r"^(?:any\s+)?open issues" + proj + r"\??$", low))
    if m:
        return Route("github_issues", {"project": (m.group("proj") or "babilious").strip()})

    # ---- vision model (optional): describe rather than read
    if re.search(r"^(?:describe|look at)\s+(?:my|the|this)\s+screen\b|^what do you see(?: on my screen)?\??$|"
                 r"^(?:look at my screen and )?describe what(?:'s| is) on (?:my|the) screen", low):
        return Route("describe_screen", {"question": t if not re.match(r"^describe (?:my|the|this) screen$", low) else ""},
                     question=t)
    m = re.match(r"^(?:describe|what'?s in|look at)\s+(?:this\s+|the\s+)?(?:image|picture|photo|screenshot)\s+(?P<path>[a-z]:\\.+|\S+\.(?:png|jpe?g|gif|bmp|webp))$", t, re.I)
    if m:
        return Route("describe_image", {"path": m.group("path"), "question": "Describe this image."})

    # ---- media & volume (works with Spotify via media keys)
    if re.match(r"^(?:pause|resume|play|unpause|toggle)\s+(?:the\s+)?(?:music|song|track|spotify|playback|media|video)$|^(?:pause|play)$", low):
        return Route("media_control", {"action": "play_pause"})
    if re.match(r"^(?:next|skip)(?:\s+(?:this|the))?\s*(?:song|track)?$|^skip$", low):
        return Route("media_control", {"action": "next"})
    if re.match(r"^(?:previous|last|go back a)\s+(?:song|track)$", low):
        return Route("media_control", {"action": "previous"})
    m = re.match(r"^(?:turn\s+(?:the\s+)?volume\s+(?P<a>up|down)|volume\s+(?P<b>up|down)|turn\s+it\s+(?P<c>up|down)"
                 r"|(?P<d>louder|quieter))(?:\s+a\s+(?P<lot>lot|bit))?$", low)
    if m:
        d = m.group("a") or m.group("b") or m.group("c") or ("up" if m.group("d") == "louder" else "down")
        return Route("volume", {"action": d, "steps": 10 if m.group("lot") == "lot" else 5})
    if re.match(r"^(?:un)?mute(?:\s+(?:the\s+)?(?:sound|volume|audio|pc|computer))?$", low):
        return Route("volume", {"action": "mute"})
    if re.match(r"^lock\s+(?:my\s+|the\s+)?(?:pc|computer|screen|laptop)$", low):
        return Route("lock_screen", {})
    m = re.match(r"^open\s+(?:the\s+)?(?:windows\s+)?(?P<page>display|sound|bluetooth|wifi|network|battery|apps|updates|privacy|microphone|storage)?\s*settings$", low)
    if m:
        return Route("open_settings", {"page": m.group("page") or ""})
    return None


# ---------------------------------------------------------------- model tool calling (fallback)
ACTION_HINT = re.compile(r"^(?:open|launch|start|run|execute|create|make|delete|remove|move|rename|copy|paste|take|"
                         r"show|go|navigate|visit|search|find|close|type|click|fill|read|list|turn|mute|pause|play|"
                         r"skip|lock|download|install|check|look|capture|screenshot)\b", re.I)
MODEL_TOOLS = ["git_status", "github_issues", "open_app", "open_path", "open_url", "list_folder", "read_file", "create_folder", "create_file",
               "delete_path", "move_path", "search_folder", "run_command", "run_tests", "take_screenshot",
               "read_screen", "read_clipboard", "media_control", "volume", "read_webpage", "web_goto", "web_fill",
               "web_click", "web_read"]


def looks_like_action(text: str) -> bool:
    return bool(ACTION_HINT.match(_clean(text)))


TOOL_HINTS = {  # keep the prompt small: CPU prompt processing is the slow part
    "open_app": r"\b(open|launch|start|app|program|calculator|notepad|chrome|edge|code|terminal|powershell|spotify|word|excel)\b",
    "open_path": r"\b(open|show|folder|directory|project|desktop|documents|downloads)\b",
    "open_url": r"\b(website|site|web|online|browser|url|http|search|look up|google|ip address)\b",
    "list_folder": r"\b(files|folder|contents|what'?s in|list|desktop|documents|downloads)\b",
    "read_file": r"\b(read|file|document|txt|pdf|docx)\b",
    "create_folder": r"\b(folder|directory)\b", "create_file": r"\b(file|note|txt)\b",
    "delete_path": r"\b(delete|remove|trash|bin)\b", "move_path": r"\b(move|rename)\b",
    "search_folder": r"\b(search|find|look for|containing)\b",
    "run_command": r"\b(run|command|terminal|powershell|check|install|version|packages|git|ip)\b",
    "run_tests": r"\btests?\b", "take_screenshot": r"\b(screenshot|screen ?shot|capture)\b",
    "read_screen": r"\b(screen|window|error|looking at)\b", "read_clipboard": r"\b(clipboard|copied|paste)\b",
    "media_control": r"\b(music|song|track|play|pause|spotify|media)\b", "volume": r"\b(volume|louder|quieter|mute|sound)\b",
    "read_webpage": r"\b(page|article|website|link|http)\b",
    "git_status": r"\b(git|commit|branch|repo|pushed|uncommitted)\b", "github_issues": r"\b(issues?|github|bugs?)\b",
    "web_goto": r"\b(automation|in the browser|fill|form)\b", "web_fill": r"\b(type|fill|enter|form|field)\b",
    "web_click": r"\b(click|press|button)\b", "web_read": r"\b(page|automation window)\b",
}


def _pick_tools(text: str) -> list[str]:
    low = text.lower()
    picked = [n for n in MODEL_TOOLS if re.search(TOOL_HINTS.get(n, r"$^"), low)]
    return (picked or ["open_app", "open_path", "open_url", "run_command"])[:6]


def _coerce(tool, args: dict[str, Any]) -> dict[str, Any] | None:
    """Fix near-miss enum values from the model ("play" → "play_pause"); drop impossible ones."""
    out = {}
    for k, v in args.items():
        if k not in tool.params:
            continue
        enum = tool.params[k].get("enum")
        if enum and v not in enum:
            match = [e for e in enum if e and (str(v).lower() in e or e in str(v).lower())]
            if len(match) != 1:
                return None
            v = match[0]
        out[k] = v
    return out


def model_route(text: str, client: Any, model: str) -> list[Route]:
    """Ask the local model to pick tools. Returns proposals (all will need approval)."""
    load_all()
    tools = [REGISTRY[n].schema() for n in _pick_tools(_clean(text)) if n in REGISTRY]
    system = ("You turn the user's request into tool calls on their Windows PC. Use a tool only if the request "
              "clearly asks for that action; otherwise call no tool. Use exact names and paths from the request.")
    try:
        msg = client.chat_tools(model, [{"role": "system", "content": system}, {"role": "user", "content": _clean(text)}],
                                tools, options={"temperature": 0, "num_ctx": 4096, "num_predict": 200})
    except Exception as exc:  # noqa: BLE001 - model routing is best effort
        log.warning("Model tool routing failed: %s", exc)
        return []
    routes = []
    for call in msg.get("tool_calls") or []:
        fn = call.get("function", {})
        name, args = fn.get("name"), fn.get("arguments") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                continue
        if name not in MODEL_TOOLS:
            continue
        tool = REGISTRY[name]
        if any(r not in args or args[r] in ("", None) for r in tool.required):
            continue
        clean_args = _coerce(tool, args)
        if clean_args is None:
            continue
        routes.append(Route(name, clean_args, question=text if tool.followup else ""))
    return routes[:3]


ToolHandler = Callable[[str], Route | None]
