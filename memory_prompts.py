from memory_manager import get_recent_entries


def build_memory_context():
    """Return recent memory in a neutral form for future AI integrations."""
    entries = get_recent_entries()
    if not entries:
        return "No recent memories recorded."

    return "Recent context:\n" + "\n".join(
        f"- [{entry['type']}] {entry['summary']}" for entry in entries
    )
