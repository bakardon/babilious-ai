"""File tools: list, read, open, create, move, delete (to the Recycle Bin), live search."""
from __future__ import annotations

import os
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from .. import documents
from . import Risk, Tool, ToolResult, register, safe
from .places import resolve, write_guard

EXECUTABLE = {".exe", ".bat", ".cmd", ".ps1", ".vbs", ".js", ".jse", ".wsf", ".msi", ".scr", ".com", ".lnk",
              ".reg", ".hta", ".jar", ".py", ".pyw"}
INVALID_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _fmt_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def _startfile(target: str) -> None:
    if os.name == "nt":
        os.startfile(target)  # noqa: S606 - opens with the user's default app
    else:  # pragma: no cover
        subprocess.Popen(["xdg-open", target])


# ---------------------------------------------------------------- list
def _list(args: dict[str, Any]) -> ToolResult:
    p = resolve(args["path"])
    if not p.is_dir():
        return ToolResult(False, f"{p} is not a folder.")
    entries = []
    try:
        for e in os.scandir(p):
            if e.name.lower() in ("desktop.ini", "thumbs.db"):
                continue
            try:
                st = e.stat()
                entries.append((e.is_dir(), e.name, st.st_size, st.st_mtime))
            except OSError:
                continue
    except PermissionError:
        return ToolResult(False, f"Windows won't let me read {p}.")
    entries.sort(key=lambda x: (not x[0], -x[3]))
    lines = [f"{'[folder]' if d else _fmt_size(s):>9}  {datetime.fromtimestamp(m):%Y-%m-%d}  {n}"
             for d, n, s, m in entries[:200]]
    more = f"\n... and {len(entries) - 200} more" if len(entries) > 200 else ""
    dirs = sum(1 for e in entries if e[0])
    return ToolResult(True, f"{p} has {dirs} folders and {len(entries) - dirs} files.",
                      "\n".join(lines) + more, {"path": str(p)})


register(Tool("list_folder", "List the contents of a folder (newest first).",
              {"path": {"type": "string", "description": "Folder path, or a name like Downloads"}}, ["path"],
              _list, lambda a: f"List the folder {a.get('path')}", safe))


# ---------------------------------------------------------------- read
def _read(args: dict[str, Any]) -> ToolResult:
    p = resolve(args["path"])
    if not p.is_file():
        return ToolResult(False, f"{p} is not a file.")
    try:
        text = documents.extract_text(p) if documents.can_read(p) else None
    except ValueError as exc:
        return ToolResult(False, f"Can't read {p.name}: {exc}")
    if text is None:
        return ToolResult(False, f"I can't read {p.suffix or 'that'} files (only text, Markdown, PDF, Word, code).")
    clipped = text[:20_000]
    note = "" if len(text) <= 20_000 else f"\n[Only the first 20,000 of {len(text):,} characters.]"
    return ToolResult(True, f"Read {p.name} ({len(text):,} characters).", clipped + note,
                      {"path": str(p), "name": p.name})


register(Tool("read_file", "Read a text, Markdown, PDF, Word or code file so you can answer questions about it.",
              {"path": {"type": "string"}}, ["path"], _read, lambda a: f"Read the file {a.get('path')}", safe,
              followup=True))


# ---------------------------------------------------------------- open
def _open_risk(args: dict[str, Any]) -> Risk:
    try:
        p = resolve(args["path"])
    except ValueError:
        return Risk("safe")  # run() will report the problem
    if p.is_file() and p.suffix.lower() in EXECUTABLE and not args.get("app"):
        return Risk("danger", f"{p.name} is a program or script: opening it runs it.")
    return Risk("safe")


