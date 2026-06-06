import json
from pathlib import Path

MISSION_FILE = Path("state/missions.json")


def load_missions():
    with open(MISSION_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def mission_count():
    data = load_missions()
    return len(data["missions"])


def completed_count():
    data = load_missions()

    return len(
        [m for m in data["missions"] if m["completed"]]
    )