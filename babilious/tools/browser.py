"""Web: open sites in your browser, read pages, and drive a visible Edge window.

Playwright uses your installed Microsoft Edge (no extra browser download). The
automation window has its own profile in data/browser-profile, separate from
your normal browser. Clicking anything that submits, sends, buys, posts or
deletes always needs your approval.
"""
from __future__ import annotations

import logging
import queue
import re
import threading
import webbrowser
from concurrent.futures import Future
from typing import Any, Callable
from urllib.parse import quote_plus, urlparse

from .. import config
from . import Risk, Tool, ToolResult, register, safe

log = logging.getLogger(__name__)

SITES = {
    "github": "https://github.com", "gmail": "https://mail.google.com", "google": "https://www.google.com",
    "youtube": "https://www.youtube.com", "linkedin": "https://www.linkedin.com", "stackoverflow": "https://stackoverflow.com",
    "stack overflow": "https://stackoverflow.com", "wikipedia": "https://en.wikipedia.org", "maps": "https://maps.google.com",
    "google maps": "https://maps.google.com", "calendar": "https://calendar.google.com", "drive": "https://drive.google.com",
    "chatgpt": "https://chatgpt.com", "reddit": "https://www.reddit.com", "spotify": "https://open.spotify.com",
    "outlook": "https://outlook.live.com", "whatsapp": "https://web.whatsapp.com", "hugging face": "https://huggingface.co",
    "huggingface": "https://huggingface.co", "kaggle": "https://www.kaggle.com", "arxiv": "https://arxiv.org",
    "leetcode": "https://leetcode.com", "netflix": "https://www.netflix.com", "amazon": "https://www.amazon.com",
}
RISKY_CLICK = re.compile(r"\b(submit|send|post|publish|buy|order|pay|checkout|purchase|confirm|delete|remove|"
                         r"sign ?up|register|apply|transfer|tweet|share|unsubscribe|accept)\b", re.I)
SENSITIVE_FIELD = re.compile(r"pass(word)?|card|cvv|cvc|iban|ssn|account number|pin\b|otp|security code", re.I)


def normalise_url(target: str) -> str:
    t = target.strip().strip("<>\"'").rstrip(".,!?")
    if t.lower() in SITES:
        return SITES[t.lower()]
    if re.match(r"https?://", t, re.I):
        return t
    if re.match(r"^[a-z][a-z0-9+.-]*:(?!\d)", t, re.I):
        return t  # another scheme (file:, javascript:, ms-...): returned as-is so the risk check blocks it
    if re.fullmatch(r"[\w-]+(\.[\w-]+)+(/\S*)?", t):
        return "https://" + t
    return "https://duckduckgo.com/?q=" + quote_plus(t)  # free, no tracking-heavy search


def _web_risk(url: str) -> Risk:
    scheme = urlparse(url).scheme.lower()
    if scheme not in ("http", "https"):
        return Risk("blocked", "Only web addresses (http/https) can be opened this way.")
    return Risk("safe")


# ---------------------------------------------------------------- open in your own browser
def _open_url(args: dict[str, Any]) -> ToolResult:
    url = normalise_url(args["url"])
    browser = (args.get("browser") or "").lower()
    if browser in ("chrome", "edge", "firefox"):
        from .apps import APPS, available, launch
        target, label = APPS[browser]
        if not available(target):
            return ToolResult(False, f"{label} isn't installed.")
        launch(target, [url])
        return ToolResult(True, f"Opened {url} in {label}.", data={"url": url})
    webbrowser.open(url)
    return ToolResult(True, f"Opened {url} in your browser.", data={"url": url})


register(Tool("open_url", "Open a website or web search in the user's browser.",
              {"url": {"type": "string", "description": "URL, site name (github, gmail...) or search words"},
               "browser": {"type": "string", "enum": ["", "chrome", "edge", "firefox"]}},
              ["url"], _open_url,
              lambda a: f"Open {normalise_url(a.get('url', ''))}" + (f" in {a['browser'].title()}" if a.get("browser") else ""),
              lambda a: _web_risk(normalise_url(a.get("url", "")))))