def _open(args: dict[str, Any]) -> ToolResult:
    p = resolve(args["path"])
    app = (args.get("app") or "").lower()
    if app in ("vscode", "vs code", "code"):
        from .apps import launch
        launch("code", [str(p)])
        return ToolResult(True, f"Opened {p} in VS Code.", data={"path": str(p)})
    if app in ("terminal", "powershell"):
        from .apps import launch
        launch("wt", ["-d", str(p)]) if app == "terminal" else launch("powershell", ["-NoExit", "-Command",
                                                                                     f"Set-Location -LiteralPath '{p}'"])
        return ToolResult(True, f"Opened a terminal in {p}.", data={"path": str(p)})
    _startfile(str(p))
    kind = "folder" if p.is_dir() else "file"
    return ToolResult(True, f"Opened the {kind} {p}.", data={"path": str(p)})


register(Tool("open_path", "Open a folder in Explorer or a file with its default app, or a folder in VS Code/terminal.",
              {"path": {"type": "string"},
               "app": {"type": "string", "enum": ["", "vscode", "terminal", "powershell"],
                       "description": "Optional: open in VS Code or a terminal"}},
              ["path"], _open,
              lambda a: f"Open {a.get('path')}" + (f" in {a['app']}" if a.get("app") else ""), _open_risk))


# ---------------------------------------------------------------- create folder / file
def _target(args: dict[str, Any]) -> Path:
    name = args["name"].strip().strip('"')
    if Path(name).is_absolute():
        return Path(name)
    if INVALID_NAME.search(name.replace("\\", "").replace("/", "")):
        raise ValueError(f"\"{name}\" isn't a valid file name.")
    base = resolve(args["location"]) if args.get("location") else None
    if base is None:
        from .places import default_folder
        base = default_folder()
    return base / name


def _mkdir_risk(args: dict[str, Any]) -> Risk:
    try:
        why = write_guard(_target(args))
    except ValueError as exc:
        return Risk("blocked", str(exc))
    return Risk("blocked", why) if why else Risk("safe")


def _mkdir(args: dict[str, Any]) -> ToolResult:
    p = _target(args)
    if p.exists():
        return ToolResult(True, f"{p} already exists; I left it alone.", data={"path": str(p)})
    p.mkdir(parents=True)
    return ToolResult(True, f"Created the folder {p}.", data={"path": str(p)})


register(Tool("create_folder", "Create a new folder.",
              {"name": {"type": "string", "description": "Folder name or full path"},
               "location": {"type": "string", "description": "Optional parent: Desktop, Documents, a project or a path"}},
              ["name"], _mkdir, lambda a: f"Create the folder \"{a.get('name')}\"" +
              (f" in {a['location']}" if a.get("location") else " in Documents"), _mkdir_risk))


def _mkfile_risk(args: dict[str, Any]) -> Risk:
    try:
        p = _target(args)
    except ValueError as exc:
        return Risk("blocked", str(exc))
    why = write_guard(p)
    if why:
        return Risk("blocked", why)
    if p.exists():
        return Risk("danger", f"{p.name} already exists and would be overwritten.")
    return Risk("safe")


def _mkfile(args: dict[str, Any]) -> ToolResult:
    p = _target(args)
    existed = p.exists()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(args.get("content") or "", encoding="utf-8")
    return ToolResult(True, f"{'Overwrote' if existed else 'Created'} {p}.", data={"path": str(p)})


register(Tool("create_file", "Create a text file (asks before overwriting).",
              {"name": {"type": "string"}, "location": {"type": "string"},
               "content": {"type": "string", "description": "Text to put in the file"}},
              ["name"], _mkfile, lambda a: f"Create the file \"{a.get('name')}\"" +
              (f" in {a['location']}" if a.get("location") else " in Documents") +
              (f" with {len(a.get('content') or '')} characters" if a.get("content") else " (empty)"), _mkfile_risk))


# ---------------------------------------------------------------- delete (Recycle Bin) / move
def _del_risk(args: dict[str, Any]) -> Risk:
    try:
        p = resolve(args["path"])
    except ValueError as exc:
        return Risk("blocked", str(exc))
    why = write_guard(p)
    if why:
        return Risk("blocked", why)
    what = "folder and everything in it" if p.is_dir() else "file"
    return Risk("danger", f"Moves this {what} to the Recycle Bin (you can restore it from there).")


