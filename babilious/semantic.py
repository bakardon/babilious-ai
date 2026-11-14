"""Optional semantic search with a local embedding model through Ollama.

Off by default. Turn it on in Settings once an embedding model is installed
(``ollama pull nomic-embed-text``, ~270 MB). Vectors live in SQLite; search is
a brute-force cosine over a cached numpy matrix, which is fast for the tens of
thousands of chunks a personal notes folder produces.
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Callable

import numpy as np

from . import db

log = logging.getLogger(__name__)

MIN_SIMILARITY = 0.55
BATCH = 16

_cache_lock = threading.Lock()
_cache: dict[str, Any] = {"key": None, "ids": None, "matrix": None}


def enabled() -> bool:
    return bool(db.get_setting("semantic_search", False))


def model_name() -> str:
    return str(db.get_setting("embed_model", "nomic-embed-text"))


def _normalise(vectors: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0] = 1
    return vectors / norms


def embed_missing(client: Any, progress: Callable[[int], None] | None = None,
                  max_chunks: int = 20_000) -> int:
    """Embed chunks that don't have a vector for the current model yet."""
    model = model_name()
    done = 0
    while done < max_chunks:
        with db.connect() as conn:
            todo = db.rows(conn.execute(
                "SELECT c.id, c.text FROM chunks c LEFT JOIN embeddings e ON e.chunk_id = c.id "
                "AND e.model = ? WHERE e.chunk_id IS NULL LIMIT ?", (model, BATCH)))
        if not todo:
            break
        vectors = client.embed(model, [t["text"][:2000] for t in todo])
        with db.connect() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO embeddings(chunk_id, model, vector) VALUES (?, ?, ?)",
                [(t["id"], model, np.asarray(v, dtype=np.float32).tobytes()) for t, v in zip(todo, vectors)])
        done += len(todo)
        if progress:
            progress(done)
    return done


def _matrix() -> tuple[list[int], np.ndarray | None]:
    model = model_name()
    with db.connect() as conn:
        key = (model, *conn.execute(
            "SELECT COUNT(*), COALESCE(MAX(chunk_id), 0) FROM embeddings WHERE model = ?", (model,)).fetchone())
        with _cache_lock:
            if _cache["key"] == key:
                return _cache["ids"], _cache["matrix"]
        rows = conn.execute("SELECT chunk_id, vector FROM embeddings WHERE model = ?", (model,)).fetchall()
    if not rows:
        return [], None
    ids = [r["chunk_id"] for r in rows]
    matrix = _normalise(np.vstack([np.frombuffer(r["vector"], dtype=np.float32) for r in rows]))
    with _cache_lock:
        _cache.update(key=key, ids=ids, matrix=matrix)
    return ids, matrix


def search(client: Any, query: str, limit: int = 5) -> list[tuple[float, dict[str, Any]]]:
    ids, matrix = _matrix()
    if matrix is None:
        return []
    try:
        q = np.asarray(client.embed(model_name(), [query])[0], dtype=np.float32)
    except Exception as exc:  # noqa: BLE001 - semantic search is best-effort
        log.warning("Embedding the query failed: %s", exc)
        return []
    if q.shape[0] != matrix.shape[1]:
        return []
    sims = matrix @ (q / (np.linalg.norm(q) or 1))
    top = np.argsort(-sims)[:limit]
    chosen = {ids[i]: float(sims[i]) for i in top}
    with db.connect() as conn:
        rows = db.rows(conn.execute(
            "SELECT c.id AS chunk_id, c.doc_id, c.text, d.name, d.path FROM chunks c "
            f"JOIN documents d ON d.id = c.doc_id WHERE c.id IN ({','.join('?' * len(chosen))})",
            tuple(chosen)))
    return sorted(((chosen[r["chunk_id"]], r) for r in rows), key=lambda x: -x[0])
