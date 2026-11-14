"""Read documents into plain text, chunks and keywords. No network, no models."""
from __future__ import annotations

import json
import logging
import re
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path

log = logging.getLogger(__name__)

TEXT_EXTS = {
    ".txt", ".md", ".markdown", ".rst", ".log", ".csv", ".tsv", ".json", ".yaml", ".yml", ".ini",
    ".cfg", ".toml", ".tex", ".py", ".js", ".mjs", ".ts", ".tsx", ".jsx", ".java", ".c", ".cpp", ".cc",
    ".h", ".hpp", ".cs", ".go", ".rs", ".m", ".r", ".sql", ".sh", ".ps1", ".bat", ".css", ".scss",
    ".xml", ".vhd", ".vhdl", ".v", ".sv", ".kt", ".swift", ".php", ".rb", ".lua", ".dart",
}
HTML_EXTS = {".html", ".htm"}
RICH_EXTS = {".pdf", ".docx", ".ipynb"}
CONTENT_EXTS = TEXT_EXTS | HTML_EXTS | RICH_EXTS

MAX_TEXT_BYTES = 3_000_000
MAX_RICH_BYTES = 40_000_000
MAX_PDF_PAGES = 300
CHUNK_CHARS = 1200
CHUNK_OVERLAP = 150

STOP = set("""
a about above after again against all also am an and any are as at be because been before being below
between both but by can could did do does doing down during each few for from further had has have
having he her here hers herself him himself his how i if in into is it its itself just let me more most
my myself no nor not now of off on once only or other our ours ourselves out over own same she should so
some such than that the their theirs them themselves then there these they this those through to too
under until up very was we were what when where which while who whom why will with would you your yours
yourself yourselves also using used use one two three may might must shall within without however
therefore thus etc via per like get got make made new old true false none null return import from self
def class function var const let elif else print value values data file files page pages section
""".split())


class _HTMLText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1
        if tag in ("p", "div", "br", "li", "h1", "h2", "h3", "tr"):
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):  # UTF-16 with BOM (common for Windows tools)
        return raw.decode("utf-16", errors="replace")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace")


def _read_pdf(path: Path) -> str:
    from pypdf import PdfReader
    reader = PdfReader(str(path))
    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception as exc:  # noqa: BLE001
            raise ValueError("PDF is password-protected") from exc
    pages = []
    for i, page in enumerate(reader.pages):
        if i >= MAX_PDF_PAGES:
            break
        try:
            pages.append(page.extract_text() or "")
        except Exception:  # noqa: BLE001 - one bad page shouldn't lose the document
            pages.append("")
    text = "\n\n".join(pages)
    if not text.strip():
        raise ValueError("No extractable text (probably a scanned PDF)")
    return text


def _read_docx(path: Path) -> str:
    import docx
    d = docx.Document(str(path))
    parts = [p.text for p in d.paragraphs]
    for table in d.tables:
        for row in table.rows:
            parts.append(" | ".join(c.text.strip() for c in row.cells))
    return "\n".join(parts)


def _read_ipynb(path: Path) -> str:
    nb = json.loads(_read_text(path))
    return "\n\n".join("".join(c.get("source", [])) for c in nb.get("cells", []))


def can_read(path: Path) -> bool:
    return path.suffix.lower() in CONTENT_EXTS


def extract_text(path: Path) -> str:
    """Return the text of a supported file. Raises ValueError with a readable reason."""
    ext = path.suffix.lower()
    size = path.stat().st_size
    limit = MAX_RICH_BYTES if ext in RICH_EXTS else MAX_TEXT_BYTES
    if size > limit:
        raise ValueError(f"Too large to index ({size // 1_000_000} MB)")
    if ext == ".pdf":
        text = _read_pdf(path)
    elif ext == ".docx":
        text = _read_docx(path)
    elif ext == ".ipynb":
        text = _read_ipynb(path)
    elif ext in HTML_EXTS:
        p = _HTMLText()
        p.feed(_read_text(path))
        text = "".join(p.parts)
    elif ext in TEXT_EXTS:
        text = _read_text(path)
    else:
        raise ValueError(f"Unsupported type {ext}")
    text = text.replace("\x00", "")
    text = re.sub(r"[ \t]+\n", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def chunk(text: str, size: int = CHUNK_CHARS, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split into ~size-char chunks on paragraph/sentence boundaries, with overlap."""
    if len(text) <= size:
        return [text] if text.strip() else []
    chunks, start = [], 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            window = text[start:end]
            cut = max(window.rfind("\n\n"), window.rfind(". "), window.rfind("\n"))
            if cut > size * 0.5:
                end = start + cut + 1
        piece = text[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return chunks


def keywords(text: str, n: int = 12) -> list[str]:
    words = re.findall(r"[a-zA-Z][a-zA-Z0-9_-]{3,}", text[:200_000].lower())
    counts = Counter(w for w in words if w not in STOP and not w.isdigit())
    return [w for w, _ in counts.most_common(n)]


def links(text: str) -> list[str]:
    """Names this document refers to: [[wiki links]] and relative Markdown links."""
    found = set(re.findall(r"\[\[([^\]|#]+)", text))
    for target in re.findall(r"\]\(([^)\s]+)\)", text):
        if not re.match(r"[a-z]+://", target) and not target.startswith("#"):
            found.add(Path(target.split("#")[0]).stem)
    return sorted(f.strip().lower() for f in found if f.strip())[:50]


def summary(text: str, limit: int = 280) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[:limit].rsplit(" ", 1)[0] + " ..."