def _delete(args: dict[str, Any]) -> ToolResult:
    from send2trash import send2trash
    p = resolve(args["path"])
    send2trash(str(p))
    return ToolResult(True, f"Moved {p} to the Recycle Bin.", data={"path": str(p)})


register(Tool("delete_path", "Move a file or folder to the Recycle Bin (always asks first).",
              {"path": {"type": "string"}}, ["path"], _delete,
              lambda a: f"Delete {a.get('path')} (to the Recycle Bin)", _del_risk))


def _move_risk(args: dict[str, Any]) -> Risk:
    try:
        src = resolve(args["source"])
        dst = resolve(args["destination"], must_exist=False)
    except ValueError as exc:
        return Risk("blocked", str(exc))
    why = write_guard(src) or write_guard(dst)
    if why:
        return Risk("blocked", why)
    final = dst / src.name if dst.is_dir() else dst
    if final.exists():
        return Risk("blocked", f"{final} already exists; I won't overwrite it by moving.")
    return Risk("confirm", "Moves or renames a file.")


def _move(args: dict[str, Any]) -> ToolResult:
    import shutil
    src = resolve(args["source"])
    dst = resolve(args["destination"], must_exist=False)
    final = dst / src.name if dst.is_dir() else dst
    if final.exists():
        return ToolResult(False, f"{final} already exists.")
    shutil.move(str(src), str(final))
    return ToolResult(True, f"Moved {src} to {final}.", data={"path": str(final)})


register(Tool("move_path", "Move or rename a file or folder (never overwrites).",
              {"source": {"type": "string"}, "destination": {"type": "string"}}, ["source", "destination"],
              _move, lambda a: f"Move {a.get('source')} to {a.get('destination')}", _move_risk))


# ---------------------------------------------------------------- live search (unindexed folders)
def _search(args: dict[str, Any]) -> ToolResult:
    root = resolve(args["folder"])
    pattern = (args.get("pattern") or "*").strip()
    needle = (args.get("contains") or "").lower()
    hits, scanned = [], 0
    skip = {"node_modules", ".git", ".venv", "venv", "__pycache__", "dist", "build"}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in skip and not d.startswith(".")]
        for fn in filenames:
            scanned += 1
            if scanned > 20_000:
                break
            p = Path(dirpath) / fn
            if not p.match(pattern):
                continue
            if needle:
                if p.suffix.lower() not in documents.TEXT_EXTS or p.stat().st_size > 2_000_000:
                    continue
                try:
                    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
                except OSError:
                    continue
                found = [(i + 1, line.strip()) for i, line in enumerate(lines) if needle in line.lower()]
                if not found:
                    continue
                hits.append(f"{p}\n" + "\n".join(f"    {n}: {t[:160]}" for n, t in found[:3]))
            else:
                hits.append(str(p))
            if len(hits) >= 50:
                break
        if len(hits) >= 50 or scanned > 20_000:
            break
    what = f"{pattern}" + (f" containing \"{args.get('contains')}\"" if needle else "")
    if not hits:
        return ToolResult(True, f"No {what} in {root}.", data={"count": 0})
    return ToolResult(True, f"Found {len(hits)}{'+' if len(hits) >= 50 else ''} {what} in {root}.",
                      "\n".join(hits), {"count": len(hits)})


register(Tool("search_folder", "Search a folder on disk (not just indexed ones) by file pattern and optional text.",
              {"folder": {"type": "string"}, "pattern": {"type": "string", "description": "e.g. *.py"},
               "contains": {"type": "string", "description": "Text the file must contain"}},
              ["folder"], _search,
              lambda a: f"Search {a.get('folder')} for {a.get('pattern') or 'files'}" +
              (f" containing \"{a['contains']}\"" if a.get("contains") else ""), safe))
