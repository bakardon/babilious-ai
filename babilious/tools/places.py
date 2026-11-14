"""Turn words like "my Documents" or "my ParkSight project" into real paths."""
from __future__ import annotations

import os
import re
from pathlib import Path

from .. import config, db

SHELL_FOLDERS = {
    "desktop": "Desktop", "documents": "Personal", "docs": "Personal", "downloads": "{374DE290-123F-4565-9164-39C4925E467B}",
    "pictures": "My Pictures", "photos": "My Pictures", "music": "My Music", "videos": "My Video",
}


def known_folder(name: str) -> Path | None:
    """Real location of Desktop/Documents/... (handles OneDrive redirection)."""
    key = name.lower().strip().removeprefix("my ").removesuffix(" folder").strip()
    if key in ("home", "user", "profile"):
        return Path.home()
    reg = SHELL_FOLDERS.get(key)
    if not reg:
        return None
    if os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as k:
                value, _ = winreg.QueryValueEx(k, reg)
                p = Path(os.path.expandvars(value))
                if p.is_dir():
                    return p
        except OSError:
            pass
    fallback = Path.home() / {"documents": "Documents", "docs": "Documents", "photos": "Pictures"}.get(key, key.title())
    return fallback if fallback.is_dir() else None


def default_folder() -> Path:
    custom = str(db.get_setting("default_folder", "") or "")
    if custom and Path(custom).is_dir():
        return Path(custom)
    return known_folder("documents") or Path.home()


def projects() -> dict[str, str]:
    """Named projects: your saved shortcuts, Babilious itself, and indexed folders."""
    out: dict[str, str] = {"babilious": str(config.BASE_DIR)}
    from .. import indexer
    for f in indexer.list_folders():
        out.setdefault(Path(f["path"]).name.lower(), f["path"])
    for name, path in (db.get_setting("projects", {}) or {}).items():
        out[name.lower()] = path
    return out


def find_project(name: str) -> Path | None:
    """Saved/known projects first, then a shallow look in Desktop, Documents and home."""
    q = re.sub(r"\b(my|the|project|folder|repo|repository)\b", " ", name.lower())
    q = " ".join(q.split())
    if not q:
        return None
    projs = projects()
    for key in (q, q.replace(" ", ""), q.replace(" ", "-"), q.replace(" ", "_")):
        if key in projs and Path(projs[key]).is_dir():
            return Path(projs[key])
    squash = re.sub(r"[\s_-]", "", q)
    roots = [p for p in (known_folder("desktop"), known_folder("documents"), Path.home()) if p]
    for root in roots:
        try:
            for depth1 in root.iterdir():
                if not depth1.is_dir() or depth1.name.startswith("."):
                    continue
                if re.sub(r"[\s_-]", "", depth1.name.lower()) == squash:
                    # A folder that just wraps the real project (e.g. "x-main/x-main")
                    inner = depth1 / depth1.name
                    return inner if inner.is_dir() else depth1
                if depth1.name.lower().startswith(squash) and len(squash) >= 4:
                    return depth1
        except OSError:
            continue
    return None


def resolve(text: str, must_exist: bool = True, base: Path | None = None) -> Path:
    """Path from an absolute path, a known folder, a project, or a name inside the default folder."""
    t = text.strip().strip('"').strip("'").rstrip("?.!")
    if not t:
        raise ValueError("No path given.")
    p = Path(os.path.expandvars(os.path.expanduser(t)))
    if p.is_absolute():
        if not p.exists():
            # Models often write C:\Users\[username]\Desktop or C:\Users\YourName\Documents\x.
            parts = [x for x in p.parts[1:]]
            for i, part in enumerate(parts):
                kf = known_folder(part)
                if kf and (i > 0 and parts[0].lower() == "users"):
                    p = kf.joinpath(*parts[i + 1:])
                    break
        if must_exist and not p.exists():
            raise ValueError(f"Not found: {p}")
        return p
    kf = known_folder(t)
    if kf:
        return kf
    # "Documents\\notes" or "desktop/x"
    head, _, rest = t.replace("/", "\\").partition("\\")
    kf = known_folder(head)
    if kf and rest:
        p = kf / rest
        if must_exist and not p.exists():
            raise ValueError(f"Not found: {p}")
        return p
    proj = find_project(t)
    if proj:
        return proj
    p = (base or default_folder()) / t
    if must_exist and not p.exists():
        raise ValueError(f"I couldn't find \"{t}\". Give me the full path, e.g. C:\\Users\\you\\Documents\\{t}")
    return p


PROTECTED = [Path(os.environ.get("WINDIR", r"C:\Windows")), Path(r"C:\Program Files"), Path(r"C:\Program Files (x86)"),
             Path(r"C:\ProgramData")]


def write_guard(p: Path) -> str | None:
    """Reason a path must never be changed, or None."""
    rp = p.resolve()
    if rp.parent == rp:
        return "That's a whole drive."
    if rp == Path.home().resolve():
        return "That's your entire user folder."
    for bad in PROTECTED:
        if rp == bad or bad in rp.parents:
            return "System folders are off limits."
    own = [config.DATA_DIR.resolve(), (config.BASE_DIR / ".venv").resolve()]
    for o in own:
        if rp == o or o in rp.parents:
            return "That's Babilious' own data; manage it from the app instead."
    if any(part in str(rp).lower() + "\\" for part in FORBIDDEN_PARTS):
        return "AppData belongs to your programs."
    return None


FORBIDDEN_PARTS = ["\\appdata\\"]  # tests clear this: pytest's temp folders live under AppData
