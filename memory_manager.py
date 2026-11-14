import json
from pathlib import Path

MEMORY_FILE = Path(__file__).resolve().parent / "memory" / "adventure_log.json"


def load_memory():
    if not MEMORY_FILE.exists():
        return {"entries": []}

    with open(MEMORY_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_memory(data):
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)


def add_entry(entry_type, summary):
    data = load_memory()

    data["entries"].append({
        "type": entry_type,
        "summary": summary
    })

    save_memory(data)


def get_recent_entries(limit=5):
    data = load_memory()
    return data["entries"][-limit:]