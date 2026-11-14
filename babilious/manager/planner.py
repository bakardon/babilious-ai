"""'Help me organise my master's applications' → a task plan you approve.

The local model drafts a short plan as JSON; it is validated and shown as an
approval card. Nothing is created until you press Approve, and every task can
be edited or deleted afterwards like any other.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date, timedelta
from typing import Any

from .. import activity, tasks
from ..tools import Risk, Tool, ToolResult, register
from .reminders import local_now

log = logging.getLogger(__name__)

PLAN_PROMPT = """You are a practical planner. Break the user's goal into 4 to 8 concrete, actionable tasks in a sensible order.
Reply with JSON only, in exactly this shape:
{"project": "short project name", "tasks": [{"title": "verb-first task, max 80 chars", "due_in_days": 7, "priority": 2, "notes": ""}]}
Rules: due_in_days is days from today (0-365) or null if there is no natural deadline; priority 1=high, 2=normal, 3=low.
Use real deadlines the user mentions. Do not invent facts (university names, dates) the user didn't give."""


def draft(goal: str, client: Any, model: str, context: str = "") -> dict[str, Any]:
    from ..llm import OllamaError
    messages = [{"role": "system", "content": PLAN_PROMPT},
                {"role": "user", "content": f"Today is {local_now():%A %d %B %Y}.\n{context}\nGoal: {goal}"}]
    last_error: Exception | None = None
    # Small models sometimes loop in JSON mode or return a thin plan: one retry with more variety.
    for options in ({"temperature": 0.2, "num_ctx": 4096, "num_predict": 700, "repeat_penalty": 1.15},
                    {"temperature": 0.6, "num_ctx": 4096, "num_predict": 700, "repeat_penalty": 1.3}):
        try:
            return validate(client.chat_json(model, messages, options=options), goal)
        except (OllamaError, ValueError) as exc:
            log.info("Plan attempt failed: %s", exc)
            last_error = exc
    raise ValueError(str(last_error) if isinstance(last_error, ValueError) else
                     "The model got stuck writing the plan. Try again, or phrase the goal a little differently.")


def validate(raw: Any, goal: str) -> dict[str, Any]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError as exc:
            raise ValueError("The model didn't return a usable plan. Try rephrasing the goal.") from exc
    # Accept the shapes small models actually produce: a bare list, other key names, plain strings.
    if isinstance(raw, list):
        raw = {"tasks": raw}
    if isinstance(raw, dict) and not isinstance(raw.get("tasks"), list):
        for key in ("plan", "steps", "items", "todo", "todos", "actions"):
            if isinstance(raw.get(key), list):
                raw = {**raw, "tasks": raw[key]}
                break
    if not isinstance(raw, dict) or not isinstance(raw.get("tasks"), list):
        raise ValueError("The model didn't return a usable plan.")
    project = re.sub(r"\s+", " ", str(raw.get("project") or raw.get("name") or goal)).strip()[:60] or "Plan"
    if project == project.lower():  # "rust-learning" → "Rust Learning"
        project = re.sub(r"[-_]+", " ", project).title()
    out = []
    seen = set()
    for t in raw["tasks"][:10]:
        if isinstance(t, str):
            t = {"title": t}
        if not isinstance(t, dict):
            continue
        t = {**t, "title": t.get("title") or t.get("task") or t.get("name") or t.get("step") or ""}
        title = re.sub(r"\s+", " ", str(t.get("title") or "")).strip().rstrip(".")[:120]
        if len(title) < 3 or title.lower() in seen:
            continue
        seen.add(title.lower())
        due = t.get("due_in_days")
        try:
            due = int(due) if due is not None and str(due).strip() not in ("", "null") else None
        except (TypeError, ValueError):
            due = None
        if due is not None and not 0 <= due <= 365:
            due = None
        try:
            pr = int(t.get("priority") or 2)
        except (TypeError, ValueError):
            pr = 2
        out.append({"title": title[0].upper() + title[1:], "due_in_days": due, "priority": min(3, max(1, pr)),
                    "notes": str(t.get("notes") or "").strip()[:300]})
    if len(out) < 2:
        raise ValueError("The plan came back too thin to be useful. Try giving me a bit more detail.")
    return {"project": project, "tasks": out}


def _dated(plan_tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    today = local_now().date()
    return [{**t, "due_date": (today + timedelta(days=t["due_in_days"])).isoformat() if t["due_in_days"] is not None else None}
            for t in plan_tasks]


def _risk(args: dict[str, Any]) -> Risk:
    try:
        plan = validate({"project": args.get("project"), "tasks": args.get("tasks")}, args.get("project", ""))
    except ValueError as exc:
        return Risk("blocked", str(exc))
    lines = []
    for i, t in enumerate(_dated(plan["tasks"]), 1):
        due = f" · due {date.fromisoformat(t['due_date']):%a %d %b}" if t["due_date"] else ""
        flag = " · high priority" if t["priority"] == 1 else ""
        lines.append(f"{i}. {t['title']}{due}{flag}")
    return Risk("confirm", "\n".join(lines))


def _create(args: dict[str, Any]) -> ToolResult:
    plan = validate({"project": args.get("project"), "tasks": args.get("tasks")}, args.get("project", ""))
    ids = []
    for t in _dated(plan["tasks"]):
        ids.append(tasks.create_task(t["title"], notes=t["notes"], priority=t["priority"], project=plan["project"],
                                     due_date=t["due_date"]))
    activity.record("task", f"Created {len(ids)} tasks in project {plan['project']}")
    return ToolResult(True, f"Created {len(ids)} tasks in \"{plan['project']}\". They're in Tasks, and the dated ones "
                            "are on the Calendar.", data={"task_ids": ids, "project": plan["project"]})


register(Tool("create_tasks", "Create several tasks in a project (from a plan).",
              {"project": {"type": "string"}, "tasks": {"type": "array"}}, ["project", "tasks"], _create,
              lambda a: f"Create {len(a.get('tasks') or [])} tasks in project \"{a.get('project')}\"", _risk,
              model_visible=False))
