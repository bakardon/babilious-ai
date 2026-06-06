from memory_manager import load_memory
from mission_manager import load_missions
from state_manager import load_state


def generate_briefing():
    state = load_state()
    memory = load_memory()
    missions = load_missions()

    briefing = []

    briefing.append("=== EXPEDITION BRIEFING ===\n")

    briefing.append(
        f"Current Expedition: {state['current_expedition']}\n"
    )

    briefing.append("Recent Adventures:")

    for entry in memory["entries"][-3:]:
        briefing.append(
            f"- {entry['summary']}"
        )

    briefing.append("\nOutstanding Missions:")

    for mission in missions["missions"]:
        if not mission["completed"]:
            briefing.append(
                f"- {mission['objective']}"
            )

    return "\n".join(briefing)