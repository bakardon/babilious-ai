import json
from pathlib import Path

STATE_FILE = Path(__file__).resolve().parent / "state" / "user_state.json"


def load_state():
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)