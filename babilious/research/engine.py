"""Web research: search free sources, read the pages, and write a summary tied to numbered sources.

Honesty rules, enforced in code rather than hoped for from the model:
- every source shown was actually fetched and read in this run;
- citation numbers that don't match a source are removed;
- figures in the summary that appear in none of the pages are flagged as unverified;
- offline means "I'm offline", never a summary from memory dressed up as research.
"""
from __future__ import annotations

import json
import logging
import math
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Iterator

from .. import activity, db, persona
from ..llm import OllamaClient, OllamaError, OllamaUnavailable
from . import sources as src
from .sources import Hit, Offline

log = logging.getLogger(__name__)

DEPTHS = {"quick": {"sources": 3, "budget": 6000}, "normal": {"sources": 5, "budget": 6500},
          "deep": {"sources": 7, "budget": 8000}}
ACADEMIC = re.compile(r"\b(papers?|stud(?:y|ies)|research(?:ers)?|algorithms?|arxiv|academic|literature|survey|"
                      r"state of the art|sota|neural|deep learning|machine learning|transformer|radar|signal processing|"
                      r"quantum|theorem|model(?:s|ling)?)\b", re.I)
STOP = set("""a an the and or of to in on for with about from by at is are was were be been being it its this that these
those what which who whom how why when where do does did can could should would will i me my you your we our they their
tell explain research find out look up some any more most other into over than then also just vs versus between latest
news best good bad give show compare sources online web internet""".split())

REQUEST = [
    re.compile(r"^(?:please\s+)?(?:can you\s+|could you\s+)?(?P<depth>quick(?:ly)?|deep(?:ly)?|thorough(?:ly)?|in-depth)?\s*"
               r"(?:do\s+(?:some|a bit of|a|an)\s+)?(?:quick\s+|deep\s+|thorough\s+)?"
               r"(?:research|investigate)\s+(?:on\s+|about\s+|into\s+)?(?P<q>.{3,})$", re.I),
    re.compile(r"^(?:please\s+)?(?:look up|find out(?: about)?|read up on)\s+(?P<q>.{3,}?)\s+(?:online|on the (?:web|internet))$",
               re.I),
    re.compile(r"^(?:what does|what do)\s+(?:the (?:web|internet)|sources|people online)\s+say\s+(?:about|on)\s+(?P<q>.{3,})$",
               re.I),
    re.compile(r"^(?:compare|find)\s+sources\s+(?:on|about|for)\s+(?P<q>.{3,})$", re.I),
    re.compile(r"^what'?s the latest (?:news\s+)?(?:on|about|in|with)\s+(?P<q>.{3,})$", re.I),
    re.compile(r"^(?:find|search for|look for)\s+(?:research\s+)?papers?\s+(?:on|about)\s+(?P<q>.{3,})$", re.I),
]


def parse_request(text: str) -> dict[str, Any] | None:
    """'research mmWave radar for cars' → {"topic": ..., "depth": ..., "papers": bool} or None."""
    t = text.strip().rstrip("?.!")
    for i, pat in enumerate(REQUEST):
        m = pat.match(t)
        if not m:
            continue
        topic = re.sub(r"^(?:the|a|an)\s+", "", m.group("q").strip(" .,:;"), flags=re.I)
        topic = re.sub(r"\s+(?:for me|please|and summari[sz]e(?: it)?|and tell me(?: about it)?)$", "", topic, flags=re.I)
        if len(topic) < 3 or re.fullmatch(r"(it|this|that|them|more)", topic, re.I):
            return None
        depth_word = (m.groupdict().get("depth") or "").lower()
        if re.search(r"\b(quick|briefly)\b", t, re.I) or i in (1, 4):
            depth = "quick"
        elif depth_word.startswith(("deep", "thorough", "in-depth")) or re.search(r"\b(deep|thorough|in-depth)\b", t, re.I):
            depth = "deep"
        else:
            depth = "normal"
        papers = i == 5 or bool(re.search(r"\b(papers?|arxiv|academic|studies)\b", t, re.I))
        return {"topic": topic[:200], "depth": depth, "papers": papers}
    return None


# ---------------------------------------------------------------- passages
def terms(text: str) -> list[str]:
    out = []
    for w in re.findall(r"[a-z0-9][a-z0-9.+-]*", text.lower()):
        w = w.strip(".-")
        if len(w) >= 2 and w not in STOP:
            out.append(w[:-1] if len(w) > 4 and w.endswith("s") and not w.endswith("ss") else w)
    return out


