from memory_manager import add_entry


def log_project(project_name):
    add_entry(
        "project",
        f"Started working on {project_name}"
    )


def log_goal(goal):
    add_entry(
        "goal",
        goal
    )


def log_milestone(milestone):
    add_entry(
        "milestone",
        milestone
    )