# ---------------------------------------------------------------- Playwright worker (one thread owns it)
class _Worker:
    """Playwright's sync API must stay on one thread, so all browser work is queued here."""

    def __init__(self):
        self.jobs: queue.Queue = queue.Queue()
        self.thread: threading.Thread | None = None
        self.pw = None
        self.headless_browser = None
        self.session = None   # visible persistent context
        self.page = None

    def call(self, fn: Callable[["_Worker"], Any], timeout: float = 90) -> Any:
        if self.thread is None or not self.thread.is_alive():
            self.thread = threading.Thread(target=self._loop, daemon=True, name="playwright")
            self.thread.start()
        fut: Future = Future()
        self.jobs.put((fn, fut))
        return fut.result(timeout=timeout)

    def _loop(self):
        from playwright.sync_api import sync_playwright
        self.pw = sync_playwright().start()
        while True:
            fn, fut = self.jobs.get()
            try:
                fut.set_result(fn(self))
            except Exception as exc:  # noqa: BLE001
                fut.set_exception(exc)

    def headless(self):
        if self.headless_browser is None or not self.headless_browser.is_connected():
            self.headless_browser = _launch(self.pw.chromium, headless=True)
        return self.headless_browser

    def visible_page(self):
        if self.session is None or self.page is None or self.page.is_closed():
            profile = config.DATA_DIR / "browser-profile"
            profile.mkdir(parents=True, exist_ok=True)
            self.session = _launch_persistent(self.pw.chromium, str(profile))
            self.page = self.session.pages[0] if self.session.pages else self.session.new_page()
        return self.page


def _launch(chromium, headless: bool):
    for channel in ("msedge", "chrome", None):
        try:
            return chromium.launch(channel=channel, headless=headless) if channel else chromium.launch(headless=headless)
        except Exception as exc:  # noqa: BLE001
            last = exc
    raise RuntimeError(f"No browser available for automation (Edge/Chrome not found): {last}")


def _launch_persistent(chromium, profile: str):
    for channel in ("msedge", "chrome"):
        try:
            return chromium.launch_persistent_context(profile, channel=channel, headless=False, no_viewport=True)
        except Exception as exc:  # noqa: BLE001
            last = exc
    raise RuntimeError(f"Couldn't open an automation window: {last}")


WORKER = _Worker()

EXTRACT_JS = """() => {
  const pick = document.querySelector('main, article, [role=main]') || document.body;
  const clone = pick.cloneNode(true);
  clone.querySelectorAll('script,style,noscript,nav,footer,header,aside,form,svg').forEach(e => e.remove());
  const links = [...document.querySelectorAll('a[href]')].map(a => [a.innerText.trim(), a.href])
    .filter(([t, h]) => t && h.startsWith('http')).slice(0, 25);
  return {title: document.title, text: clone.innerText, links};
}"""


def _page_text(page) -> dict[str, Any]:
    data = page.evaluate(EXTRACT_JS)
    text = re.sub(r"\n{3,}", "\n\n", data["text"] or "").strip()
    return {"title": data["title"], "text": text, "links": data["links"], "url": page.url}


def _read_webpage(args: dict[str, Any]) -> ToolResult:
    url = normalise_url(args["url"])

    def job(w: _Worker):
        ctx = w.headless().new_context()
        try:
            page = ctx.new_page()
            page.goto(url, timeout=25_000, wait_until="domcontentloaded")
            page.wait_for_timeout(800)  # let client-side pages render a little
            return _page_text(page)
        finally:
            ctx.close()
    data = WORKER.call(job)
    text = data["text"][:15_000]
    if not text:
        return ToolResult(False, f"{url} loaded but had no readable text.")
    links = "\n".join(f"- {t[:80]}: {h}" for t, h in data["links"][:15])
    return ToolResult(True, f"Read \"{data['title'] or url}\" ({len(data['text']):,} characters).",
                      f"Title: {data['title']}\nURL: {data['url']}\n\n{text}\n\nLinks:\n{links}",
                      {"url": data["url"], "title": data["title"]})


register(Tool("read_webpage", "Load a public web page (headless) and read its text, to summarise or answer questions.",
              {"url": {"type": "string"}}, ["url"], _read_webpage,
              lambda a: f"Read the page {normalise_url(a.get('url', ''))}",
              lambda a: _web_risk(normalise_url(a.get("url", ""))), followup=True))


# ---------------------------------------------------------------- visible automation window
def _goto(args: dict[str, Any]) -> ToolResult:
    url = normalise_url(args["url"])

    def job(w: _Worker):
        page = w.visible_page()
        page.goto(url, timeout=30_000, wait_until="domcontentloaded")
        page.bring_to_front()
        return page.title()
    title = WORKER.call(job)
    return ToolResult(True, f"Opened {url} in the automation window ({title}).", data={"url": url})


