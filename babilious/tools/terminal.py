"""Terminal commands (PowerShell) with an allowlist, plus "run the tests for my project"."""
from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any

from . import Risk, Tool, ToolResult, register
from .places import default_folder, find_project, resolve

MAX_OUTPUT = 12_000

# Read-only commands that run without asking (only when there's no chaining/redirection).
SAFE = [
    r"git (status|log|diff|branch|show|remote( -v)?|rev-parse|describe|tag( -l)?|stash list)\b(?!.*(--output|>))",
    r"(dir|ls|gci|Get-ChildItem|pwd|Get-Location|whoami|hostname|ipconfig|tree)\b",
    r"(python|py|node|npm|git|pip|ollama|java|gcc|code) (--version|-V|version)$",
    r"(pip|python -m pip) (list|show|freeze)\b", r"ollama (list|ps)\b", r"npm (ls|list|outdated)\b",
    r"(Get-Process|tasklist|Get-Date|date|systeminfo|Get-ComputerInfo)\b",
    r"(type|cat|Get-Content|gc) [^|;&<>]+$", r"(where|where\.exe|Get-Command) \S+$", r"echo [^|;&<>`$]*$",
]
# Never run, whatever the confirmation.
BLOCKED = [
    (r"\bformat(\.com)?\s+[a-z]:", "formats a drive"), (r"\bdiskpart\b", "edits disk partitions"),
    (r"\bbcdedit\b", "changes the boot configuration"), (r"\bvssadmin\s+delete\b", "deletes system restore points"),
    (r"\bcipher\s+/w\b", "wipes free disk space"), (r"\b(rd|rmdir|del|erase)\b.*\s[a-z]:\\?\s*$", "deletes a whole drive"),
    (r"remove-item\b.*\s[a-z]:\\?\s*(-|$)", "deletes a whole drive"), (r"\brm\s+-rf\s+/(\s|$)", "deletes everything"),
    (r"\breg(\.exe)?\s+delete\s+hklm", "deletes system registry keys"), (r"\bmkfs\b|\bdd\s+if=", "overwrites disks"),
]
# Run only with a red warning.
DANGER = [
    (r"\b(rm|del|erase|rd|rmdir|remove-item|ri)\b", "deletes files"),
    (r"\b(move-item|mv|move|rename-item|ren)\b", "moves or renames files"),
    (r"\bgit\s+(push|reset\s+--hard|clean|rebase|merge|commit|checkout\s+--|restore|stash\s+(drop|clear)|branch\s+-D)",
     "changes your git history or remote"),
    (r"\b(pip|npm|yarn|pnpm|winget|choco|scoop|msiexec)\b.*\b(install|uninstall|i|add|remove|update|upgrade)\b",
     "installs or removes software"),
    (r"\b(shutdown|restart-computer|stop-computer|logoff)\b", "shuts down or restarts the PC"),
    (r"\b(taskkill|stop-process|kill)\b", "kills running programs"),
    (r"\b(set-executionpolicy|reg(\.exe)?\s+add|set-itemproperty|netsh|sc(\.exe)?\s|bcd|new-service)\b",
     "changes system settings"),
    (r"\b(invoke-webrequest|iwr|curl|wget|invoke-restmethod|irm)\b", "downloads from the internet"),
    (r"(^|[^2])>(?!&)|\bout-file\b|\bset-content\b|\badd-content\b", "writes to a file"),
    (r"\biex\b|\binvoke-expression\b|\bstart-process\b", "runs other programs"),
]
CHAINING = re.compile(r"[;&|`]|\$\(|>|<")


def classify(command: str) -> Risk:
    c = command.strip()
    low = c.lower()
    for pat, why in BLOCKED:
        if re.search(pat, low):
            return Risk("blocked", f"It {why}.")
    for pat, why in DANGER:
        if re.search(pat, low):
            return Risk("danger", f"This command {why}.")
    if not CHAINING.search(c) and any(re.match(p + r"", c, re.I) for p in SAFE):
        return Risk("safe")
    return Risk("confirm", "Runs a command on your PC.")


def _cwd(args: dict[str, Any]) -> Path:
    cwd = str(args.get("cwd") or "").strip()
    if cwd and cwd.lower() not in ("none", "null", "default", "."):
        return resolve(cwd)
    return default_folder()


