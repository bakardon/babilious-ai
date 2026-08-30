from knowledge_retriever import retrieve_answer


def _clean_markdown(text: str) -> str:
    """Turn local markdown notes into readable chat text."""
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            if lines and lines[-1] != "":
                lines.append("")
            continue
        if stripped.startswith("#"):
            stripped = stripped.lstrip("#").strip()
        lines.append(stripped)
    return "\n".join(lines).strip()


def _intro() -> str:
    return (
        "I'm Babilious — Abubakar's AI companion and portfolio manager.\n\n"
        "I can tell you about his background, projects, technical skills, experience, "
        "and the things he's building. What would you like to know?"
    )


def ask_babilious(question: str) -> str:
    """Answer a visitor using Babilious' local portfolio knowledge base."""
    q = question.lower().strip()

    if q in {"hi", "hello", "hey", "yo", "sup", "hey there"}:
        return "Hey. I'm Babilious. What would you like to know about Abubakar?"

    if "who are you" in q or "what are you" in q:
        return (
            "I'm Babilious — Abubakar's AI companion and portfolio manager. "
            "Think of me as the helpful layer between you and his portfolio."
        )

    if "who is abubakar" in q or q in {"about abubakar", "tell me about abubakar"}:
        return _intro() if not retrieve_answer("about Abubakar") else _answer_from("about Abubakar")

    result = retrieve_answer(question)
    if result:
        _, filename, content = result
        return _clean_markdown(content[:2200])

    return (
        "I don't have a reliable answer for that in my current knowledge base. "
        "Try asking about Abubakar, his projects, skills, experience, or ambitions."
    )


def _answer_from(question: str) -> str:
    result = retrieve_answer(question)
    if not result:
        return _intro()
    return _clean_markdown(result[2][:2200])