register(Tool("web_goto", "Open a URL in Babilious' visible automation browser window (for filling/clicking later).",
              {"url": {"type": "string"}}, ["url"], _goto,
              lambda a: f"Open {normalise_url(a.get('url', ''))} in the automation window",
              lambda a: _web_risk(normalise_url(a.get("url", "")))))


def _find_field(page, field: str):
    candidates = [page.get_by_label(field, exact=False), page.get_by_placeholder(field, exact=False),
                  page.get_by_role("textbox", name=field), page.get_by_role("searchbox", name=field),
                  page.get_by_role("combobox", name=field), page.locator(f"[name='{field}' i]")]
    if field.lower() in ("search", "search box", "the search box"):
        candidates.insert(0, page.get_by_role("searchbox"))
        candidates.append(page.locator("input[type=search], input[name=q]"))
    for loc in candidates:
        try:
            if loc.count() and loc.first.is_visible():
                return loc.first
        except Exception:  # noqa: BLE001
            continue
    return None


def _fill_risk(args: dict[str, Any]) -> Risk:
    if SENSITIVE_FIELD.search(args.get("field", "")):
        return Risk("blocked", "I don't type passwords, card numbers or codes; enter those yourself.")
    return Risk("safe")


def _fill(args: dict[str, Any]) -> ToolResult:
    field, value = args["field"], args.get("value", "")

    def job(w: _Worker):
        if w.page is None or w.page.is_closed():
            raise RuntimeError("No automation window is open. Ask me to open a page first.")
        loc = _find_field(w.page, field)
        if loc is None:
            return False
        loc.fill(value, timeout=5_000)
        return True
    if not WORKER.call(job):
        return ToolResult(False, f"I couldn't find a field called \"{field}\" on the page.")
    return ToolResult(True, f"Typed into \"{field}\". Nothing has been submitted.")


register(Tool("web_fill", "Type into a field in the automation window (never submits).",
              {"field": {"type": "string", "description": "Field label, placeholder or name"},
               "value": {"type": "string"}}, ["field", "value"], _fill,
              lambda a: f"Type \"{a.get('value', '')[:60]}\" into \"{a.get('field')}\"", _fill_risk))


def _click_risk(args: dict[str, Any]) -> Risk:
    target = args.get("target", "")
    if RISKY_CLICK.search(target):
        return Risk("danger", f"Clicking \"{target}\" could submit, send, buy or delete something.")
    return Risk("confirm", "Clicks on a web page.")


def _click(args: dict[str, Any]) -> ToolResult:
    target = args["target"]

    def job(w: _Worker):
        if w.page is None or w.page.is_closed():
            raise RuntimeError("No automation window is open.")
        page = w.page
        for loc in (page.get_by_role("button", name=target), page.get_by_role("link", name=target),
                    page.get_by_text(target, exact=False)):
            try:
                if loc.count() and loc.first.is_visible():
                    loc.first.click(timeout=5_000)
                    page.wait_for_load_state("domcontentloaded", timeout=15_000)
                    return page.url
            except Exception:  # noqa: BLE001
                continue
        return None
    url = WORKER.call(job)
    if not url:
        return ToolResult(False, f"I couldn't find \"{target}\" to click.")
    return ToolResult(True, f"Clicked \"{target}\" (now at {url}).", data={"url": url})


register(Tool("web_click", "Click a button or link (by its text) in the automation window.",
              {"target": {"type": "string"}}, ["target"], _click,
              lambda a: f"Click \"{a.get('target')}\" in the automation window", _click_risk))


def _web_read(_args: dict[str, Any]) -> ToolResult:
    def job(w: _Worker):
        if w.page is None or w.page.is_closed():
            raise RuntimeError("No automation window is open.")
        return _page_text(w.page)
    data = WORKER.call(job)
    return ToolResult(True, f"Read the automation window: \"{data['title']}\".",
                      f"Title: {data['title']}\nURL: {data['url']}\n\n{data['text'][:15_000]}", {"url": data["url"]})


register(Tool("web_read", "Read the text of the page open in the automation window.", {}, [], _web_read,
              lambda a: "Read the automation window", safe, followup=True))