def run_powershell(command: str, cwd: Path, timeout: int = 120) -> tuple[int, str, float]:
    started = time.perf_counter()
    # Pass the real exit code through: native programs set $LASTEXITCODE, cmdlets only set $?.
    script = ("[Console]::OutputEncoding = [Text.Encoding]::UTF8; $ProgressPreference = 'SilentlyContinue'; "
              f"$global:LASTEXITCODE = 0; {command}\n"
              "$__ok = $?; if ($LASTEXITCODE) { exit $LASTEXITCODE } elseif (-not $__ok) { exit 1 }")
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "NO_COLOR": "1", "PY_COLORS": "0"}
    try:
        proc = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                              cwd=str(cwd), capture_output=True, timeout=timeout, env=env,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired as exc:
        out = (exc.stdout or b"").decode("utf-8", "replace") + (exc.stderr or b"").decode("utf-8", "replace")
        return -1, out + f"\n[Stopped after {timeout}s]", time.perf_counter() - started
    out = proc.stdout.decode("utf-8", "replace")
    err = proc.stderr.decode("utf-8", "replace")
    text = (out + ("\n" + err if err.strip() else "")).strip()
    return proc.returncode, text, time.perf_counter() - started


def _clip(text: str) -> str:
    return text if len(text) <= MAX_OUTPUT else "[...earlier output cut...]\n" + text[-MAX_OUTPUT:]


def _run(args: dict[str, Any]) -> ToolResult:
    cwd = _cwd(args)
    if not cwd.is_dir():
        return ToolResult(False, f"Folder not found: {cwd}")
    code, out, secs = run_powershell(args["command"], cwd, int(args.get("timeout") or 120))
    ok = code == 0
    summary = (f"Ran `{args['command']}` in {cwd.name} ({secs:.1f}s)." if ok
               else f"`{args['command']}` exited with code {code} after {secs:.1f}s.")
    return ToolResult(ok, summary, _clip(out), {"exit_code": code, "cwd": str(cwd)})


register(Tool("run_command", "Run a PowerShell command. Read-only commands run directly; anything else asks first.",
              {"command": {"type": "string"},
               "cwd": {"type": "string", "description": "Folder to run in (a path, project name or Documents)"}},
              ["command"], _run,
              lambda a: f"Run `{a.get('command')}`" + (f" in {a['cwd']}" if a.get("cwd") else ""),
              lambda a: classify(a.get("command", ""))))


# ---------------------------------------------------------------- tests
def detect_test_command(project: Path) -> str | None:
    venv_py = next((p for p in (project / ".venv" / "Scripts" / "python.exe", project / "venv" / "Scripts" / "python.exe")
                    if p.exists()), None)
    has_pytest = (project / "pytest.ini").exists() or (project / "tests").is_dir() or \
        ("[tool.pytest" in (project / "pyproject.toml").read_text(encoding="utf-8", errors="ignore")
         if (project / "pyproject.toml").exists() else False)
    if has_pytest:
        py = f"& '{venv_py}'" if venv_py else "python"
        return f"{py} -m pytest -q --no-header -p no:cacheprovider"
    pkg = project / "package.json"
    if pkg.exists() and '"test"' in pkg.read_text(encoding="utf-8", errors="ignore"):
        return "npm test --silent"
    if any(project.glob("test_*.py")):
        return "python -m unittest discover -q"
    return None


def _test_target(args: dict[str, Any]) -> tuple[Path | None, str | None]:
    name = args.get("project") or "babilious"
    proj = find_project(name) if not Path(name).is_absolute() else Path(name)
    if not proj or not proj.is_dir():
        return None, None
    return proj, detect_test_command(proj)


def _tests_preview(args: dict[str, Any]) -> str:
    proj, cmd = _test_target(args)
    if not proj:
        return f"Run the tests for {args.get('project') or 'Babilious'}"
    return f"Run the tests in {proj}: `{cmd or '?'}`"


def _tests_risk(args: dict[str, Any]) -> Risk:
    proj, cmd = _test_target(args)
    if not proj:
        return Risk("blocked", f"I couldn't find a project called \"{args.get('project')}\". "
                               "Add it under Automation > Projects.")
    if not cmd:
        return Risk("blocked", f"I can't tell how to run the tests in {proj} (no pytest, npm test or test_*.py).")
    return Risk("confirm", "Running tests executes the project's code.")


def _tests(args: dict[str, Any]) -> ToolResult:
    proj, cmd = _test_target(args)
    if not proj or not cmd:
        return ToolResult(False, "No test setup found.")
    code, out, secs = run_powershell(cmd, proj, timeout=600)
    tail = out.strip().splitlines()[-1] if out.strip() else ""
    if code == 0:
        return ToolResult(True, f"Tests passed in {proj.name} ({secs:.0f}s): {tail}", _clip(out),
                          {"exit_code": 0, "cwd": str(proj)})
    return ToolResult(False, f"Tests failed in {proj.name} ({secs:.0f}s): {tail}", _clip(out),
                      {"exit_code": code, "cwd": str(proj)})


register(Tool("run_tests", "Run a project's automated tests (pytest, npm test or unittest).",
              {"project": {"type": "string", "description": "Project name or path; default Babilious"}},
              [], _tests, _tests_preview, _tests_risk))
