from knowledge_retriever import retrieve_answer


def ask_babilious(question: str) -> str:
    """Return a short in-character answer grounded in the local knowledge base."""
    q = question.lower()

    if "api" in q:
        return (
            "The expedition once possessed a powerful AI engine.\n\n"
            "Unfortunately, it required funding.\n"
            "We no longer discuss the incident."
        )

    if "money" in q or "budget" in q:
        return (
            "Current treasury report:\n\n"
            "• 2 paperclips\n"
            "• one notebook\n"
            "• optimism\n\n"
            "Budget status remains concerning."
        )

    result = retrieve_answer(question)

    if result:
        _, filename, content = result
        return (
            "Ah, let me consult the expedition archives.\n\n"
            f"{content[:900]}\n\n"
            "The remainder of the record has been condensed for traveller sanity."
        )

    return (
        "A curious question.\n\n"
        "Unfortunately the expedition archives contain no reliable record regarding "
        "that topic.\n\n"
        "Either the information has not yet been documented, or Abubakar forgot to "
        "tell me.\n\n"
        "Both possibilities are equally plausible."
    )