def chunks(text: str, size: int = 550) -> list[str]:
    out, cur = [], ""
    for para in [p.strip() for p in re.split(r"\n+", text) if p.strip()]:
        if len(cur) + len(para) > size and cur:
            out.append(cur)
            cur = ""
        cur = (cur + " " + para).strip() if cur else para
        while len(cur) > size * 1.6:  # one huge paragraph: cut at a sentence end
            cut = cur.rfind(". ", 0, size) + 1 or size
            out.append(cur[:cut].strip())
            cur = cur[cut:].strip()
    if cur:
        out.append(cur)
    return out


def select_passages(topic: str, hits: list[Hit], budget: int) -> list[dict[str, Any]]:
    """Best-matching passages from each source, in page order, within a character budget.

    Returns one entry per *relevant* source: {"hit", "text", "score"}.
    """
    q = list(dict.fromkeys(terms(topic)))
    all_chunks = [(i, j, c, set(terms(c))) for i, h in enumerate(hits) for j, c in enumerate(chunks(h.text)[:80])]
    n = max(1, len(all_chunks))
    idf = {t: math.log(1 + n / (1 + sum(1 for *_, ts in all_chunks if t in ts))) for t in q}
    per = budget // max(1, len(hits))
    out = []
    for i, h in enumerate(hits):
        mine = [(j, c, ts) for ii, j, c, ts in all_chunks if ii == i]
        scored = []
        for j, c, ts in mine:
            s = sum(idf[t] for t in q if t in ts)
            s += 0.4 / (1 + j)  # the start of a page/abstract usually defines the subject
            scored.append((s, j, c))
        best = sorted(scored, reverse=True)
        if not best or (q and best[0][0] < max(0.5, 0.25 * sum(idf.values()))):
            continue  # nothing on this page is about the topic
        picked, used = [], 0
        for s, j, c in best:
            if used + len(c) > per and picked:
                continue
            picked.append((j, c))
            used += len(c)
            if used >= per:
                break
        text = " … ".join(c for _, c in sorted(picked))[:per + 200]
        out.append({"hit": h, "text": text, "score": round(best[0][0], 2)})
    return out


# ---------------------------------------------------------------- checks
CITE = re.compile(r"\[(\d+(?:\s*[,–-]\s*\d+)*)\]")
NUMBER = re.compile(r"(?<![\w.\[])(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)(\s?%|\s?GHz|\s?MHz|\s?km|\s?kg|\s?m\b)?(?![\w\]])")


def fix_citations(text: str, n_sources: int) -> tuple[str, set[int]]:
    """Drop citation numbers that don't match a real source; return the cleaned text and the ones used."""
    used: set[int] = set()

    def repl(m):
        nums = []
        for part in re.split(r"\s*,\s*", m.group(1)):
            rng = re.split(r"\s*[–-]\s*", part)
            vals = range(int(rng[0]), int(rng[-1]) + 1) if len(rng) == 2 and int(rng[-1]) - int(rng[0]) < 10 else [int(rng[0])]
            nums += [v for v in vals if 1 <= v <= n_sources]
        nums = sorted(set(nums))
        used.update(nums)
        return "".join(f"[{v}]" for v in nums)
    return re.sub(r"\s+(?=\[\])", "", CITE.sub(repl, text)).replace("[]", ""), used


def unverified_numbers(summary: str, corpus: str, topic: str = "") -> list[str]:
    """Figures in the summary that appear in none of the pages read (or the question)."""
    def norm(s: str) -> str:
        return s.replace(",", "")
    hay = norm(corpus) + " " + norm(topic)
    body = re.sub(r"^\s*\d+[.)]\s", "", summary, flags=re.M)  # list numbering isn't a claim
    body = CITE.sub(" ", body)
    found = []
    for m in NUMBER.finditer(body):
        num = norm(m.group(1))
        try:
            value = float(num)
        except ValueError:
            continue
        if value < 10 and "." not in num and not m.group(2):
            continue  # small counts ("three steps", "2 sources") aren't worth flagging
        if not re.search(rf"(?<![\d.]){re.escape(num)}(?![\d])", hay):
            found.append(m.group(0).strip())
    return list(dict.fromkeys(found))[:6]


