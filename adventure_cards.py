from memory_manager import load_memory


def generate_cards():
    data = load_memory()

    cards = []

    for entry in data["entries"]:
        card = {
            "title": f"Adventure: {entry['type'].title()}",
            "summary": entry["summary"]
        }

        cards.append(card)

    return cards