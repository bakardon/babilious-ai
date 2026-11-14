"""Git & GitHub help for your projects.

Reading (status, recent commits, changes, open issues) runs straight away. Committing needs
your approval; pushing is marked dangerous and always needs a click. Nothing is ever pushed
or published on its own.

Issues come from the GitHub CLI (`gh`) when it's installed and signed in, otherwise from
GitHub's public API (public repositories only, no token stored).
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import requests

from ..tools import Risk, Tool, ToolResult, register, safe
from ..tools import places

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _repo(project: str) -> Path:
    name = (project or "babilious").strip()
    p = places.find_project(name)
    if p is None:
        try:
            p = places.resolve(name)
        except (ValueError, FileNotFoundError):
            p = None
    if p is None or not p.is_dir():
        raise ValueError(f"I can't find a project called \"{name}\". Add it in Automation > Projects.")
    out = _git(p, "rev-parse", "--show-toplevel", check=False)
    if out[0] != 0:
        raise ValueError(f"{p} isn't a git repository.")
    return Path(out[1].strip())


def _git(cwd: Path, *args: str, check: bool = True, timeout: int = 30) -> tuple[int, str, str]:
    if not shutil.which("git"):
        raise ValueError("Git isn't installed (https://git-scm.com).")
    r = subprocess.run(["git", "-c", "core.quotepath=off", *args], cwd=cwd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout, creationflags=NO_WINDOW)
    if check and r.returncode != 0:
        raise ValueError(f"git {args[0]} failed: {(r.stderr or r.stdout).strip()[:300]}")
    return r.returncode, r.stdout, r.stderr


def remote_repo(cwd: Path) -> str | None:
    """'owner/name' of the GitHub remote, if there is one."""
    code, out, _ = _git(cwd, "remote", "get-url", "origin", check=False)
    m = re.search(r"github\.com[:/](?P<r>[\w.-]+/[\w.-]+?)(?:\.git)?/?$", out.strip()) if code == 0 else None
    return m.group("r") if m else None


def status_data(cwd: Path) -> dict[str, Any]:
    _, branch_line, _ = _git(cwd, "status", "--porcelain=v1", "--branch")
    lines = branch_line.splitlines()
    head = lines[0][3:] if lines and lines[0].startswith("## ") else ""
    m = re.match(r"(?P<b>[^.\s]+)(?:\.\.\.(?P<up>\S+))?(?: \[(?P<ab>[^\]]+)\])?", head)
    ahead = behind = 0
    if m and m.group("ab"):
        a = re.search(r"ahead (\d+)", m.group("ab"))
        b = re.search(r"behind (\d+)", m.group("ab"))
        ahead, behind = int(a.group(1)) if a else 0, int(b.group(1)) if b else 0
    changes = [{"code": ln[:2], "path": ln[3:]} for ln in lines[1:] if ln.strip()]
    _, log, _ = _git(cwd, "log", "-5", "--pretty=format:%h %ar: %s", check=False)
    return {"branch": m.group("b") if m else head, "upstream": (m.group("up") if m else "") or "",
            "ahead": ahead, "behind": behind, "changes": changes, "recent": log.splitlines(),
            "remote": remote_repo(cwd) or "", "path": str(cwd)}


def _describe_changes(changes: list[dict[str, str]]) -> str:
    kinds = {"M": "modified", "A": "added", "D": "deleted", "R": "renamed", "?": "new (untracked)"}
    counts: dict[str, int] = {}
    for c in changes:
        k = kinds.get((c["code"].strip() or "M")[0], "changed")
        counts[k] = counts.get(k, 0) + 1
    return ", ".join(f"{n} {k}" for k, n in counts.items())


# ---------------------------------------------------------------- tools
def _status(args: dict[str, Any]) -> ToolResult:
    cwd = _repo(args.get("project", ""))
    d = status_data(cwd)
    sync = ""
    if d["upstream"]:
        sync = (" Up to date with " + d["upstream"] + "." if not (d["ahead"] or d["behind"]) else
                f" {d['ahead']} commit(s) not pushed, {d['behind']} to pull from {d['upstream']}.")
    else:
        sync = " No upstream branch (never pushed)."
    summary = (f"{cwd.name} is on branch {d['branch']}: "
               + (f"{len(d['changes'])} changed files ({_describe_changes(d['changes'])})." if d["changes"] else "no uncommitted changes.")
               + sync)
    out = "\n".join(f"{c['code'].strip():<2} {c['path']}" for c in d["changes"][:40])
    if len(d["changes"]) > 40:
        out += f"\n... and {len(d['changes']) - 40} more"
    if d["recent"]:
        out += ("\n\n" if out else "") + "Recent commits:\n" + "\n".join(d["recent"])
    return ToolResult(True, summary, out, d)


register(Tool("git_status", "Show a project's git status: branch, changed files, unpushed commits, recent commits.",
              {"project": {"type": "string", "description": "Project name, e.g. babilious"}}, [], _status,
              lambda a: f"Check git status of {a.get('project') or 'babilious'}", safe))


def _diff(args: dict[str, Any]) -> ToolResult:
    cwd = _repo(args.get("project", ""))
    d = status_data(cwd)
    if not d["changes"]:
        return ToolResult(False, f"{cwd.name} has no uncommitted changes to describe.")
    _, stat, _ = _git(cwd, "diff", "HEAD", "--stat", check=False)
    _, diff, _ = _git(cwd, "diff", "HEAD", "--unified=1", check=False)
    untracked = [c["path"] for c in d["changes"] if c["code"] == "??"]
    text = f"Changed files:\n{stat.strip()}\n"
    if untracked:
        text += "New files: " + ", ".join(untracked[:30]) + "\n"
    # The model sees ~6000 characters, so every file gets a share instead of the first file taking it all.
    files = [f for f in re.split(r"(?m)^(?=diff --git )", diff) if f.strip()]
    if files:
        share = max(250, (5200 - len(text)) // len(files))
        parts = []
        for f in files:
            body = re.sub(r"(?m)^(?:index |--- |\+\+\+ |new file mode|deleted file mode).*\n", "", f)
            parts.append(body[:share].rstrip() + ("\n  ..." if len(body) > share else ""))
        text += "\nDiff (each file trimmed):\n" + "\n".join(parts)
    return ToolResult(True, f"Read the changes in {cwd.name} ({len(d['changes'])} files).", text,
                      {"name": f"{cwd.name} changes", "path": str(cwd)})


register(Tool("git_diff", "Read a project's uncommitted changes (to explain them or draft a commit message).",
              {"project": {"type": "string"}}, [], _diff,
              lambda a: f"Read uncommitted changes in {a.get('project') or 'babilious'}", safe, followup=True))


def _commit_risk(args: dict[str, Any]) -> Risk:
    msg = str(args.get("message", "")).strip()
    if not msg:
        return Risk("blocked", "A commit needs a message.")
    try:
        cwd = _repo(args.get("project", ""))
        n = len(status_data(cwd)["changes"])
    except ValueError as exc:
        return Risk("blocked", str(exc))
    if not n:
        return Risk("blocked", "There's nothing to commit.")
    return Risk("confirm", f"Stages all {n} changed files in {cwd.name} and commits them locally. Nothing is pushed.")


def _commit(args: dict[str, Any]) -> ToolResult:
    cwd = _repo(args.get("project", ""))
    _git(cwd, "add", "-A")
    _, out, _ = _git(cwd, "commit", "-m", str(args["message"]).strip())
    first = out.strip().splitlines()[0] if out.strip() else "Committed."
    return ToolResult(True, f"Committed in {cwd.name}: {first}. Not pushed.", out)


register(Tool("git_commit", "Commit all changes in a project locally (needs approval; never pushes).",
              {"project": {"type": "string"}, "message": {"type": "string"}}, ["message"], _commit,
              lambda a: f"Commit all changes in {a.get('project') or 'babilious'}: \"{str(a.get('message', ''))[:80]}\"",
              _commit_risk, model_visible=False))


def _push_risk(args: dict[str, Any]) -> Risk:
    try:
        cwd = _repo(args.get("project", ""))
        d = status_data(cwd)
    except ValueError as exc:
        return Risk("blocked", str(exc))
    where = d["upstream"] or f"origin/{d['branch']} (new branch on the remote)"
    return Risk("danger", f"Publishes {d['ahead'] or 'the'} commit(s) on {d['branch']} to {where}. Others can see it.")


def _push(args: dict[str, Any]) -> ToolResult:
    cwd = _repo(args.get("project", ""))
    d = status_data(cwd)
    extra = [] if d["upstream"] else ["-u", "origin", d["branch"]]
    code, out, err = _git(cwd, "push", *extra, check=False, timeout=120)
    if code != 0:
        return ToolResult(False, "The push failed.", (err or out).strip())
    return ToolResult(True, f"Pushed {d['branch']} of {cwd.name}.", (err or out).strip())


register(Tool("git_push", "Push a project's commits to GitHub (dangerous: always needs a click).",
              {"project": {"type": "string"}}, [], _push,
              lambda a: f"Push {a.get('project') or 'babilious'} to its remote", _push_risk, model_visible=False))


def _issues(args: dict[str, Any]) -> ToolResult:
    cwd = _repo(args.get("project", ""))
    repo = remote_repo(cwd)
    if not repo:
        return ToolResult(False, f"{cwd.name} has no GitHub remote, so there are no issues to show.")
    items: list[dict[str, Any]] = []
    via = ""
    if shutil.which("gh"):
        r = subprocess.run(["gh", "issue", "list", "--repo", repo, "--limit", "15", "--json",
                            "number,title,labels,updatedAt,url"], capture_output=True, text=True, timeout=30,
                           creationflags=NO_WINDOW)
        if r.returncode == 0:
            items = [{"number": i["number"], "title": i["title"], "url": i["url"],
                      "labels": [lb["name"] for lb in i.get("labels", [])]} for i in json.loads(r.stdout or "[]")]
            via = "gh"
    if not via:
        try:
            r2 = requests.get(f"https://api.github.com/repos/{repo}/issues", params={"state": "open", "per_page": 15},
                              headers={"Accept": "application/vnd.github+json"}, timeout=15)
        except requests.ConnectionError:
            return ToolResult(False, "I can't reach GitHub (no internet?).")
        if r2.status_code == 404:
            return ToolResult(False, f"GitHub says {repo} doesn't exist or is private. Sign in with `gh auth login` "
                                     "so I can see private repos.")
        if r2.status_code >= 400:
            return ToolResult(False, f"GitHub error {r2.status_code}.")
        items = [{"number": i["number"], "title": i["title"], "url": i["html_url"],
                  "labels": [lb["name"] for lb in i.get("labels", [])]}
                 for i in r2.json() if "pull_request" not in i]
        via = "api"
    if not items:
        return ToolResult(True, f"{repo} has no open issues.", data={"repo": repo, "issues": []})
    out = "\n".join(f"#{i['number']} {i['title']}" + (f"  [{', '.join(i['labels'])}]" if i["labels"] else "")
                    for i in items)
    return ToolResult(True, f"{repo} has {len(items)} open issue(s).", out, {"repo": repo, "issues": items})


register(Tool("github_issues", "List open GitHub issues for a project's repository.",
              {"project": {"type": "string"}}, [], _issues,
              lambda a: f"List GitHub issues for {a.get('project') or 'babilious'}", safe))
