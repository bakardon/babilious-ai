"""Clipboard: read when you ask, write what you ask."""
from __future__ import annotations

from typing import Any

from . import Tool, ToolResult, register, safe


def _read(_args: dict[str, Any]) -> ToolResult:
    import pyperclip
    text = pyperclip.paste() or ""
    if not text.strip():
        return ToolResult(False, "Your clipboard is empty (or holds an image, which I can't read yet).")
    clipped = text[:15_000]
    return ToolResult(True, f"Read your clipboard ({len(text):,} characters).", clipped, {"chars": len(text)})


def _write(args: dict[str, Any]) -> ToolResult:
    import pyperclip
    text = args.get("text") or ""
    if not text:
        return ToolResult(False, "Nothing to copy.")
    pyperclip.copy(text)
    return ToolResult(True, f"Copied {len(text):,} characters to your clipboard.")


register(Tool("read_clipboard", "Read the text currently on the clipboard.", {}, [], _read,
              lambda a: "Read your clipboard", safe, followup=True))
register(Tool("write_clipboard", "Put text on the clipboard.", {"text": {"type": "string"}}, ["text"], _write,
              lambda a: f"Copy {len(a.get('text') or '')} characters to your clipboard", safe))
