"""Launch applications from an allowlist (plus apps you add yourself)."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .. import db
from . import Risk, Tool, ToolResult, register

# spoken name -> (launch target, friendly name). Targets are App Paths names, PATH commands or URIs.
APPS: dict[str, tuple[str, str]] = {
    "vs code": ("code", "VS Code"), "vscode": ("code", "VS Code"), "visual studio code": ("code", "VS Code"),
    "code": ("code", "VS Code"),
    "powershell": ("powershell", "PowerShell"), "terminal": ("wt", "Windows Terminal"),
    "windows terminal": ("wt", "Windows Terminal"), "command prompt": ("cmd", "Command Prompt"), "cmd": ("cmd", "Command Prompt"),
    "notepad": ("notepad", "Notepad"), "calculator": ("calc", "Calculator"), "calc": ("calc", "Calculator"),
    "chrome": ("chrome.exe", "Chrome"), "google chrome": ("chrome.exe", "Chrome"),
    "edge": ("msedge.exe", "Edge"), "microsoft edge": ("msedge.exe", "Edge"), "firefox": ("firefox.exe", "Firefox"),
    "spotify": ("spotify", "Spotify"),
    "explorer": ("explorer", "File Explorer"), "file explorer": ("explorer", "File Explorer"),
    "files": ("explorer", "File Explorer"),
    "task manager": ("taskmgr", "Task Manager"), "paint": ("mspaint", "Paint"),
    "word": ("winword.exe", "Word"), "microsoft word": ("winword.exe", "Word"), "excel": ("excel.exe", "Excel"),
    "powerpoint": ("powerpnt.exe", "PowerPoint"), "outlook": ("outlook.exe", "Outlook"),
    "onenote": ("onenote.exe", "OneNote"), "teams": ("ms-teams.exe", "Teams"),
    "matlab": ("matlab", "MATLAB"), "snipping tool": ("SnippingTool.exe", "Snipping Tool"),
    "settings": ("ms-settings:", "Settings"), "windows settings": ("ms-settings:", "Settings"),
    "git bash": (r"C:\Program Files\Git\git-bash.exe", "Git Bash"),
}


def _app_path_registered(exe: str) -> bool:
    if os.name != "nt" or not exe.lower().endswith(".exe"):
        return False
    import winreg
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, rf"Software\Microsoft\Windows\CurrentVersion\App Paths\{exe}"):
                return True
        except OSError:
            continue
    return False


def available(target: str) -> bool:
    if target.endswith(":"):
        return True  # URI like ms-settings:
    if Path(target).is_absolute():
        return Path(target).exists()
    return bool(shutil.which(target) or _app_path_registered(target))


def custom_apps() -> dict[str, str]:
    return {k.lower(): v for k, v in (db.get_setting("custom_apps", {}) or {}).items()}


def lookup(name: str) -> tuple[str, str] | None:
    key = " ".join(name.lower().replace("the ", "").split())
    if key in custom_apps():
        return custom_apps()[key], name.strip().title()
    return APPS.get(key)


def launch(target: str, args: list[str] | None = None) -> None:
    """Start an app detached from Babilious (closing Babilious won't close it)."""
    args = args or []
    if os.name != "nt":  # pragma: no cover
        subprocess.Popen([target, *args])
        return
    if target in ("powershell", "cmd"):
        # Console apps need their own window.
        subprocess.Popen([target, *args], creationflags=subprocess.CREATE_NEW_CONSOLE)
        return
    # `start` resolves App Paths (chrome.exe, winword.exe...) and URIs.
    subprocess.Popen(["cmd", "/c", "start", "", target, *args], creationflags=subprocess.CREATE_NO_WINDOW)


def _risk(args: dict[str, Any]) -> Risk:
    if not lookup(args.get("app", "")):
        return Risk("blocked", f"\"{args.get('app')}\" isn't on my list of apps. Add it under Automation > Apps.")
    return Risk("safe")


def _open_app(args: dict[str, Any]) -> ToolResult:
    found = lookup(args["app"])
    if not found:
        return ToolResult(False, f"I don't know an app called {args['app']}.")
    target, label = found
    if not available(target):
        return ToolResult(False, f"{label} doesn't seem to be installed.")
    extra = [args["argument"]] if args.get("argument") else []
    launch(target, extra)
    return ToolResult(True, f"Opened {label}" + (f" with {args['argument']}" if extra else "") + ".",
                      data={"app": label})


register(Tool("open_app", "Open an application by name (VS Code, PowerShell, Chrome, Spotify, Word, ...).",
              {"app": {"type": "string", "description": "App name, e.g. 'vs code', 'chrome', 'spotify'"},
               "argument": {"type": "string", "description": "Optional file, folder or URL to open with it"}},
              ["app"], _open_app,
              lambda a: f"Open {(lookup(a.get('app', '')) or (None, a.get('app')))[1]}" +
              (f" with {a['argument']}" if a.get("argument") else ""), _risk))


def installed_apps() -> list[dict[str, Any]]:
    seen, out = set(), []
    for name, (target, label) in APPS.items():
        if label in seen:
            continue
        seen.add(label)
        out.append({"name": label, "say": name, "installed": available(target), "custom": False})
    for name, path in custom_apps().items():
        out.append({"name": name.title(), "say": name, "installed": Path(path).exists(), "custom": True, "path": path})
    return out
