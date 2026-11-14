"""Deterministic commands handled before the LLM.

Small local models are unreliable at tool calling, so clear commands
(switch model, add a task, remember something) are matched here and executed
directly. Requests for abilities that are not built yet get an honest answer
instead of letting the model pretend it did something.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from . import activity, memory, models, tasks
from .llm import OllamaClient, OllamaError, OllamaUnavailable

NAME_PREFIX = re.compile(r"^\s*(hey\s+|ok(ay)?\s+)?babilious[\s,:!.-]*", re.I)


@dataclass
class IntentResult:
    text: str
    mood: str = "happy"
    action: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    # When set, the assistant runs the LLM on this task instead of replying with `text`.
    llm_task: dict[str, Any] | None = None


def _clean(text: str) -> str:
    return NAME_PREFIX.sub("", text.strip()).strip()


# --------------------------------------------------------------- not built yet
# Abilities that are planned but not built get an honest "not yet" here instead of a model guess.
# Everything from the original plan exists as of Milestone 6, so the list is empty.
PENDING: list[tuple[re.Pattern[str], str, int]] = []


def _pending(text: str) -> IntentResult | None:
    for pattern, ability, milestone in PENDING:
        if pattern.search(text):
            activity.record("request", f"Asked for '{ability}' (not built yet): {text[:80]}", "suggested")
            return IntentResult(
                f"I'd love to, but I can't do that yet. {ability} arrives in Milestone {milestone}, "
                "and I'd rather tell you that than pretend. Meanwhile I can open a web search, "
                "manage tasks, or read a page you point me at.",
                mood="confused", action="unsupported", data={"ability": ability, "milestone": milestone},
            )
    return None


# --------------------------------------------------------------- models
MODEL_SWITCH = re.compile(r"\b(switch|change|swap|use|go)\b.*\b(model|to\s+\S+)", re.I)
MODEL_QUERY = re.compile(r"\b(which|what)\s+model\b|\bmodel\s+are\s+you\b", re.I)


def _model_intent(text: str, client: OllamaClient) -> IntentResult | None:
    if MODEL_QUERY.search(text) and not re.search(r"\b(switch|change|use)\b", text, re.I):
        return IntentResult(
            f"I'm running on {models.active_model()} right now. Same dog, different engine.",
            mood="curious", action="model_query")
    mentions_model = "model" in text.lower() or re.search(
        r"\b(llama|qwen|gemma|phi|mistral|deepseek|coder)", text, re.I)
    if not (MODEL_SWITCH.search(text) and mentions_model):
        return None
    try:
        target, reason = models.resolve_request(client, text)
    except OllamaUnavailable:
        return IntentResult("I can't switch models: Ollama isn't running. Start it with `ollama serve` "
                            "and try again.", mood="error", action="model_switch_failed")
    if not target:
        hints = {
            "already on the lightest installed model": "I'm already on the lightest model you have installed. "
            "If you want something faster, download llama3.2:1b from Settings > Models.",
            "already on the strongest installed model": "I'm already on the strongest model you have installed.",
            "no coding model installed": "You don't have a coding model installed. qwen2.5-coder:3b is a good "
            "one; you can download it from Settings > Models.",
        }
        return IntentResult(hints.get(reason, "Which model? Tell me its name, or say 'faster' or 'smarter'. "
                                      "The list is in Settings > Models."),
                            mood="confused", action="model_switch_failed")
    if target == models.active_model():
        return IntentResult(f"I'm already running on {target}.", mood="curious", action="model_switch")
    try:
        name = models.set_active(client, target)
    except (ValueError, OllamaError, OllamaUnavailable) as exc:
        activity.record("model", f"Model switch to {target} failed: {exc}", "failed")
        return IntentResult(f"That didn't work: {exc}", mood="error", action="model_switch_failed")
    flavour = {
        "faster": "We may sacrifice a little reasoning depth, but at least I won't keep your laptop "
                  "busy all afternoon.",
        "stronger": "More brains, slower paws. Worth it for the hard problems.",
        "coding": "Builder Mode engaged. Show me the code.",
    }.get(reason, "Personality unchanged, obviously.")
    return IntentResult(f"Done. Switched to {name}. {flavour}", mood="excited",
                        action="model_switch", data={"model": name})


# --------------------------------------------------------------- tasks
TASK_CREATE = re.compile(
    r"^(?:please\s+)?(?:create|add|make|new)\s+(?:a\s+|an\s+|new\s+)?(?:task|todo|to-do)\s*"
    r"(?:to|:|-|for|called|named)?\s+(?P<title>.+)$", re.I)
TASK_LIST = re.compile(
    r"\b(what'?s|what is|show( me)?|list)\s+(unfinished|left|pending|open|my\s+(open\s+)?tasks|on my (list|plate))"
    r"|\bmy (open\s+)?tasks\b|\bwhat do i (have|need) to do\b", re.I)


def parse_task_details(raw: str) -> tuple[str, str | None, int, str]:
    """'finish the portfolio by friday, high priority, for the job hunt project'
    → ('Finish the portfolio', '2026-11-20', 1, 'Job hunt')."""
    from .manager.timeparse import parse
    text = raw.strip().rstrip(".!")
    priority = 2
    if re.search(r"\b(high priority|urgent|asap|important|priority 1)\b", text, re.I):
        priority = 1
    elif re.search(r"\b(low priority|whenever|someday|no rush|priority 3)\b", text, re.I):
        priority = 3
    text = re.sub(r",?\s*\b(?:high|low) priority\b|,?\s*\b(?:urgent|asap|no rush|whenever|someday)\b|,?\s*\bpriority [123]\b",
                  "", text, flags=re.I)
    project = ""
    pm = re.search(r",?\s+(?:for|in|under)\s+(?:the\s+|my\s+)?(?P<p>[\w' .&-]{2,40}?)\s+project\b", text, re.I)
    if pm:
        project = pm.group("p").strip()
        project = project[0].upper() + project[1:]
        text = text[:pm.start()] + text[pm.end():]
    due = None
    dm = re.search(r",?\s+(?:by|due|before|on|for|until)\s+(?P<d>.+)$", text, re.I)
    if dm:
        w = parse(dm.group("d"))
        if w is not None and not w.recurrence and len(w.rest.split()) <= 1:
            due = w.at.date().isoformat()
            text = text[:dm.start()]
    elif not re.search(r"\b(?:from|of|since|last|about|after)\s+(?:the\s+)?\S+(?:\s+\S+)?$", text, re.I):
        # "...tomorrow" at the end without "by" (but "notes from monday" is a past reference, not a deadline)
        w = parse(text)
        if w is not None and not w.recurrence and w.rest and len(w.rest) < len(text) - 3:
            due = w.at.date().isoformat()
            text = w.rest
    title = " ".join(text.split()).strip(" ,.;-")
    return (title[0].upper() + title[1:] if title else ""), due, priority, project


def _task_intent(text: str) -> IntentResult | None:
    m = TASK_CREATE.match(text)
    if m:
        title, due, priority, project = parse_task_details(m.group("title"))
        if not title:
            return IntentResult("A task about what?", mood="confused", action="task_unclear")
        task_id = tasks.create_task(title, priority=priority, project=project, due_date=due)
        activity.record("task", f"Created task: {title}")
        extras = []
        if due:
            from datetime import date as _date
            extras.append(f"due {_date.fromisoformat(due):%a %d %b}")
        if priority == 1:
            extras.append("high priority")
        if project:
            extras.append(f"in {project}")
        return IntentResult(f"Added to your list: \"{title}\"" + (f" ({', '.join(extras)})" if extras else "") +
                            ". I'll be watching.", mood="happy", action="task_created", data={"task_id": task_id})
    if TASK_LIST.search(text):
        open_ = tasks.open_tasks()
        if not open_:
            return IntentResult("Nothing unfinished. Either you're remarkably productive, or you "
                                "haven't told me anything. Suspicious either way.",
                                mood="curious", action="task_list")
        lines = "\n".join(
            f"- {t['title']}" + (f" (due {t['due_date']})" if t["due_date"] else "")
            + (" [in progress]" if t["status"] == "in_progress" else "")
            for t in open_[:10])
        more = f"\n...and {len(open_) - 10} more in Tasks." if len(open_) > 10 else ""
        return IntentResult(f"Here's what's still open:\n{lines}{more}", mood="curious",
                            action="task_list")
    return None


# --------------------------------------------------------------- memory
REMEMBER = re.compile(r"^(?:please\s+)?remember(?:\s+that|:)\s+(?P<fact>.+)$", re.I)


def _memory_intent(text: str) -> IntentResult | None:
    m = REMEMBER.match(text)
    if not m:
        return None
    fact = m.group("fact").strip().rstrip(".")
    kind = "preference" if re.search(r"\b(i (like|love|prefer|hate|dislike))\b", fact, re.I) else "fact"
    memory.add_memory(fact, kind=kind, source="chat")
    activity.record("memory", f"Saved memory: {fact[:80]}")
    return IntentResult("Noted. It's in my long-term memory now; you can view or delete it "
                        "under Second Brain.", mood="happy", action="memory_saved")


# --------------------------------------------------------------- second brain (files)
EXT_WORDS = {
    "pdf": ".pdf", "pdfs": ".pdf", "word": ".docx", "docx": ".docx", "python": ".py", "py": ".py",
    "markdown": ".md", "md": ".md", "text": ".txt", "txt": ".txt", "javascript": ".js", "js": ".js",
    "typescript": ".ts", "matlab": ".m", "notebook": ".ipynb", "notebooks": ".ipynb", "jupyter": ".ipynb",
    "powerpoint": ".pptx", "slides": ".pptx", "excel": ".xlsx .csv", "spreadsheet": ".xlsx .csv",
    "spreadsheets": ".xlsx .csv", "csv": ".csv", "image": ".png .jpg .jpeg", "images": ".png .jpg .jpeg",
    "photo": ".png .jpg .jpeg", "photos": ".png .jpg .jpeg", "html": ".html .htm", "verilog": ".v .sv",
}
FILE_WORDS = r"(files?|pdfs?|documents?|docs?|docx|notes?|notebooks?|slides|spreadsheets?|images?|photos?|scripts?)"
FILE_FIND = re.compile(
    r"^(?:please\s+)?(?:can you\s+)?(?:find|search(?:\s+for)?|look\s+for|locate|show(?:\s+me)?|list|where(?:'s|\s+is|\s+are))\s+"
    r"(?P<q>.+)$", re.I)
SUMMARISE = re.compile(
    r"^(?:please\s+)?(?:can you\s+)?(?:summari[sz]e|give me a summary of|tl;?dr( of)?)\s+(?P<q>.+)$", re.I)
INDEX_PATH = re.compile(r"^(?:please\s+)?(?:index|add)\s+(?:the\s+)?(?:folder\s+)?(?P<path>[a-zA-Z]:\\[^\"*?<>|]+?)"
                        r"(?:\s+to (?:my )?(?:second brain|index))?$", re.I)
INDEX_VAGUE = re.compile(r"\b(index|scan)\s+(my\s+|the\s+|a\s+)?(\w+\s+)?(folder|files|documents|notes)\b", re.I)


def _when(ts_iso: str) -> str:
    return ts_iso[:10]


def _file_intent(text: str) -> IntentResult | None:
    from . import indexer  # local import keeps intents importable without the indexer

    m = INDEX_PATH.match(text)
    if m:
        try:
            folder = indexer.add_folder(m.group("path"))
        except ValueError as exc:
            return IntentResult(f"Can't index that: {exc}", mood="confused", action="index_failed")
        indexer.start_indexing(folder["id"])
        return IntentResult(f"Added {folder['path']} and started indexing it in the background. "
                            "Progress is in Files.", mood="excited", action="index_started", data=folder)
    if INDEX_VAGUE.search(text) and not FILE_FIND.match(text):
        return IntentResult("Point me at it: open Files and press \"Add folder\" (or say \"index C:\\path\\to\\folder\"). "
                            "I only read folders you pick.", mood="curious", action="index_help")

    m = SUMMARISE.match(text)
    if m:
        q = m.group("q").strip().rstrip("?.!")
        doc = indexer.find_document(q)
        if not doc:
            if indexer.list_folders() and re.search(FILE_WORDS + r"|\.\w{2,4}\b", q, re.I):
                return IntentResult(f"I couldn't find a document matching \"{q}\" in your indexed folders. "
                                    "Check the name in Files.", mood="confused", action="summary_not_found")
            return None  # e.g. "summarise this paragraph: ..." goes to the model as normal chat
        full = indexer.document_text(doc["id"])
        limit = 5000  # ~1300 tokens: fits a 4096-token context with the persona
        body = full[:limit]
        note = "" if len(full) <= limit else f"\n[Only the first {limit} of {len(full)} characters fit; say so.]"
        return IntentResult("", mood="happy", action="summary", data={"doc_id": doc["id"]},
                            llm_task={
                                "context": f"Document: {doc['name']} ({doc['path']})\n---\n{body}\n---{note}",
                                "instruction": (f"Summarise the document {doc['name']} above in at most 6 short bullet "
                                                "points, then one line on what matters most. Use only the document."),
                                "sources": [{"doc_id": doc["id"], "name": doc["name"], "path": doc["path"]}],
                            })

    m = FILE_FIND.match(text)
    if not m or not re.search(FILE_WORDS + r"|\b(contain|containing|named|called)\b", text, re.I):
        return None
    q = m.group("q").lower().rstrip("?.!")
    if not indexer.list_folders():
        return IntentResult("I haven't indexed any folders yet, so I'd be guessing. Add one in Files and ask again.",
                            mood="confused", action="file_search_empty")
    sort = "modified" if re.search(r"\b(latest|newest|most recent|recent|last)\b", q) else "relevance"
    exts = " ".join(dict.fromkeys(e for w in re.findall(r"[a-z]+", q) if w in EXT_WORDS for e in EXT_WORDS[w].split()))
    place = re.search(r"\bin (?:my |the )?([\w ]+?) folder\b|\bin (?:my |the )?(documents|downloads|desktop|pictures)\b", q)
    path_contains = (place.group(1) or place.group(2)).strip() if place else None
    content = re.search(r"\b(?:containing|that (?:contain|mention)s?|mentioning|about|with)\s+(.+)$", q)
    named = re.search(r"\b(?:named|called)\s+(.+)$", q)
    if content:
        query = content.group(1)
    elif named:
        query = named.group(1)
    else:
        noise = r"\b(the|my|a|an|all|any|latest|newest|most|recent|last|in|folder|of|for|me|files?|documents?|docs?)\b"
        query = re.sub(noise, " ", q)
        for w in EXT_WORDS:
            query = re.sub(rf"\b{w}\b", " ", query)
        if path_contains:
            query = query.replace(path_contains.lower(), " ")
    query = query.strip(" \"'")
    results = indexer.search(query, ext=exts or None, sort=sort, path_contains=path_contains, limit=8)
    if not results:
        what = " ".join(filter(None, [exts, f'"{query}"' if query else "", f"in {path_contains}" if path_contains else ""]))
        return IntentResult(f"Nothing in your indexed folders matches {what or 'that'}.", mood="confused",
                            action="file_search", data={"results": []})
    lines = []
    for r in results[:6]:
        lines.append(f"- **{r['name']}** ({_when(r['modified'])})\n  `{r['path']}`")
    more = f"\n...{len(results) - 6} more in Files." if len(results) > 6 else ""
    lead = "Newest first:" if sort == "modified" else "Best matches:"
    activity.record("search", f"File search: {text[:80]} ({len(results)} results)")
    return IntentResult(f"{lead}\n" + "\n".join(lines) + more +
                        "\n\nI can't open files for you until Milestone 4, but the paths are right there.",
                        mood="curious", action="file_search",
                        data={"results": [{"id": r["id"], "name": r["name"], "path": r["path"]} for r in results]})


# --------------------------------------------------------------- memory suggestions
FACT_PATTERNS = [
    re.compile(r"\b(i am|i'm|im)\s+(a|an|studying|working|from|based|learning|applying|planning|building|doing)\b", re.I),
    re.compile(r"\bmy\s+(name|birthday|favou?rite|goal|major|degree|university|job|thesis|supervisor|research|"
               r"girlfriend|boyfriend|wife|husband|sister|brother|mother|father|mom|dad)\b[^.?!]*\b(is|are|was)\b", re.I),
    re.compile(r"\bi\s+(like|love|prefer|hate|dislike|enjoy|work at|work on|study|live in|want to become)\b", re.I),
]


def suggest_memory(user_text: str) -> str | None:
    """A personal fact worth offering to remember. Never saved without the user's click."""
    if user_text.strip().endswith("?") or REMEMBER.match(_clean(user_text)):
        return None
    for sentence in re.split(r"(?<=[.!])\s+|\n", user_text):
        s = sentence.strip().rstrip(".!")
        if 8 <= len(s) <= 200 and any(p.search(s) for p in FACT_PATTERNS):
            if memory.list_memories(s[:60]):
                return None
            return f'User said: "{s}"'
    return None


def handle(text: str, client: OllamaClient) -> IntentResult | None:
    cleaned = _clean(text)
    if not cleaned:
        return None
    for fn in (_memory_intent, _task_intent, _file_intent):
        result = fn(cleaned)
        if result:
            return result
    result = _model_intent(cleaned, client)
    if result:
        return result
    return _pending(cleaned)


# --------------------------------------------------------------- expressions
def mood_for(user_text: str, reply: str) -> str:
    """Cheap heuristic for the puppy's expression after a reply."""
    u = user_text.lower()
    if re.search(r"\b(annoying|stupid|dumb|useless|shut up|hate you)\b", u):
        return "annoyed"
    if re.search(r"\b(thanks|thank you|good boy|love you|awesome|great job|nice)\b", u):
        return "happy"
    if re.search(r"\b(i don'?t know|not sure|no idea|can'?t tell)\b", reply.lower()):
        return "confused"
    if u.count("!") >= 2 or re.search(r"\b(amazing|excited|let'?s go|woo)\b", u):
        return "excited"
    if "?" in reply[-40:]:
        return "curious"
    return "happy"
