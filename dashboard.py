"""Small terminal dashboard for Babilious' stored context."""
from memory_manager import load_memory
from state_manager import load_state
from mission_manager import mission_count, completed_count


data = load_memory()
state = load_state()

print("\n=== BABILIOUS ===\n")
print("Recent context")
print("--------------")
for entry in data.get("entries", [])[-5:]:
    print(f"• {entry['summary']}")

print("\nStatistics")
print("----------")
print(f"Total tasks: {mission_count()}")
print(f"Completed tasks: {completed_count()}")
print(f"Stored memories: {len(data.get('entries', []))}")
print(f"Current mode: {state.get('mode', 'default')}")

active = state.get("active_missions", [])
if active:
    print("\nActive items")
    print("------------")
    for item in active:
        print(f"• {item}")
