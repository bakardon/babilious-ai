"""Tools: everything Babilious can do on this computer, with explicit risk levels.

Risk levels
- safe:    harmless and reversible (open an app, list a folder, take a screenshot you asked for).
           Runs immediately when *you* ask with a clear command.
- confirm: changes something or runs code (run tests, move a file, click in a web page).
           Shown as a card; nothing happens until you press Approve.
- danger:  hard to undo (delete to the Recycle Bin, overwrite, git push, installs).
           Same card, with a red warning.
- blocked: never runs (formatting drives, wiping disks...).

Anything the language model proposes on its own is at least "confirm",
because a small model can misread a request.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

LEVELS = ["safe", "confirm", "danger", "blocked"]


@dataclass
class Risk:
    level: str
    reason: str = ""

    def at_least(self, level: str) -> "Risk":
        if LEVELS.index(self.level) < LEVELS.index(level):
            return Risk(level, self.reason or "Proposed by the AI model, so it needs your OK.")
        return self


@dataclass
class ToolResult:
    ok: bool
    summary: str                  # one honest line: what happened
    output: str = ""              # longer text: command output, file contents, OCR text
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class Tool:
    name: str
    description: str
    params: dict[str, dict[str, Any]]       # JSON-schema properties
    required: list[str]
    run: Callable[[dict[str, Any]], ToolResult]
    preview: Callable[[dict[str, Any]], str]
    risk: Callable[[dict[str, Any]], Risk]
    followup: bool = False   # the result is raw material for an answer (OCR, file text, page text)
    model_visible: bool = True

    def schema(self) -> dict[str, Any]:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object", "properties": self.params, "required": self.required}}}


REGISTRY: dict[str, Tool] = {}


def register(tool: Tool) -> Tool:
    REGISTRY[tool.name] = tool
    return tool


def get(name: str) -> Tool:
    if name not in REGISTRY:
        raise ValueError(f"Unknown tool: {name}")
    return REGISTRY[name]


def safe(_args: dict[str, Any]) -> Risk:
    return Risk("safe")


def confirm(reason: str) -> Callable[[dict[str, Any]], Risk]:
    return lambda _args: Risk("confirm", reason)


def load_all() -> dict[str, Tool]:
    """Import the tool modules (each registers itself)."""
    from . import apps, browser, clipboard, files, screen, terminal, wintools  # noqa: F401
    from ..manager import planner  # noqa: F401  (create_tasks)
    from ..integrations import github, vision  # noqa: F401  (git/GitHub, optional vision model)
    return REGISTRY
