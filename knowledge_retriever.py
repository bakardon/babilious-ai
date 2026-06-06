from pathlib import Path

KNOWLEDGE_FOLDER = Path("knowledge")

STOP_WORDS = {
    "what", "who", "is", "are", "the", "a", "an",
    "he", "she", "has", "have", "had", "on", "in",
    "at", "of", "to", "his", "her", "their"
}


def retrieve_answer(question):

    question = question.lower()

    # Explicit routing first
    if "project" in question or "worked on" in question or "built" in question:
        target = KNOWLEDGE_FOLDER / "projects.md"
        return (
    999,
    "projects.md",
    target.read_text(encoding="utf-8").replace("#", "")
)

    if "ambition" in question or "goal" in question or "future" in question:
        target = KNOWLEDGE_FOLDER / "ambitions.md"
        return (
            999,
            "ambitions.md",
            target.read_text(encoding="utf-8").replace("#", "")
        )

    if "challenge" in question or "journey" in question or "lesson" in question:
        target = KNOWLEDGE_FOLDER / "journey.md"
        return (
            999,
            "journey.md",
            target.read_text(encoding="utf-8").replace("#", "")
        )

    if "who is" in question or "abubakar" in question or "about" in question:
        target = KNOWLEDGE_FOLDER / "about_abubakar.md"
        return (
            999,
            "about_abubakar.md",
            target.read_text(encoding="utf-8").replace("#", "")
        )

    # Fallback search
    matches = []

    for file in KNOWLEDGE_FOLDER.glob("*.md"):

        if file.name == "faq.md":
            continue

        try:
            content = file.read_text(encoding="utf-8")
            score = 0

            for word in question.split():

                word = word.strip("?,.!")

                if word in STOP_WORDS:
                    continue

                if len(word) < 3:
                    continue

                if word in content.lower():
                    score += 1

            matches.append(
                (score, file.name, content)
            )

        except Exception:
            pass

    matches.sort(
        key=lambda x: x[0],
        reverse=True
    )

    if matches and matches[0][0] > 0:
        return matches[0]

    return None