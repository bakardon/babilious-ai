"""The orchestrator: commands first, then the local LLM, then an offline fallback.

``respond`` yields events the web layer streams to the browser:
  {"type": "state", "state": "thinking"}
  {"type": "token", "text": "..."}
  {"type": "done", "text": full_reply, "mood": "...", "source": "...", "sources": [...], ...}
  {"type": "error", "message": "..."}
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, Iterator

from . import activity, db, indexer, intents, knowledge, memory, persona, tasks
from .integrations import commands as music_commands
from .research import engine as research
from .tools import actions, router
from .manager import commands as manager_commands
from .manager import notes as manager_notes
from .manager import reminders as manager_reminders
from .llm import OllamaClient, OllamaError, OllamaUnavailable

log = logging.getLogger(__name__)


def _offline_reply(text: str) -> tuple[str, list[dict[str, Any]]]:
    lead = ("My brain (Ollama) is offline, so I'm running on reflexes. "
            "Start it with `ollama serve` and I'll be back to full wit.")
    docs = indexer.retrieve_for_chat(text, limit=1)
    if docs:
        d = docs[0]
        return (f"{lead}\n\nThe closest thing in your files is **{d['name']}**:\n\n{d['text']}",
                [{"doc_id": d["doc_id"], "name": d["name"], "path": d["path"]}])
    hits = knowledge.search(text, limit=1)
    if hits and hits[0][0] >= 2:
        sec = hits[0][1]
        snippet = sec.text if len(sec.text) < 900 else sec.text[:900].rsplit(" ", 1)[0] + " ..."
        return f"{lead}\n\nHere's what my notes say ({sec.source} > {sec.heading}):\n\n{snippet}", []
    return f"{lead}\n\nUntil then I can still manage tasks, memories, settings and file search.", []


def build_messages(conversation_id: int, user_text: str, client: OllamaClient | None = None,
                   task: dict[str, Any] | None = None,
                   memory_offer: str | None = None) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    """Return (messages for the model, file sources used)."""
    settings = db.all_settings()
    history = memory.get_messages(conversation_id, limit=int(settings["history_messages"]) + 1)
    # The newest stored message is the current user turn; it is appended after context.
    if history and history[-1]["role"] == "user" and history[-1]["content"] == user_text:
        history = history[:-1]
    if task:  # a document task needs the room in the context window more than chat history
        history = []
    msgs = [{"role": "system", "content": persona.system_prompt(settings.get("user_name", "Abubakar"))}]
    msgs += [{"role": m["role"], "content": m["content"]} for m in history
             if m["role"] in ("user", "assistant")]

    notes = knowledge.relevant_notes(user_text)
    if not task:
        notes += manager_notes.relevant(user_text)
        today = [r for r in manager_reminders.list_reminders()
                 if r["status"] == "fired" or r["due_local"][:10] == manager_reminders.local_now().date().isoformat()]
        latest = research.recent_for(conversation_id)
        if latest:  # follow-up questions about research just done in this chat
            refs = "\n".join(f"[{x['n']}] {x['title']} {x['url']}" for x in latest["sources"])
            notes.append((f"research you just did on {latest['topic']}", f"{latest['summary'][:1400]}\n{refs}"))
        if today:
            notes.append(("reminders today", "\n".join(f"- {r['text']} ({r['when']})" for r in today[:5])))
    sources: list[dict[str, Any]] = []
    if task:
        sources = task.get("sources", [])
    elif settings.get("use_documents_in_chat", True):
        for d in indexer.retrieve_for_chat(user_text, embed_client=client):
            notes.append((f"file: {d['name']}", d["text"]))
            sources.append({"doc_id": d["doc_id"], "name": d["name"], "path": d["path"]})
    if task and task.get("isolated"):
        # Briefings/reviews: the task context is the whole truth; extra lists only tempt the model to embellish.
        ctx = f"Current local time: {datetime.now():%A %d %B %Y, %H:%M}."
    else:
        ctx = persona.context_block(memory.memory_context(), tasks.task_context(), notes)
    if sources and not task:
        ctx += ("\n\nIf you use anything from a 'file:' note, name the file it came from. "
                "Do not claim a file says something it doesn't.")
    if memory_offer:
        ctx += ("\n\nReply to the user's message normally and naturally. (Side note: the app shows them "
                "its own 'Remember this?' button, so don't say you saved or noted anything.)")
    if task:
        ctx += "\n\n" + task["context"]
    msgs.append({"role": "system", "content": ctx})
    msgs.append({"role": "user", "content": task["instruction"] if task else user_text})
    return msgs, sources


WHY_FAILED = re.compile(r"\b(why did (it|that|this|the tests?|the command|the build) fail|what went wrong|"
                        r"explain (the|that|this) (failure|error|output|traceback)|find the error\b(?! on (my|the) screen)|"
                        r"fix (the|that|this) (error|failure))", re.I)


APPROVE_SAY = re.compile(r"^(?:(?:hey\s+)?babilious[,\s]+)?(?:yes|yeah|yep|ok(?:ay)?|sure)?[,\s]*(?:approve(?:d)?|do it|go ahead|"
                         r"run it|confirm(?:ed)?|proceed)(?:\s+(?:it|that|please))?[.!]?$|^(?:yes|yeah|yep)[.!]?$", re.I)
REJECT_SAY = re.compile(r"^(?:(?:hey\s+)?babilious[,\s]+)?(?:no|nope|don'?t|cancel|stop|reject|abort|never ?mind)"
                        r"(?:\s+(?:it|that|do it|please))?[.!]?$", re.I)


def _last_reply(conversation_id: int) -> str:
    msgs = memory.get_messages(conversation_id, limit=3)
    return next((m["content"] for m in reversed(msgs) if m["role"] == "assistant"), "")


def _tool_reply(action: dict[str, Any]) -> tuple[str, str]:
    """(chat text, mood) for an action outcome. Says exactly what happened, nothing more."""
    status = action["status"]
    if status == "pending":
        warn = "⚠️ " if action["risk"] == "danger" else ""
        return (f"{warn}That needs your OK first: **{action['preview']}**. {action.get('reason', '')}".strip(),
                "curious")
    if status == "blocked":
        return action["summary"], "annoyed"
    if status == "failed":
        out = action.get("output", "").strip()
        tail = "\n```\n" + out[-1500:] + "\n```" if out else ""
        return f"That didn't work. {action['summary']}{tail}", "error"
    out = action.get("output", "").strip()
    if out and action["tool"] in ("describe_screen", "describe_image"):
        return f"{out}\n\n({action['summary']})", "happy"   # the vision model's own words
    if out and action["tool"] in ("list_folder", "search_folder", "run_command", "git_status", "github_issues"):
        shown = out if len(out) <= 2500 else out[:2500] + "\n..."
        return f"{action['summary']}\n```\n{shown}\n```", "happy"
    return f"Done. {action['summary']}", "happy"


def _followup_task(action: dict[str, Any], question: str) -> dict[str, Any]:
    tool = action["tool"]
    what = {"read_screen": "Text read from the user's screen with OCR (layout is lost; lines may be jumbled)",
            "read_clipboard": "The user's clipboard contents",
            "read_file": f"Contents of the file {action['data'].get('path', '')}",
            "read_webpage": "Text of the web page", "web_read": "Text of the page in the automation window",
            "git_diff": "The project's uncommitted git changes"}.get(
        tool, "Tool output")
    return {
        "context": f"{what}:\n---\n{action.get('output', '')[:6000]}\n---",
        "instruction": (f"{question}\n\n(Answer using the {what.split(' (')[0].lower()} above. Be concise. "
                        "If it shows an error, explain the cause and the fix. Don't invent details that aren't there.)"),
        "sources": [{"doc_id": None, "name": action["data"].get("name") or action["data"].get("title") or tool,
                     "path": action["data"].get("path") or action["data"].get("url", "")}] if tool != "read_screen" else [],
        "action": action,
        "detail": {"read_screen": "Reading your screen...", "read_clipboard": "Reading your clipboard...",
                   "read_webpage": "Reading the page..."}.get(tool, "Reading..."),
    }


def respond(conversation_id: int, user_text: str, client: OllamaClient,
            via: str = "text") -> Iterator[dict[str, Any]]:
    user_text = user_text.strip()
    last = _last_reply(conversation_id)
    memory.add_message(conversation_id, "user", user_text)

    # 0. Personal manager: reminders, briefing, habits, notes, plans, history.
    try:
        mres = manager_commands.handle(user_text, client, conversation_id)
    except ValueError as exc:
        mres = manager_commands.Reply(str(exc), "confused", "error")
    except Exception as exc:  # noqa: BLE001
        log.exception("Manager command failed")
        mres = manager_commands.Reply(f"That blew up on my side: {exc}", "error", "error")
    if mres and not mres.task:
        memory.add_message(conversation_id, "assistant", mres.text, source="tool" if mres.actions else "command")
        yield {"type": "done", "text": mres.text, "mood": mres.mood, "source": "command", "action": mres.action,
               "data": mres.data, "actions": mres.actions}
        return

    # 0a. Music: "what's playing?", "play X on Spotify".
    try:
        mus = None if mres else music_commands.handle(user_text)
    except Exception as exc:  # noqa: BLE001
        log.exception("Music command failed")
        mus = music_commands.Reply(f"Music control blew up: {exc}", "error", "error")
    if mus:
        memory.add_message(conversation_id, "assistant", mus.text, source="command")
        yield {"type": "done", "text": mus.text, "mood": mus.mood, "source": "command", "action": mus.action,
               "data": mus.data}
        return

    # 0b. Web research: search, read, summarise with numbered sources.
    req = None if mres else research.parse_request(user_text)
    if req:
        final: dict[str, Any] = {}
        for ev in research.run(req["topic"], client, req["depth"], req["papers"], conversation_id):
            if ev["type"] == "done":
                final = ev
            elif ev["type"] == "source":
                yield {"type": "state", "state": "thinking",
                       "detail": (f"Read {ev['site']}" if ev["ok"] else f"Skipped {ev['site']}") + "..."}
            elif ev["type"] in ("state", "token"):
                yield ev
        memory.add_message(conversation_id, "assistant", final["stored_text"], model=final.get("model") or None,
                           source="research")
        yield {k: v for k, v in final.items() if k != "stored_text"}
        return

    # 1. Deterministic commands.
    try:
        result = None if mres else intents.handle(user_text, client)
    except Exception as exc:  # a broken command must not kill the chat
        log.exception("Intent handling failed")
        result = intents.IntentResult(f"That command blew up on my side: {exc}", mood="error",
                                      action="error")
    task = {**mres.task, "fallback": mres.text, "action_name": mres.action, "data": mres.data} if mres else None
    if result and result.llm_task:
        task = result.llm_task
    elif result:
        memory.add_message(conversation_id, "assistant", result.text, source="command")
        yield {"type": "done", "text": result.text, "mood": result.mood, "source": "command",
               "action": result.action, "data": result.data}
        return

    # "Yes, do it" / "cancel that" → the latest pending action in this conversation.
    m = APPROVE_SAY.match(user_text.strip()) or REJECT_SAY.match(user_text.strip())
    # Only when my previous message was the one asking for approval, so a "yes" to something
    # else can't approve an older action.
    prev = [x for x in memory.get_messages(conversation_id, limit=3) if x["role"] == "assistant"]
    asked = bool(prev) and prev[-1]["source"] == "tool" and (
        "needs your OK" in prev[-1]["content"] or "until you approve" in prev[-1]["content"])
    if task is None and m and asked:
        pending = [a for a in actions.list_actions("pending", 20) if a["conversation_id"] == conversation_id]
        if pending:
            a = pending[0]
            if APPROVE_SAY.match(user_text.strip()):
                if a["risk"] == "danger":
                    text, mood, out = (f"That one's risky (**{a['preview']}**), so it needs a click on Approve, "
                                       "not just a word.", "curious", [a])
                else:
                    done = actions.approve(a["id"])
                    text, mood = _tool_reply(done)
                    out = [done]
            else:
                out = [actions.reject(a["id"])]
                text, mood = f"Cancelled: {a['preview']}.", "happy"
            memory.add_message(conversation_id, "assistant", text, source="tool")
            yield {"type": "done", "text": text, "mood": mood, "source": "tool", "action": "tool",
                   "actions": out, "data": {}}
            return

    # "Why did that fail?" → explain the last failed command/test output.
    if task is None and WHY_FAILED.search(user_text):
        failed = actions.last_failed_output()
        if failed:
            task = {"context": f"Output of the failed action \"{failed['preview']}\":\n---\n{failed['output'][-6000:]}\n---",
                    "instruction": f"{user_text}\n\n(Find the actual error in the output above, explain the cause "
                                   "in plain words and suggest the fix. Quote the key line.)",
                    "sources": [], "detail": "Reading the error output..."}

    # 2. Computer actions: clear phrasings first, then (for action-like requests) the model's tool calls.
    if task is None:
        route = None
        try:
            route = router.route(user_text, via=via, last_reply=last)
        except Exception:  # noqa: BLE001
            log.exception("Tool routing failed")
        proposals: list[dict[str, Any]] = []
        if route:
            if route.tool == "read_screen" or route.tool == "take_screenshot":
                if route.args.get("delay"):
                    yield {"type": "state", "state": "thinking",
                           "detail": f"Screenshot in {route.args['delay']:.0f}s: switch to the window you mean"}
            try:
                action = actions.request(route.tool, route.args, "command", conversation_id)
            except ValueError as exc:
                action = {"status": "failed", "summary": str(exc), "tool": route.tool, "output": "", "data": {},
                          "preview": route.tool, "risk": "safe"}
            if action["status"] == "completed" and action.get("followup"):
                task = _followup_task(action, route.question or user_text)
            else:
                text, mood = _tool_reply(action)
                memory.add_message(conversation_id, "assistant", text, source="tool")
                yield {"type": "done", "text": text, "mood": mood, "source": "tool", "action": "tool",
                       "actions": [action] if action.get("id") else [], "data": {}}
                return
        elif router.looks_like_action(user_text) and db.get_setting("model_tool_routing", True):
            model = str(db.get_setting("active_model"))
            yield {"type": "state", "state": "thinking", "model": model, "detail": "Working out what to do..."}
            for r in router.model_route(user_text, client, model):
                try:
                    proposals.append(actions.request(r.tool, r.args, "model", conversation_id))
                except ValueError as exc:
                    log.info("Model proposal rejected: %s", exc)
            proposals = [p for p in proposals if p.get("status") in ("pending", "blocked")]
            if proposals:
                lines = [f"- **{p['preview']}**" + (f": {p['summary']}" if p["status"] == "blocked" else "")
                         for p in proposals]
                text = ("Here's what I think you're asking for. Nothing happens until you approve:\n"
                        + "\n".join(lines))
                memory.add_message(conversation_id, "assistant", text, source="tool")
                yield {"type": "done", "text": text, "mood": "curious", "source": "tool", "action": "tool",
                       "actions": [p for p in proposals if p.get("id")], "data": {}}
                return

    # 2. Local LLM.
    model = str(db.get_setting("active_model"))
    settings = db.all_settings()
    detail = ""
    if task:
        detail = task.get("detail") or (f"Reading {task['sources'][0]['name']}..." if task.get("sources") else "")
    yield {"type": "state", "state": "thinking", "model": model, "detail": detail}
    parts: list[str] = []
    stats: dict[str, Any] = {}
    sources: list[dict[str, Any]] = []
    memory_offer = None if task else intents.suggest_memory(user_text)
    try:
        messages, sources = build_messages(conversation_id, user_text, client, task, memory_offer)
        options = {"temperature": float(settings["temperature"]),
                   # Changing num_ctx forces Ollama to reload the model, so it stays fixed.
                   "num_ctx": int(settings["num_ctx"]),
                   "num_predict": int(settings["max_reply_tokens"])}
        for chunk in client.chat_stream(model, messages, options=options,
                                        keep_alive=str(settings["keep_alive"])):
            if "token" in chunk:
                parts.append(chunk["token"])
                if not (task and task.get("forbid")):  # guarded replies are checked before anyone sees them
                    yield {"type": "token", "text": chunk["token"]}
            else:
                stats = chunk["stats"]
    except OllamaUnavailable:
        if task and task.get("fallback"):
            # Briefings and reviews are built from local data; only the phrasing needed the model.
            text = task["fallback"]
            memory.add_message(conversation_id, "assistant", text, source="command")
            yield {"type": "done", "text": text, "mood": "curious", "source": "command",
                   "action": task.get("action_name", ""), "data": task.get("data", {})}
            return
        text, sources = _offline_reply(user_text)
        memory.add_message(conversation_id, "assistant", text, source="offline")
        activity.record("chat", "Answered offline (Ollama unreachable)", "info")
        yield {"type": "done", "text": text, "mood": "confused", "source": "offline", "sources": sources}
        return
    except OllamaError as exc:
        msg = str(exc)
        if "not found" in msg.lower():
            msg = (f"The model '{model}' isn't installed. Pick an installed one in Settings > Models, "
                   f"or run: ollama pull {model}")
        activity.record("chat", f"Model error: {msg}", "failed")
        yield {"type": "error", "message": msg}
        return

    text = "".join(parts).strip()
    if task and task.get("forbid") and task.get("fallback") and any(
            re.search(p, text, re.I) for p in task["forbid"]):
        # The model claimed something the data contradicts (e.g. a deadline that doesn't exist).
        log.info("Replaced a briefing that contradicted the agenda: %s", text[:200])
        text = task["fallback"]
    if not text:
        text = "...I had a thought and lost it. Ask me again?"
    if parts:  # partial replies after an interruption are still saved
        memory.add_message(conversation_id, "assistant", text, model=model, source="llm")
    if task and not task.get("action"):
        activity.record("document", f"Summarised {sources[0]['name']}" if sources else "Ran a document task")
    yield {"type": "done", "text": text, "mood": intents.mood_for(user_text, text),
           "source": "llm", "model": model, "stats": stats, "sources": [s for s in sources if s.get("doc_id")],
           "suggest_memory": memory_offer,
           "actions": [task["action"]] if task and task.get("action") else []}
