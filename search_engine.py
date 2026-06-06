from pathlib import Path

KNOWLEDGE_FOLDER = Path("knowledge")


def search_knowledge(query):

    query_words = query.lower().split()

    best_match = None
    best_score = 0

    for file in KNOWLEDGE_FOLDER.glob("*.md"):

        content = file.read_text(
            encoding="utf-8"
        )

        score = 0

        for word in query_words:

            score += content.lower().count(word)

        if score > best_score:

            best_score = score

            best_match = {
                "source": file.name,
                "content": content,
                "score": score
            }

    return best_match