from pathlib import Path

KNOWLEDGE_FOLDER = Path(__file__).resolve().parent / "knowledge"

def load_knowledge():
    knowledge = []

    for file in KNOWLEDGE_FOLDER.glob("*.md"):
        content = file.read_text(
            encoding="utf-8"
        )

        knowledge.append(
            f"\n=== {file.stem.upper()} ===\n"
        )

        knowledge.append(content)

    return "\n".join(knowledge)