from memory_manager import load_memory
from state_manager import load_state
from mission_manager import mission_count, completed_count

data = load_memory()
state = load_state()

print("\n=== BABILIOUS ===\n")

print("Current Expedition")
print("------------------")
print(state["current_expedition"])

print("\nRecent Adventures")
print("------------------")

for entry in data["entries"][-5:]:
    print(f"✓ {entry['summary']}")
    
print("\nStatistics")
print("------------------")
print(f"Total Missions: {mission_count()}")
print(f"Completed Missions: {completed_count()}")
print(f"Total Adventures: {len(data['entries'])}")

print("\nCurrent Mode")
print("------------------")
print(state["mode"])

print("\nActive Missions")
print("------------------")

for mission in state["active_missions"]:
    print(f"□ {mission}")