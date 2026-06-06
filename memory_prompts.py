from memory_manager import get_recent_entries


def build_memory_context():
    entries = get_recent_entries()

    if not entries:
        return "No adventures recorded yet."

    memory_text = "Recent Adventure Log:\n"

    for entry in entries:
        memory_text += f"- [{entry['type']}] {entry['summary']}\n"

    return memory_text