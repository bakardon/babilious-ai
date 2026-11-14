"""Model layer: a thin client for the local Ollama server.

Nothing here knows about Babilious' personality; that lives in persona.py.
Any other local backend can be added later by implementing the same methods.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Iterator

import requests

from . import config

log = logging.getLogger(__name__)


class OllamaUnavailable(RuntimeError):
    """Ollama is not running or not reachable."""


class OllamaError(RuntimeError):
    """Ollama answered with an error (for example: model not found)."""


# Curated small models that fit an 8 GB, CPU-only laptop.
# ram_gb is an approximate peak with num_ctx=4096 (weights + KV cache + runtime).
CATALOG: list[dict[str, Any]] = [
    {"name": "llama3.2:1b", "label": "Fastest", "params": "1.2B", "download_gb": 1.3, "ram_gb": 2.0,
     "notes": "Very quick replies; weaker reasoning. Good for quick chat and commands."},
    {"name": "qwen2.5:1.5b", "label": "Fast", "params": "1.5B", "download_gb": 1.0, "ram_gb": 2.0,
     "notes": "Fast and surprisingly capable at instructions."},
    {"name": "gemma2:2b", "label": "Fast", "params": "2.6B", "download_gb": 1.6, "ram_gb": 3.0,
     "notes": "Good conversational tone for its size."},
    {"name": "llama3.2:latest", "label": "Balanced (default)", "params": "3.2B", "download_gb": 2.0, "ram_gb": 3.5,
     "notes": "Recommended default for this laptop: good personality, acceptable speed."},
    {"name": "qwen2.5:3b", "label": "Balanced", "params": "3.1B", "download_gb": 1.9, "ram_gb": 3.5,
     "notes": "Strong reasoning for its size; good at structured output."},
    {"name": "qwen2.5-coder:3b", "label": "Coding", "params": "3.1B", "download_gb": 1.9, "ram_gb": 3.5,
     "notes": "Best small model here for code questions; flatter personality."},
    {"name": "phi3.5:latest", "label": "Heavier", "params": "3.8B", "download_gb": 2.2, "ram_gb": 4.5,
     "notes": "Better reasoning, noticeably slower on CPU."},
    {"name": "qwen2.5:7b", "label": "Heavy (not recommended)", "params": "7.6B", "download_gb": 4.7, "ram_gb": 6.5,
     "notes": "Will strain 8 GB RAM and reply slowly. Only if nothing else is open."},
    {"name": "nomic-embed-text:latest", "label": "Embeddings", "params": "137M", "download_gb": 0.27, "ram_gb": 0.5,
     "notes": "Not a chat model: enables optional semantic search in the Second Brain."},
    {"name": "moondream:latest", "label": "Vision (light)", "params": "1.9B", "download_gb": 1.7, "ram_gb": 2.5,
     "notes": "Optional: lets him describe your screen or a picture. Short, plain descriptions."},
    {"name": "qwen2.5vl:3b", "label": "Vision (better, heavy)", "params": "3.8B", "download_gb": 3.2, "ram_gb": 5.5,
     "notes": "Much better at reading screens and charts, but tight on 8 GB: close other apps first."},
]


def catalog_entry(name: str) -> dict[str, Any] | None:
    base = name if ":" in name else f"{name}:latest"
    for entry in CATALOG:
        if entry["name"] in (name, base):
            return entry
    return None


def estimate_ram_gb(size_bytes: int) -> float:
    """Rough runtime RAM estimate for an unknown model from its file size."""
    return round(size_bytes / 1e9 * 1.25 + 0.6, 1)


class OllamaClient:
    def __init__(self, host: str | None = None, timeout: float = 4.0):
        self.host = (host or config.OLLAMA_HOST).rstrip("/")
        self.timeout = timeout

    # ------------------------------------------------------------ helpers
    def _get(self, path: str, timeout: float | None = None) -> dict:
        try:
            r = requests.get(self.host + path, timeout=timeout or self.timeout)
        except requests.RequestException as exc:
            raise OllamaUnavailable(str(exc)) from exc
        if r.status_code >= 400:
            raise OllamaError(_error_text(r))
        return r.json()

    # ------------------------------------------------------------ status
    def is_available(self) -> bool:
        try:
            self._get("/api/version", timeout=1.5)
            return True
        except (OllamaUnavailable, OllamaError):
            return False

    def version(self) -> str | None:
        try:
            return self._get("/api/version", timeout=1.5).get("version")
        except (OllamaUnavailable, OllamaError):
            return None

    def list_models(self) -> list[dict[str, Any]]:
        models = self._get("/api/tags").get("models", [])
        return [
            {
                "name": m["name"],
                "size": m.get("size", 0),
                "params": m.get("details", {}).get("parameter_size", ""),
                "quantization": m.get("details", {}).get("quantization_level", ""),
                "family": m.get("details", {}).get("family", ""),
                "modified_at": m.get("modified_at"),
            }
            for m in models
        ]

    def running_models(self) -> list[dict[str, Any]]:
        try:
            models = self._get("/api/ps", timeout=2).get("models", [])
        except (OllamaUnavailable, OllamaError):
            return []
        return [{"name": m["name"], "size": m.get("size", 0), "expires_at": m.get("expires_at")}
                for m in models]

    # ------------------------------------------------------------ chat
    def chat_stream(self, model: str, messages: list[dict[str, str]],
                    options: dict[str, Any] | None = None,
                    keep_alive: str | None = None) -> Iterator[dict[str, Any]]:
        """Yield {"token": str} chunks, then one {"done": True, "stats": {...}}."""
        payload: dict[str, Any] = {"model": model, "messages": messages, "stream": True,
                                   "options": options or {}}
        if keep_alive:
            payload["keep_alive"] = keep_alive
        started = time.perf_counter()
        first_token_at: float | None = None
        try:
            # (connect timeout, read timeout): first token can take a while on CPU
            # while the model loads, so the read timeout is generous.
            resp = requests.post(self.host + "/api/chat", json=payload, stream=True,
                                 timeout=(3, 300))
        except requests.RequestException as exc:
            raise OllamaUnavailable(str(exc)) from exc
        with resp:
            if resp.status_code >= 400:
                raise OllamaError(_error_text(resp))
            for line in resp.iter_lines():
                if not line:
                    continue
                data = json.loads(line)
                if data.get("error"):
                    raise OllamaError(data["error"])
                token = data.get("message", {}).get("content", "")
                if token:
                    if first_token_at is None:
                        first_token_at = time.perf_counter()
                    yield {"token": token}
                if data.get("done"):
                    yield {"done": True, "stats": _stats(data, started, first_token_at)}
                    return

    def chat(self, model: str, messages: list[dict[str, str]],
             options: dict[str, Any] | None = None, keep_alive: str | None = None) -> dict[str, Any]:
        text, stats = [], {}
        for chunk in self.chat_stream(model, messages, options, keep_alive):
            if "token" in chunk:
                text.append(chunk["token"])
            else:
                stats = chunk["stats"]
        return {"text": "".join(text), "stats": stats}

    def chat_tools(self, model: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]],
                   options: dict[str, Any] | None = None) -> dict[str, Any]:
        """One non-streaming call with tool definitions; returns the assistant message."""
        try:
            r = requests.post(self.host + "/api/chat", json={"model": model, "messages": messages, "tools": tools,
                                                             "stream": False, "options": options or {}},
                              timeout=(3, 180))
        except requests.RequestException as exc:
            raise OllamaUnavailable(str(exc)) from exc
        if r.status_code >= 400:
            raise OllamaError(_error_text(r))
        return r.json().get("message", {})

    def chat_json(self, model: str, messages: list[dict[str, Any]],
                  options: dict[str, Any] | None = None) -> Any:
        """One non-streaming call constrained to JSON output; returns the parsed object."""
        try:
            r = requests.post(self.host + "/api/chat", json={"model": model, "messages": messages, "stream": False,
                                                             "format": "json", "options": options or {}},
                              timeout=(3, 240))
        except requests.RequestException as exc:
            raise OllamaUnavailable(str(exc)) from exc
        if r.status_code >= 400:
            raise OllamaError(_error_text(r))
        content = r.json().get("message", {}).get("content", "")
        try:
            return json.loads(content)
        except ValueError as exc:
            raise OllamaError("The model returned invalid JSON.") from exc

    def show(self, model: str) -> dict[str, Any]:
        try:
            r = requests.post(self.host + "/api/show", json={"model": model}, timeout=10)
        except requests.RequestException as exc:
            raise OllamaUnavailable(str(exc)) from exc
        if r.status_code >= 400:
            raise OllamaError(_error_text(r))
        return r.json()

    def embed(self, model: str, texts: list[str]) -> list[list[float]]:
        try:
            r = requests.post(self.host + "/api/embed", json={"model": model, "input": texts},
                              timeout=(3, 120))
        except requests.RequestException as exc:
            raise OllamaUnavailable(str(exc)) from exc
        if r.status_code >= 400:
            raise OllamaError(_error_text(r))
        return r.json().get("embeddings", [])

    # ------------------------------------------------------------ management
    def pull_model(self, name: str) -> Iterator[dict[str, Any]]:
        try:
            resp = requests.post(self.host + "/api/pull", json={"model": name, "stream": True},
                                 stream=True, timeout=(3, 3600))
        except requests.RequestException as exc:
            raise OllamaUnavailable(str(exc)) from exc
        with resp:
            if resp.status_code >= 400:
                raise OllamaError(_error_text(resp))
            for line in resp.iter_lines():
                if line:
                    data = json.loads(line)
                    if data.get("error"):
                        raise OllamaError(data["error"])
                    yield data

    def delete_model(self, name: str) -> None:
        try:
            r = requests.delete(self.host + "/api/delete", json={"model": name}, timeout=30)
        except requests.RequestException as exc:
            raise OllamaUnavailable(str(exc)) from exc
        if r.status_code >= 400:
            raise OllamaError(_error_text(r))

    def unload_model(self, name: str) -> None:
        """Free RAM immediately by asking Ollama to unload the model."""
        try:
            requests.post(self.host + "/api/generate", json={"model": name, "keep_alive": 0},
                          timeout=10)
        except requests.RequestException as exc:
            raise OllamaUnavailable(str(exc)) from exc


def _error_text(resp: requests.Response) -> str:
    try:
        return resp.json().get("error") or resp.text
    except ValueError:
        return resp.text or f"HTTP {resp.status_code}"


def _stats(data: dict, started: float, first_token_at: float | None) -> dict[str, Any]:
    ns = 1e9
    eval_count = data.get("eval_count") or 0
    eval_dur = (data.get("eval_duration") or 0) / ns
    p_count = data.get("prompt_eval_count") or 0
    p_dur = (data.get("prompt_eval_duration") or 0) / ns
    return {
        "total_ms": round((time.perf_counter() - started) * 1000),
        "first_token_ms": round((first_token_at - started) * 1000) if first_token_at else None,
        "load_ms": round((data.get("load_duration") or 0) / 1e6),
        "output_tokens": eval_count,
        "prompt_tokens": p_count,
        "tokens_per_sec": round(eval_count / eval_dur, 1) if eval_dur else None,
        "prompt_tokens_per_sec": round(p_count / p_dur, 1) if p_dur else None,
    }
