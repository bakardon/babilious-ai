"""Compatibility helpers for Babilious' portfolio context and memory."""
from memory_manager import load_memory, add_entry


def log_project(project_name):
    add_entry("project", f"Started working on {project_name}")


def log_goal(goal):
    add_entry("goal", goal)


def log_milestone(milestone):
    add_entry("milestone", milestone)


def get_memory_cards():
    return [
        {"title": entry["type"].title(), "summary": entry["summary"]}
        for entry in load_memory().get("entries", [])
    ]
