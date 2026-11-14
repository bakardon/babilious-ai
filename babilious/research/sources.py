"""Free sources for web research: DuckDuckGo (HTML results), Wikipedia and arXiv APIs, plus page reading.

No API keys, no paid services. Pages are fetched with plain HTTP and their main text is
extracted locally; pages that only render with JavaScript fall back to headless Edge.
"""
from __future__ import annotations

import html
import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import requests

log = logging.getLogger(__name__)

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/130.0 Safari/537.36",
      "Accept-Language": "en-GB,en;q=0.9"}
TIMEOUT = (5, 12)
MAX_BYTES = 2_500_000
# Pages that are video, social or login walls: their text isn't readable without an account or JS.
SKIP_DOMAINS = ("youtube.com", "youtu.be", "facebook.com", "instagram.com", "tiktok.com", "twitter.com", "x.com",
                "pinterest.", "linkedin.com", "quora.com", "amazon.", "ebay.", "duckduckgo.com")


class Offline(Exception):
    """No internet: every source failed to connect."""


@dataclass
class Hit:
    title: str
    url: str
    snippet: str = ""
    kind: str = "web"          # web | wikipedia | arxiv
    text: str = ""             # filled for API sources (abstract / extract) or after reading
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def site(self) -> str:
        host = urlparse(self.url).netloc.lower()
        return host[4:] if host.startswith("www.") else host


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", s or ""))).strip()


# ---------------------------------------------------------------- search
_ddg_blocked_until = 0.0


