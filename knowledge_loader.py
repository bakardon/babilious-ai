from pathlib import Path

KNOWLEDGE_FOLDER = Path("knowledge")


def load_knowledge():
    knowledge = []

    for file in KNOWLEDGE_FOLDER.glob("*.md"):
        try:
            content = file.read_text(
                encoding="utf-8"
            )

            knowledge.append(
                f"\n=== {file.stem.upper()} ===\n"
            )

            knowledge.append(content)

        except Exception:
            pass

    return "\n".join(knowledge)