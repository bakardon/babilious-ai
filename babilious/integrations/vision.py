"""Optional local vision: describe your screen or an image with a vision model in Ollama.

Off until you pick a vision model (Settings > Models). Screenshots are taken only when you
ask, kept in data/screenshots, and sent only to Ollama on this PC. OCR (read_screen) stays
the default for reading text and errors, because it's faster and more exact on 8 GB of RAM.
"""
from __future__ import annotations

import base64
import io
import re
import time
from pathlib import Path
from typing import Any

from .. import db
from ..llm import OllamaClient, OllamaError, OllamaUnavailable
from ..tools import Tool, ToolResult, register, safe
from ..tools import places

VISION_NAME = re.compile(r"llava|moondream|bakllava|minicpm-v|-vl\b|vl:|vision|qwen2\.5vl|qwen3-vl|gemma3:(?!1b)|granite3\.2-vision", re.I)
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp"}


def is_vision(name: str) -> bool:
    return bool(VISION_NAME.search(name))


def active_vision_model() -> str:
    return str(db.get_setting("vision_model", "") or "")


def _encode(path: Path, max_side: int = 1024) -> str:
    from PIL import Image
    img = Image.open(path).convert("RGB")
    img.thumbnail((max_side, max_side), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode()


def ask(path: Path, question: str, client: OllamaClient | None = None) -> tuple[str, float]:
    model = active_vision_model()
    if not model:
        raise ValueError("No vision model is set up. In Settings > Models, download one (moondream is the light "
                         "option, 1.7 GB) and choose it as the vision model. Until then I read text with OCR.")
    client = client or OllamaClient()
    q = question.strip() or "Describe what is on this screen: the main windows, what the user is doing, and anything notable."
    started = time.perf_counter()
    try:
        # A short keep_alive frees the RAM for the chat model again soon after.
        res = client.chat(model, [{"role": "user", "content": q, "images": [_encode(path)]}],
                          options={"temperature": 0.2, "num_predict": 350}, keep_alive="1m")
    except OllamaUnavailable as exc:
        raise ValueError("Ollama isn't running, so I can't use the vision model.") from exc
    except OllamaError as exc:
        if "not found" in str(exc).lower():
            raise ValueError(f"The vision model '{model}' isn't installed. Download it in Settings > Models.") from exc
        raise ValueError(f"The vision model failed: {exc}") from exc
    return res["text"].strip(), time.perf_counter() - started


def _describe_screen(args: dict[str, Any]) -> ToolResult:
    from ..tools.screen import capture
    if not active_vision_model():
        return ToolResult(
            False, "No vision model is set up yet. Download moondream (1.7 GB) in Settings > Models and choose it as "
                   "the vision model. Meanwhile, \"what's on my screen\" reads the text with OCR.")
    p = capture(float(args.get("delay") or 0))
    answer, secs = ask(p, str(args.get("question") or ""))
    return ToolResult(True, f"Looked at your screen with {active_vision_model()} ({secs:.0f}s).", answer,
                      {"screenshot": p.name, "vision": True})


register(Tool("describe_screen", "Screenshot + describe it with the local vision model (layout, images, what's going on).",
              {"question": {"type": "string"}, "delay": {"type": "number"}}, [], _describe_screen,
              lambda a: "Look at your screen with the vision model" + (f" in {a['delay']:.0f}s" if a.get("delay") else ""),
              safe))


def _describe_image(args: dict[str, Any]) -> ToolResult:
    p = places.resolve(str(args.get("path", "")))
    if p.suffix.lower() not in IMAGE_EXT:
        return ToolResult(False, f"{p.name} isn't an image I can look at (png, jpg, bmp, gif, webp).")
    if not active_vision_model():
        return ToolResult(False, "No vision model is set up yet. Download moondream (1.7 GB) in Settings > Models "
                                 "and choose it as the vision model.")
    answer, secs = ask(p, str(args.get("question") or "Describe this image."))
    return ToolResult(True, f"Looked at {p.name} with {active_vision_model()} ({secs:.0f}s).", answer,
                      {"path": str(p), "vision": True})


register(Tool("describe_image", "Describe an image file with the local vision model.",
              {"path": {"type": "string"}, "question": {"type": "string"}}, ["path"], _describe_image,
              lambda a: f"Look at the image {a.get('path', '')}", safe))