def search_web(query: str, limit: int = 8) -> list[Hit]:
    global _ddg_blocked_until
    import time
    if time.time() < _ddg_blocked_until:
        return search_web_rendered(query, limit)
    try:
        r = requests.post("https://html.duckduckgo.com/html/", data={"q": query, "kl": "wt-wt"}, headers=UA,
                          timeout=TIMEOUT)
    except requests.ConnectionError as exc:
        raise Offline(str(exc)) from exc
    if r.status_code == 202 or "result__a" not in r.text:
        # DuckDuckGo answers bursts of automated queries with a challenge page: use a real browser instead.
        log.info("DuckDuckGo returned %s without results; falling back to Bing in headless Edge", r.status_code)
        _ddg_blocked_until = time.time() + 1800
        return search_web_rendered(query, limit)
    r.raise_for_status()
    hits: list[Hit] = []
    blocks = re.split(r'<div class="result results_links', r.text)[1:]
    for b in blocks:
        if "result--ad" in b[:200]:
            continue
        a = re.search(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', b, re.S)
        if not a:
            continue
        url = html.unescape(a.group(1))
        if url.startswith("//duckduckgo.com/l/") or "duckduckgo.com/l/?" in url:  # redirect wrapper
            url = unquote(parse_qs(urlparse(url).query).get("uddg", [""])[0])
        if not url.startswith("http"):
            continue
        sn = re.search(r'class="result__snippet"[^>]*>(.*?)</a>', b, re.S)
        hits.append(Hit(_clean(a.group(2)), url, _clean(sn.group(1)) if sn else ""))
        if len(hits) >= limit:
            break
    return hits


BING_JS = """() => [...document.querySelectorAll('li.b_algo')].map(li => {
  const a = li.querySelector('h2 a');
  const p = li.querySelector('.b_caption p, p');
  return a ? {title: a.innerText, href: a.href, snippet: p ? p.innerText : ''} : null;
}).filter(Boolean)"""


def _unwrap_bing(url: str) -> str:
    """Bing wraps results in /ck/a?...&u=a1<base64url>; recover the real address."""
    if "bing.com/ck/" not in url:
        return url
    u = parse_qs(urlparse(url).query).get("u", [""])[0]
    if u.startswith("a1"):
        import base64
        try:
            return base64.urlsafe_b64decode(u[2:] + "=" * (-len(u[2:]) % 4)).decode("utf-8", "replace")
        except ValueError:
            return ""
    return ""


def search_web_rendered(query: str, limit: int = 8) -> list[Hit]:
    """Bing results read through headless Edge (plain HTTP requests get degraded results)."""
    from urllib.parse import quote_plus

    from ..tools.browser import WORKER

    def job(w):
        ctx = w.headless().new_context(locale="en-GB")
        try:
            page = ctx.new_page()
            page.goto(f"https://www.bing.com/search?q={quote_plus(query)}&setlang=en", timeout=20_000,
                      wait_until="domcontentloaded")
            page.wait_for_timeout(600)
            return page.evaluate(BING_JS)
        finally:
            ctx.close()
    try:
        rows = WORKER.call(job, timeout=40)
    except Exception as exc:  # noqa: BLE001
        if "ERR_INTERNET_DISCONNECTED" in str(exc) or "ERR_NAME_NOT_RESOLVED" in str(exc):
            raise Offline(str(exc)) from exc
        raise
    hits = []
    for row in rows:
        url = _unwrap_bing(row.get("href", ""))
        if url.startswith("http") and "bing.com" not in urlparse(url).netloc:
            hits.append(Hit(_clean(row.get("title", "")), url, _clean(row.get("snippet", ""))))
        if len(hits) >= limit:
            break
    return hits


def search_wikipedia(query: str, limit: int = 2) -> list[Hit]:
    try:
        r = requests.get("https://en.wikipedia.org/w/api.php", headers=UA, timeout=TIMEOUT, params={
            "action": "query", "generator": "search", "gsrsearch": query, "gsrlimit": limit, "prop": "extracts",
            "explaintext": 1, "exchars": 6000, "format": "json", "formatversion": 2})
    except requests.ConnectionError as exc:
        raise Offline(str(exc)) from exc
    r.raise_for_status()
    pages = sorted(r.json().get("query", {}).get("pages", []), key=lambda p: p.get("index", 99))
    out = []
    for p in pages:
        title = p.get("title", "")
        text = (p.get("extract") or "").strip()
        if not text:
            continue
        out.append(Hit(f"{title} (Wikipedia)", "https://en.wikipedia.org/wiki/" + title.replace(" ", "_"),
                       text[:200], "wikipedia", text))
    return out


def search_arxiv(query: str, limit: int = 2) -> list[Hit]:
    words = " AND ".join(f"all:{w}" for w in re.findall(r"[\w-]{3,}", query)[:6]) or f"all:{query}"
    try:
        r = requests.get("https://export.arxiv.org/api/query", headers=UA, timeout=TIMEOUT,
                         params={"search_query": words, "max_results": limit, "sortBy": "relevance"})
    except requests.ConnectionError as exc:
        raise Offline(str(exc)) from exc
    r.raise_for_status()
    ns = {"a": "http://www.w3.org/2005/Atom"}
    out = []
    for e in ET.fromstring(r.content).findall("a:entry", ns):
        title = _clean(e.findtext("a:title", "", ns))
        summary = _clean(e.findtext("a:summary", "", ns))
        url = (e.findtext("a:id", "", ns) or "").strip()
        authors = [_clean(a.findtext("a:name", "", ns)) for a in e.findall("a:author", ns)]
        year = (e.findtext("a:published", "", ns) or "")[:4]
        if title and summary and url:
            by = authors[0] + (" et al." if len(authors) > 1 else "") if authors else ""
            out.append(Hit(f"{title} ({by}, {year}, arXiv)" if by else f"{title} (arXiv)", url, summary[:200],
                           "arxiv", f"Abstract: {summary}", {"authors": authors, "year": year}))
    return out


# ---------------------------------------------------------------- reading pages
BLOCK = {"p", "div", "li", "br", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section", "article", "pre",
         "blockquote", "dd", "dt", "figcaption", "td", "th", "table", "ul", "ol"}
DROP = {"script", "style", "noscript", "nav", "footer", "header", "aside", "form", "svg", "button", "select",
        "template", "iframe", "menu", "dialog"}


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0
        self.title = ""
        self._in_title = False
        self.main_depth = 0
        self.main_parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in DROP:
            self.skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag in ("main", "article"):
            self.main_depth += 1
        if tag in BLOCK:
            self._add("\n")

    def handle_endtag(self, tag):
        if tag in DROP and self.skip:
            self.skip -= 1
        elif tag == "title":
            self._in_title = False
        elif tag in ("main", "article") and self.main_depth:
            self.main_depth -= 1
        if tag in BLOCK:
            self._add("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self.skip:
            self._add(data)

    def _add(self, s):
        self.parts.append(s)
        if self.main_depth:
            self.main_parts.append(s)


def extract_text(page_html: str) -> tuple[str, str]:
    """(title, readable text) from an HTML page, without boilerplate lines."""
    p = _Text()
    try:
        p.feed(page_html)
    except Exception:  # noqa: BLE001  (malformed HTML: keep what was parsed)
        pass
    raw = "".join(p.main_parts) if len("".join(p.main_parts)) > 1500 else "".join(p.parts)
    lines = []
    for line in raw.split("\n"):
        line = re.sub(r"[ \t\xa0]+", " ", line).strip()
        # Keep sentences and real headings; drop menu crumbs, cookie banners and button labels.
        if len(line) >= 60 or (len(line) >= 25 and re.search(r"[.:?!]$", line)):
            if not re.search(r"\b(cookies?|subscribe|sign in|log in|newsletter|all rights reserved)\b", line, re.I) \
                    or len(line) > 200:
                lines.append(line)
    text = "\n".join(dict.fromkeys(lines))  # drop repeated lines, keep order
    return _clean(p.title)[:200], text


def fetch(hit: Hit) -> Hit:
    """Read the page behind a search hit. Raises on failure (the caller records why)."""
    if hit.text:  # API sources already carry their text
        return hit
    r = requests.get(hit.url, headers=UA, timeout=TIMEOUT, stream=True, allow_redirects=True)
    with r:
        r.raise_for_status()
        ctype = r.headers.get("content-type", "")
        if "html" not in ctype and "text/plain" not in ctype:
            raise ValueError(f"not a web page ({ctype.split(';')[0] or 'unknown type'})")
        body = r.raw.read(MAX_BYTES, decode_content=True)
        enc = r.encoding if r.encoding and r.encoding.lower() != "iso-8859-1" else (r.apparent_encoding or "utf-8")
        page = body.decode(enc or "utf-8", errors="replace")
    title, text = extract_text(page) if "html" in ctype else ("", page)
    hit.text = text
    if title and len(title) > 8:
        hit.meta["page_title"] = title
    return hit


def fetch_rendered(hit: Hit) -> Hit:
    """Fallback for pages that only render with JavaScript: headless Edge through Playwright."""
    from ..tools.browser import WORKER, _page_text

    def job(w):
        ctx = w.headless().new_context()
        try:
            page = ctx.new_page()
            page.goto(hit.url, timeout=20_000, wait_until="domcontentloaded")
            page.wait_for_timeout(1200)
            return _page_text(page)
        finally:
            ctx.close()
    data = WORKER.call(job, timeout=40)
    hit.text = "\n".join(line for line in data["text"].split("\n") if len(line.strip()) >= 40)
    hit.meta["rendered"] = True
    return hit


def skip_reason(url: str) -> str:
    host = urlparse(url).netloc.lower()
    if any(d in host for d in SKIP_DOMAINS):
        return "video/social/shop page"
    if urlparse(url).path.lower().endswith((".pdf", ".zip", ".exe", ".mp4", ".mp3", ".docx", ".pptx")):
        return "file download, not a page"
    return ""