# ---------------------------------------------------------------- storage
def save(topic: str, summary: str, used: list[dict[str, Any]], warnings: list[str], depth: str, model: str,
         conversation_id: int | None) -> int:
    with db.connect() as conn:
        rid = conn.execute("INSERT INTO research(topic, summary, sources, warnings, depth, model, conversation_id, "
                           "created_at) VALUES (?,?,?,?,?,?,?,?)",
                           (topic, summary, json.dumps(used), json.dumps(warnings), depth, model, conversation_id,
                            db.now())).lastrowid
    activity.record("research", f"Researched \"{topic[:80]}\" ({len(used)} sources)")
    return int(rid)


def _out(r: dict[str, Any]) -> dict[str, Any]:
    return {**r, "sources": json.loads(r["sources"] or "[]"), "warnings": json.loads(r["warnings"] or "[]")}


def get(rid: int) -> dict[str, Any]:
    with db.connect() as conn:
        r = conn.execute("SELECT * FROM research WHERE id = ?", (rid,)).fetchone()
    if not r:
        raise KeyError(rid)
    return _out(dict(r))


def list_research(query: str = "", limit: int = 50) -> list[dict[str, Any]]:
    sql, args = "SELECT * FROM research", []
    words = re.findall(r"[\w'-]{2,}", query.lower())
    if words:
        sql += " WHERE " + " AND ".join("LOWER(topic || ' ' || summary) LIKE ?" for _ in words)
        args = [f"%{w}%" for w in words]
    with db.connect() as conn:
        return [_out(r) for r in db.rows(conn.execute(sql + " ORDER BY id DESC LIMIT ?", (*args, limit)))]


def delete(rid: int) -> bool:
    with db.connect() as conn:
        return conn.execute("DELETE FROM research WHERE id = ?", (rid,)).rowcount > 0


def to_note(rid: int) -> dict[str, Any]:
    from ..manager import notes
    r = get(rid)
    refs = "\n".join(f"[{s['n']}] {s['title']} {s['url']}" for s in r["sources"])
    return notes.create(f"{r['summary']}\n\nSources:\n{refs}\n\n#research", title=f"Research: {r['topic']}"[:120])


def recent_for(conversation_id: int, minutes: int = 90) -> dict[str, Any] | None:
    """The latest research in this conversation, so follow-up questions have the context."""
    with db.connect() as conn:
        r = conn.execute("SELECT * FROM research WHERE conversation_id = ? AND created_at >= datetime('now', ?) "
                         "ORDER BY id DESC LIMIT 1", (conversation_id, f"-{minutes} minutes")).fetchone()
    return _out(dict(r)) if r else None


def with_sources(summary: str, used: list[dict[str, Any]], warnings: list[str]) -> str:
    """The text kept in chat history: summary, any warnings, then the numbered sources."""
    out = summary
    if warnings:
        out += "\n\n" + "\n".join(f"⚠️ {w}" for w in warnings)
    if used:
        out += "\n\nSources:\n" + "\n".join(f"[{s['n']}] {s['title']} {s['url']}" for s in used)
    return out


# ---------------------------------------------------------------- the run
INSTRUCTION = """Research question: {topic}

Write a clear research summary using ONLY the numbered sources above.
- Start with a 1-2 sentence direct answer, then 3-6 short bullet points with the key facts.
- Put the source number in square brackets after every fact, like [1] or [2][3].
- If sources disagree or a source looks like marketing, say so.
- If the sources don't answer part of the question, say that plainly instead of filling the gap.
- Don't add facts, numbers or names that aren't in the sources. Don't list the sources at the end (the app shows them).
- No preamble like "Here's a summary". Don't comment on these instructions."""


