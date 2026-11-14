"""Screen awareness, only when you ask: screenshot → local OCR (RapidOCR) → answer.

Screenshots are kept in data/screenshots (the 30 most recent) so you can see what
was captured, and are never sent anywhere. Nothing is ever captured in the background.
"""
from __future__ import annotations

import ctypes
import logging
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from .. import config
from . import Tool, ToolResult, register, safe

log = logging.getLogger(__name__)
SHOTS = config.DATA_DIR / "screenshots"
KEEP = 30
_ocr = None
_ocr_lock = threading.Lock()
_dpi_set = False


def _dpi_aware() -> None:
    global _dpi_set
    if os.name == "nt" and not _dpi_set:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)  # real pixels on scaled displays
        except Exception:  # noqa: BLE001
            pass
        _dpi_set = True


def capture(delay: float = 0) -> Path:
    from PIL import ImageGrab
    _dpi_aware()
    if delay:
        time.sleep(min(float(delay), 10))
    img = ImageGrab.grab(all_screens=True)
    SHOTS.mkdir(parents=True, exist_ok=True)
    path = SHOTS / f"screen-{datetime.now():%Y%m%d-%H%M%S-%f}.png"
    img.save(path, optimize=True)
    for old in sorted(SHOTS.glob("screen-*.png"))[:-KEEP]:
        old.unlink(missing_ok=True)
    return path


def ocr(path: Path) -> list[str]:
    """Text lines, top to bottom, left to right."""
    global _ocr
    import numpy as np
    from PIL import Image
    with _ocr_lock:
        if _ocr is None:
            from rapidocr_onnxruntime import RapidOCR
            _ocr = RapidOCR()
        img = Image.open(path).convert("RGB")
        if img.width < 1280:  # the text detector misses words on small images
            img = img.resize((1280, round(img.height * 1280 / img.width)), Image.LANCZOS)
        result, _ = _ocr(np.array(img))
    if not result:
        return []
    items = []
    for box, text, score in result:
        if score < 0.5 or not text.strip():
            continue
        ys = [p[1] for p in box]
        xs = [p[0] for p in box]
        items.append(((min(ys) + max(ys)) / 2, min(xs), max(ys) - min(ys), text.strip()))
    items.sort()
    lines: list[list[tuple[float, str]]] = []
    last_y, last_h = None, 0.0
    for y, x, h, text in items:   # merge boxes on the same visual line
        if last_y is not None and abs(y - last_y) < max(h, last_h) * 0.5:
            lines[-1].append((x, text))
        else:
            lines.append([(x, text)])
            last_y, last_h = y, h
    return ["  ".join(t for _, t in sorted(line)) for line in lines]


def _shot(args: dict[str, Any]) -> ToolResult:
    p = capture(float(args.get("delay") or 0))
    return ToolResult(True, f"Took a screenshot ({p.name}).", data={"screenshot": p.name})


register(Tool("take_screenshot", "Take a screenshot of the screen (only when the user asks).",
              {"delay": {"type": "number", "description": "Seconds to wait first, so the user can switch windows"}},
              [], _shot, lambda a: "Take a screenshot" + (f" in {a['delay']:.0f}s" if a.get("delay") else ""), safe))


def _read_screen(args: dict[str, Any]) -> ToolResult:
    started = time.perf_counter()
    p = capture(float(args.get("delay") or 0))
    lines = ocr(p)
    secs = time.perf_counter() - started
    if not lines:
        return ToolResult(True, f"Took a screenshot but found no readable text ({secs:.1f}s).", "",
                          {"screenshot": p.name, "lines": 0})
    text = "\n".join(lines)[:12_000]
    return ToolResult(True, f"Read {len(lines)} lines of text from your screen ({secs:.1f}s).", text,
                      {"screenshot": p.name, "lines": len(lines)})


register(Tool("read_screen", "Screenshot + read the text on screen, to explain errors, windows or web pages.",
              {"delay": {"type": "number"}, "question": {"type": "string"}}, [], _read_screen,
              lambda a: "Look at your screen" + (f" in {a['delay']:.0f}s" if a.get("delay") else ""), safe,
              followup=True))


def screenshot_path(name: str) -> Path | None:
    if not name.startswith("screen-") or not name.endswith(".png") or "/" in name or "\\" in name:
        return None
    p = SHOTS / name
    return p if p.is_file() else None
