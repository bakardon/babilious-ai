"""Build model-independent prompts from the persona in character.py.

The stable part (identity, rules, examples) comes first and never changes
between turns, so Ollama can reuse its prompt cache. Dynamic context
(memories, tasks, retrieved notes, date) is sent as a separate message at
the end, right before the user's question.
"""
from __future__ import annotations

from datetime import datetime

import character


def system_prompt(user_name: str = "Abubakar") -> str:
    examples = "\n".join(
        f"User: {q}\nBabilious: {a}" for q, a in character.EXAMPLES
    )
    return (
        f"{character.IDENTITY}\n\n"
        f"Your traits: {', '.join(character.TRAITS)}.\n\n"
        f"{character.RULES.format(user_name=user_name)}\n\n"
        "Examples showing your TONE only. Never copy their wording or the details in them; "
        "base every answer on the real conversation and context you are given:\n"
        f"{examples}"
    )


def context_block(memories: str, tasks: str, notes: list[tuple[str, str]]) -> str:
    """Per-turn context. Empty sections are omitted to save tokens."""
    parts = [f"Current local time: {datetime.now().strftime('%A %d %B %Y, %H:%M')}."]
    if memories:
        parts.append(f"Things you remember about the user (user-approved):\n{memories}")
    if tasks:
        parts.append(f"The user's open tasks (the ONLY work you know about):\n{tasks}")
    else:
        parts.append("The user has no open tasks recorded.")
    if notes:
        joined = "\n\n".join(f"[{src}]\n{text}" for src, text in notes)
        parts.append(
            "Notes from the local knowledge base that may help (use only if relevant):\n"
            f"{joined}"
        )
    return "\n\n".join(parts)