def _search(topic: str, depth: str, papers: bool) -> tuple[list[Hit], list[str]]:
    """Run the searches in parallel. Raises Offline only if every source failed to connect."""
    jobs = {"web": (src.search_web, 8), "wikipedia": (src.search_wikipedia, 1 if depth == "quick" else 2)}
    if papers or (depth != "quick" and ACADEMIC.search(topic)):
        jobs["arxiv"] = (src.search_arxiv, 3 if papers else 2)
    results: dict[str, list[Hit]] = {}
    errors: list[str] = []
    offline = 0
    with ThreadPoolExecutor(max_workers=3) as pool:
        futs = {name: pool.submit(fn, topic, n) for name, (fn, n) in jobs.items()}
        for name, fut in futs.items():
            try:
                results[name] = fut.result()
            except Offline:
                offline += 1
            except Exception as exc:  # noqa: BLE001
                log.info("%s search failed: %s", name, exc)
                errors.append(f"{name} search failed ({type(exc).__name__})")
    if offline == len(jobs):
        raise Offline("no connection")
    # Wikipedia's search returns loosely related articles; keep one only if its title is about the topic.
    q = set(terms(topic))
    results["wikipedia"] = [h for h in results.get("wikipedia", []) if q & set(terms(h.title.replace("(Wikipedia)", "")))]
    # Order: an encyclopedia overview, papers when asked, then the web, one page per site.
    ordered: list[Hit] = []
    ordered += results.get("wikipedia", [])[:1]
    if papers:
        ordered += results.get("arxiv", [])
    seen = {h.site for h in ordered}
    for h in results.get("web", []):
        if h.site in seen and h.site != "en.wikipedia.org":
            continue
        if h.site == "en.wikipedia.org" and any(x.url.lower() == h.url.lower() for x in ordered):
            continue
        seen.add(h.site)
        ordered.append(h)
    if not papers:
        ordered += results.get("arxiv", [])[:1]
    ordered += results.get("wikipedia", [])[1:]
    return ordered, errors


def run(topic: str, client: OllamaClient, depth: str = "normal", papers: bool = False,
        conversation_id: int | None = None) -> Iterator[dict[str, Any]]:
    """Yield progress events, streamed summary tokens, and a final "done" event."""
    depth = depth if depth in DEPTHS else "normal"
    want, budget = DEPTHS[depth]["sources"], DEPTHS[depth]["budget"]
    yield {"type": "state", "state": "thinking", "detail": "Searching DuckDuckGo, Wikipedia" +
           (" and arXiv" if papers or ACADEMIC.search(topic) else "") + "..."}
    try:
        candidates, errors = _search(topic, depth, papers)
    except Offline:
        yield {"type": "done", **offline_reply(topic)}
        return
    yield {"type": "progress", "stage": "search", "detail": f"Found {len(candidates)} candidate pages"}
    if not candidates:
        text = (f"I searched for \"{topic}\" and got nothing back worth reading. "
                + ("(" + "; ".join(errors) + ") " if errors else "") + "Try different words?")
        yield {"type": "done", "text": text, "stored_text": text, "mood": "confused", "source": "research",
               "action": "research_empty", "web_sources": [], "warnings": []}
        return

    # Read pages in parallel, keep the first good ones in ranking order.
    pool_hits = [h for h in candidates if not src.skip_reason(h.url)][:want + 4]
    yield {"type": "state", "state": "thinking", "detail": f"Reading {min(want, len(pool_hits))} pages..."}
    read: list[Hit] = []
    short: list[Hit] = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futs = [(h, pool.submit(src.fetch, h)) for h in pool_hits]
        for h, fut in futs:
            try:
                fut.result()
                if len(h.text) >= 300:
                    read.append(h)
                    yield {"type": "source", "title": h.title, "url": h.url, "site": h.site, "ok": True,
                           "chars": len(h.text)}
                else:
                    short.append(h)
            except Exception as exc:  # noqa: BLE001
                reason = getattr(getattr(exc, "response", None), "status_code", None)
                yield {"type": "source", "title": h.title, "url": h.url, "site": h.site, "ok": False,
                       "note": f"HTTP {reason}" if reason else str(exc)[:80]}
    if len(read) < want and short:  # JavaScript-only pages: render a couple in headless Edge
        for h in short[:2]:
            try:
                src.fetch_rendered(h)
                if len(h.text) >= 300:
                    read.append(h)
                    yield {"type": "source", "title": h.title, "url": h.url, "site": h.site, "ok": True,
                           "chars": len(h.text), "note": "rendered"}
            except Exception as exc:  # noqa: BLE001
                log.info("Rendered read failed for %s: %s", h.url, exc)
    read.sort(key=lambda h: candidates.index(h))
    picked = select_passages(topic, read, budget * 2)[:want]   # relevance first, then trim to the budget
    picked = select_passages(topic, [p["hit"] for p in picked], budget)
    if not picked:
        text = (f"I found pages for \"{topic}\" but none I could read actually covered it, so I won't pretend "
                "to summarise them. Try rephrasing, or open the search yourself.")
        yield {"type": "done", "text": text, "stored_text": text, "mood": "confused", "source": "research",
               "action": "research_empty", "web_sources": [], "warnings": []}
        return

    used = [{"n": i, "title": p["hit"].title or p["hit"].meta.get("page_title") or p["hit"].site,
             "url": p["hit"].url, "site": p["hit"].site, "kind": p["hit"].kind} for i, p in enumerate(picked, 1)]
    corpus = "\n".join(h.text for h in read)
    context = "\n\n".join(f"[{u['n']}] {u['title']} ({u['site']})\n{p['text']}" for u, p in zip(used, picked))

    settings = db.all_settings()
    model = str(settings["active_model"])
    messages = [{"role": "system", "content": persona.system_prompt(settings.get("user_name", "Abubakar"))},
                {"role": "system", "content": "Sources read just now from the web:\n\n" + context},
                {"role": "user", "content": INSTRUCTION.format(topic=topic)}]
    yield {"type": "state", "state": "thinking", "model": model,
           "detail": f"Read {len(used)} sources. Writing the summary..."}
    parts: list[str] = []
    stats: dict[str, Any] = {}
    try:
        for chunk in client.chat_stream(model, messages, options={
                "temperature": 0.3, "num_ctx": int(settings["num_ctx"]),
                "num_predict": max(int(settings["max_reply_tokens"]), 650)}, keep_alive=str(settings["keep_alive"])):
            if "token" in chunk:
                parts.append(chunk["token"])
                yield {"type": "token", "text": chunk["token"]}
            else:
                stats = chunk["stats"]
    except OllamaUnavailable:
        parts = [_unsummarised(picked, used)]
        model = ""
    except OllamaError as exc:
        text = f"I read {len(used)} sources but the model failed while writing: {exc}"
        yield {"type": "done", "text": text, "stored_text": with_sources(text, used, []), "mood": "error",
               "source": "research", "action": "research_failed", "web_sources": used, "warnings": []}
        return

    summary, cited = fix_citations("".join(parts).strip(), len(used))
    warnings = []
    if model:
        if not cited:
            warnings.append("He didn't mark which source each point came from, so check the sources below.")
        nums = unverified_numbers(summary, corpus, topic)
        if nums:
            warnings.append("These figures aren't in the pages I read, so treat them as unverified: "
                            + ", ".join(nums) + ".")
    rid = save(topic, summary, used, warnings, depth, model, conversation_id)
    if errors:
        log.info("Research partial errors: %s", errors)
    yield {"type": "done", "text": summary, "stored_text": with_sources(summary, used, warnings),
           "mood": "happy" if not warnings else "curious", "source": "research", "action": "research",
           "research_id": rid, "web_sources": used, "warnings": warnings, "model": model, "stats": stats}


