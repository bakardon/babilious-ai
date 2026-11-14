"""Lightweight retrieval over the Markdown files in ``knowledge/``.

Files are split into heading sections and scored with keyword overlap plus
a few topic synonyms. It is cheap (no embeddings, no model) and good enough to
feed the LLM a couple of relevant snippets. Milestone 2 replaces this with a
SQLite full-text index over user-selected folders.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from . import config

STOP_WORDS = {
    "what", "who", "is", "are", "the", "a", "an", "he", "she", "has", "have", "had",
    "on", "in", "at", "of", "to", "his", "her", "their", "and", "or", "for", "with",
    "me", "my", "you", "your", "about", "tell", "does", "do", "did", "can", "could",
    "would", "should", "this", "that", "it", "be", "was", "were", "how", "why",
    "when", "which", "there", "some", "any", "please",
}

# Words that point at a specific file even when the file does not contain them.
TOPIC_HINTS = {
    "projects.md": {"project", "projects", "built", "build", "building", "portfolio", "worked"},
    "ambitions.md": {"ambition", "ambitions", "goal", "goals", "future", "plan", "plans"},
    "journey.md": {"journey", "challenge", "challenges", "lesson", "lessons", "story"},
    "about_abubakar.md": {"abubakar", "background", "strength", "strengths", "education", "giki"},
    "why_hire_me.md": {"hire", "hiring", "recruit", "candidate", "employ"},
    "skills.md": {"skill", "skills", "technologies", "stack", "languages"},
}
# Files that describe Babilious himself are already in the persona.
EXCLUDED = {"personality_notes.md", "faq.md"}


@dataclass(frozen=True)
class Section:
    source: str
    heading: str
    text: str


def _tokens(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9][a-z0-9+#.-]*", text.lower())
    return [w.strip(".-") for w in words if w not in STOP_WORDS and len(w) > 2]


def _stem(word: str) -> str:
    for suffix in ("ing", "ies", "es", "s", "ed"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)]
    return word


@lru_cache(maxsize=1)
def _load(folder: str, signature: tuple) -> tuple[Section, ...]:
    sections: list[Section] = []
    for path in sorted(Path(folder).glob("*.md")):
        if path.name in EXCLUDED:
            continue
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            continue
        heading, buf = path.stem.replace("_", " ").title(), []
        for line in text.splitlines():
            if re.match(r"^#{1,3}\s", line):
                if "".join(buf).strip():
                    sections.append(Section(path.name, heading, "\n".join(buf).strip()))
                heading, buf = line.lstrip("#").strip(), []
            else:
                buf.append(line)
        if "".join(buf).strip():
            sections.append(Section(path.name, heading, "\n".join(buf).strip()))
    return tuple(sections)


def sections(folder: Path | None = None) -> tuple[Section, ...]:
    folder = Path(folder or config.KNOWLEDGE_DIR)
    # Re-read automatically when any file changes.
    signature = tuple((p.name, p.stat().st_mtime) for p in sorted(folder.glob("*.md")))
    return _load(str(folder), signature)


def search(query: str, limit: int = 3, folder: Path | None = None) -> list[tuple[float, Section]]:
    q_words = {_stem(w) for w in _tokens(query)}
    raw_words = set(_tokens(query))
    if not q_words:
        return []
    results = []
    for sec in sections(folder):
        body = {_stem(w) for w in _tokens(sec.heading + " " + sec.text)}
        head = {_stem(w) for w in _tokens(sec.heading)}
        score = len(q_words & body) + 1.5 * len(q_words & head)
        if raw_words & TOPIC_HINTS.get(sec.source, set()):
            score += 2
        if score > 0:
            results.append((score, sec))
    results.sort(key=lambda r: r[0], reverse=True)
    return results[:limit]


def relevant_notes(query: str, limit: int = 2, max_chars: int = 900,
                   min_score: float = 2.0) -> list[tuple[str, str]]:
    """Snippets worth adding to the prompt, trimmed to keep CPU cost down."""
    notes = []
    for score, sec in search(query, limit=limit):
        if score < min_score:
            continue
        text = sec.text if len(sec.text) <= max_chars else sec.text[:max_chars].rsplit(" ", 1)[0] + " ..."
        notes.append((f"{sec.source} > {sec.heading}", text))
    return notes


def file_list(folder: Path | None = None) -> list[dict]:
    folder = Path(folder or config.KNOWLEDGE_DIR)
    out = []
    for p in sorted(folder.glob("*.md")):
        text = p.read_text(encoding="utf-8")
        out.append({"name": p.name, "chars": len(text), "empty": not text.strip()})
    return out
