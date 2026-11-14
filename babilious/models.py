"""Model management: what is installed, which one is active, how fast it is."""
from __future__ import annotations

import logging
import re
from typing import Any

from . import activity, db
from .integrations.vision import is_vision
from .llm import CATALOG, OllamaClient, OllamaError, OllamaUnavailable, catalog_entry, estimate_ram_gb

log = logging.getLogger(__name__)

BENCH_PROMPT = [
    {"role": "system", "content": "You are a concise assistant."},
    {"role": "user", "content": "In two short sentences, explain what a CPU cache is."},
]


def _same(a: str, b: str) -> bool:
    norm = lambda n: n if ":" in n else f"{n}:latest"  # noqa: E731
    return norm(a) == norm(b)


def is_embedding(name: str) -> bool:
    """Embedding models (for semantic search) cannot chat."""
    return "embed" in name or name.startswith(("bge-", "all-minilm", "mxbai"))


def vision_only(name: str) -> bool:
    """Small image models (moondream, llava) describe pictures but make poor chat partners."""
    return name.startswith(("moondream", "llava", "bakllava"))


def active_model() -> str:
    return str(db.get_setting("active_model"))


def overview(client: OllamaClient) -> dict[str, Any]:
    """Installed + recommended models, with load state and last benchmark."""
    available = client.is_available()
    installed = client.list_models() if available else []
    running = {m["name"] for m in client.running_models()} if available else set()
    bench = latest_benchmarks()
    active = active_model()
    items = []
    for m in installed:
        entry = catalog_entry(m["name"]) or {}
        items.append({
            **m,
            "installed": True,
            "active": _same(m["name"], active),
            "embedding": is_embedding(m["name"]),
            "vision": is_vision(m["name"]),
            "loaded": any(_same(m["name"], r) for r in running),
            "label": entry.get("label", ""),
            "notes": entry.get("notes", ""),
            "ram_gb": entry.get("ram_gb") or estimate_ram_gb(m["size"]),
            "benchmark": bench.get(m["name"]),
        })
    installed_names = [m["name"] for m in installed]
    suggestions = [
        {**c, "installed": False}
        for c in CATALOG if not any(_same(c["name"], n) for n in installed_names)
    ]
    return {
        "ollama": available,
        "active": active,
        "active_installed": any(_same(n, active) for n in installed_names),
        "installed": items,
        "suggestions": suggestions,
    }


def set_active(client: OllamaClient, name: str) -> str:
    """Make ``name`` the active model. Returns the exact installed name."""
    installed = [m["name"] for m in client.list_models()]
    match = next((n for n in installed if _same(n, name)), None)
    if not match:
        raise ValueError(f"Model '{name}' is not installed.")
    if is_embedding(match):
        raise ValueError(f"{match} is an embedding model for semantic search; it can't chat.")
    previous = active_model()
    db.set_setting("active_model", match)
    if not _same(previous, match):
        # Free the old model's RAM straight away instead of waiting for keep_alive.
        try:
            client.unload_model(previous)
        except OllamaUnavailable:
            pass
    activity.record("model", f"Switched model to {match}")
    return match


def resolve_request(client: OllamaClient, text: str) -> tuple[str | None, str]:
    """Pick an installed model from a phrase such as 'the faster model'.

    Returns (model_name or None, reason).
    """
    installed = client.list_models()
    if not installed:
        return None, "no models installed"
    text = text.lower()
    by_size = sorted(installed, key=lambda m: m["size"])
    active = active_model()

    # Exact or partial name mentioned ("switch to qwen", "use llama3.2").
    words = set(re.findall(r"[a-z0-9.:-]+", text))
    for m in installed:
        base = m["name"].split(":")[0]
        if m["name"] in text or base in words:
            return m["name"], "named"
    for m in installed:
        family = re.match(r"[a-z]+", m["name"]).group(0)
        if family in words and len(family) > 2:
            return m["name"], "named"
    if re.search(r"\b(code|coder|coding|programming)\b", text):
        coder = next((m for m in installed if "coder" in m["name"]), None)
        if coder:
            return coder["name"], "coding"
        return None, "no coding model installed"
    # General-purpose models only: a coder model is not "the faster model".
    general = [m for m in by_size if "coder" not in m["name"] and not is_embedding(m["name"]) and not vision_only(m["name"])
               and not _same(m["name"], active)]
    current = next((m for m in installed if _same(m["name"], active)), None)
    if re.search(r"\b(fast|faster|fastest|light|lighter|lightweight|small|smaller|quick|quicker)\b", text):
        # Must be meaningfully smaller (>15%) to actually feel faster.
        smaller = [m for m in general if not current or m["size"] < current["size"] * 0.85]
        if smaller:
            return smaller[0]["name"], "faster"
        return None, "already on the lightest installed model"
    if re.search(r"\b(smart|smarter|smartest|bigger|larger|better|stronger|default|balanced)\b", text):
        bigger = [m for m in reversed(general) if not current or m["size"] > current["size"] * 1.15]
        if bigger:
            return bigger[0]["name"], "stronger"
        return None, "already on the strongest installed model"
    return None, "unclear"


def benchmark(client: OllamaClient, name: str) -> dict[str, Any]:
    """Run a tiny prompt and store speed figures."""
    result = client.chat(name, BENCH_PROMPT,
                         options={"num_ctx": 2048, "num_predict": 60, "temperature": 0},
                         keep_alive=str(db.get_setting("keep_alive")))
    stats = result["stats"]
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO benchmarks(model, ts, load_ms, first_token_ms, total_ms, tokens_per_sec, "
            "prompt_tokens_per_sec, output_tokens) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (name, db.now(), stats.get("load_ms"), stats.get("first_token_ms"), stats.get("total_ms"),
             stats.get("tokens_per_sec"), stats.get("prompt_tokens_per_sec"), stats.get("output_tokens")),
        )
    activity.record("model", f"Benchmarked {name}: {stats.get('tokens_per_sec')} tokens/s")
    return {"model": name, **stats, "sample": result["text"].strip()}


def latest_benchmarks() -> dict[str, dict[str, Any]]:
    with db.connect() as conn:
        found = db.rows(conn.execute(
            "SELECT b.* FROM benchmarks b JOIN (SELECT model, MAX(id) AS id FROM benchmarks "
            "GROUP BY model) last ON last.id = b.id"
        ))
    return {b["model"]: b for b in found}


__all__ = ["overview", "set_active", "resolve_request", "benchmark", "active_model",
           "OllamaError", "OllamaUnavailable"]