def _unsummarised(picked: list[dict[str, Any]], used: list[dict[str, Any]]) -> str:
    lines = ["My brain (Ollama) is offline, so I can't summarise. Here's what each source says, unedited:"]
    for u, p in zip(used, picked):
        excerpt = p["text"][:320].rsplit(" ", 1)[0]
        lines.append(f"\n**[{u['n']}] {u['title']}**: {excerpt}...")
    return "\n".join(lines)


def offline_reply(topic: str) -> dict[str, Any]:
    """No internet. Say so, and offer what's on this PC instead."""
    from .. import indexer
    from ..manager import notes
    local = []
    for n in notes.search(topic, limit=3):
        local.append(f"- Note: **{n['title']}**")
    for d in indexer.retrieve_for_chat(topic, limit=3):
        local.append(f"- File: **{d['name']}**")
    for r in list_research(topic, limit=2):
        local.append(f"- Earlier research: **{r['topic']}**")
    text = f"I can't research \"{topic}\" right now: there's no internet connection, and I won't fake web results."
    if local:
        text += "\n\nHere's what I have on this PC that might help:\n" + "\n".join(local)
    else:
        text += " I don't have anything saved about it locally either."
    activity.record("research", f"Research offline: {topic[:80]}", "failed")
    return {"text": text, "stored_text": text, "mood": "confused", "source": "research", "action": "research_offline",
            "web_sources": [], "warnings": []}
