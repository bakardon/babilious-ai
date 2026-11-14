"""Milestone 2: folder indexing, search, retrieval in chat, graph, semantic search."""
import json
import os
import time
from pathlib import Path

import pytest

from babilious import db, documents, indexer, intents, memory, semantic
from conftest import H


def make_pdf(path: Path, text: str) -> None:
    """Write a minimal one-page PDF with a single line of text."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()
    path.write_bytes(bytes(out))


@pytest.fixture(autouse=True)
def allow_temp_dirs(monkeypatch):
    monkeypatch.setattr(indexer, "FORBIDDEN_PARTS", [])


def test_appdata_is_refused(monkeypatch):
    monkeypatch.setattr(indexer, "FORBIDDEN_PARTS", [chr(92) + "appdata"])
    appdata = os.environ.get("LOCALAPPDATA")
    if appdata and os.path.isdir(appdata):
        with pytest.raises(ValueError, match="AppData"):
            indexer.add_folder(appdata)


@pytest.fixture
def library(tmp_path):
    root = tmp_path / "notes"
    (root / "research").mkdir(parents=True)
    (root / "node_modules" / "pkg").mkdir(parents=True)
    (root / ".git").mkdir()
    (root / "research" / "radar.md").write_text(
        "# Radar project\n\nPulse-doppler radar signal processing with FMCW chirps and doppler FFT.\n"
        "See [[thesis plan]] for the write-up.\n", encoding="utf-8")
    (root / "thesis plan.md").write_text(
        "# Thesis plan\n\nChapter on radar signal processing, doppler estimation and FMCW chirps.\n",
        encoding="utf-8")
    (root / "groceries.txt").write_text("eggs, milk, bread", encoding="utf-8")
    (root / "signal.py").write_text("def parse_signal(samples):\n    return [s * 2 for s in samples]\n",
                                    encoding="utf-8")
    (root / "windows_notes.txt").write_bytes("Kalman filter tuning notes".encode("utf-16"))
    import docx
    d = docx.Document()
    d.add_paragraph("Master's applications: deadlines for TU Munich and ETH Zurich.")
    d.save(root / "applications.docx")
    make_pdf(root / "Project_Title.pdf", "Proposal for a quantum sensing testbed")
    (root / "photo.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 100)
    (root / "node_modules" / "pkg" / "index.js").write_text("radar radar radar", encoding="utf-8")
    (root / ".git" / "config").write_text("radar", encoding="utf-8")
    (root / "~$applications.docx").write_bytes(b"lock")
    return root


def _index(root: Path) -> dict:
    indexer.add_folder(str(root))
    return indexer.run_indexing_now()


# ---------------------------------------------------------------- extraction

def test_extractors(library):
    assert "quantum sensing" in documents.extract_text(library / "Project_Title.pdf")
    assert "ETH Zurich" in documents.extract_text(library / "applications.docx")
    assert "Kalman" in documents.extract_text(library / "windows_notes.txt")
    assert not documents.can_read(library / "photo.png")


def test_chunking_overlaps_and_covers_text():
    text = ("Sentence number %d is here. " * 200) % tuple(range(200))
    chunks = documents.chunk(text, size=500, overlap=80)
    assert len(chunks) > 5 and all(len(c) <= 500 for c in chunks)
    assert chunks[-1].endswith("199 is here.")


def test_links_and_keywords():
    t = "See [[Thesis Plan]] and [notes](./radar.md) and [site](https://x.org). radar radar doppler doppler doppler doppler"
    assert documents.links(t) == ["radar", "thesis plan"]
    assert documents.keywords(t)[0] == "doppler"


# ---------------------------------------------------------------- folders

def test_folder_rules(library, tmp_path):
    for bad in ("C:\\", str(Path.home()), str(tmp_path / "missing"), "relative\\path"):
        with pytest.raises(ValueError):
            indexer.add_folder(bad)
    indexer.add_folder(str(library))
    with pytest.raises(ValueError, match="already"):
        indexer.add_folder(str(library))
    with pytest.raises(ValueError, match="covered"):
        indexer.add_folder(str(library / "research"))
    with pytest.raises(ValueError, match="contains"):
        indexer.add_folder(str(tmp_path))


def test_index_skips_noise_and_is_incremental(library):
    s = _index(library)
    names = {d["name"] for d in indexer.search("", limit=100)}
    assert names == {"radar.md", "thesis plan.md", "groceries.txt", "signal.py", "windows_notes.txt",
                     "applications.docx", "Project_Title.pdf", "photo.png"}
    assert s["indexed"] == 8 and s["errors"] == 0
    st = indexer.stats()
    assert st["files"] == 8 and st["with_text"] == 7  # the PNG is name-only

    s = indexer.run_indexing_now()
    assert s["indexed"] == 0 and s["unchanged"] == 8

    time.sleep(0.05)
    (library / "groceries.txt").write_text("eggs, milk, bread, coffee beans", encoding="utf-8")
    os.remove(library / "signal.py")
    s = indexer.run_indexing_now()
    assert s["indexed"] == 1 and s["removed"] == 1
    assert indexer.search("coffee")[0]["name"] == "groceries.txt"
    assert not indexer.search("parse_signal")
    # FTS stays in sync with deletions (no orphan rows).
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0] == \
               conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]


def test_unreadable_files_are_recorded_not_fatal(library):
    (library / "broken.pdf").write_bytes(b"%PDF-1.4 this is not really a pdf")
    s = _index(library)
    assert s["errors"] == 1
    broken = [r for r in indexer.search("broken") if r["name"] == "broken.pdf"][0]
    assert broken["error"]


def test_remove_folder_keeps_files(library):
    f = indexer.add_folder(str(library))
    indexer.run_indexing_now()
    assert indexer.remove_folder(f["id"])
    assert indexer.stats()["files"] == 0
    assert (library / "research" / "radar.md").exists()


# ---------------------------------------------------------------- search

def test_search_content_name_filters_and_sort(library):
    _index(library)
    hits = indexer.search("doppler chirps")
    assert {h["name"] for h in hits[:2]} == {"radar.md", "thesis plan.md"}
    assert indexer.MARK_START in hits[0]["snippet"]
    assert indexer.search("project title")[0]["name"] == "Project_Title.pdf"
    assert [h["name"] for h in indexer.search("", ext="pdf")] == ["Project_Title.pdf"]
    assert {h["name"] for h in indexer.search("", ext=".md .txt")} == {
        "radar.md", "thesis plan.md", "groceries.txt", "windows_notes.txt"}
    assert indexer.search("parse_signal")[0]["name"] == "signal.py"
    assert indexer.search("radar", path_contains="research")[0]["name"] == "radar.md"
    assert indexer.search('"; DROP TABLE documents; --')  is not None  # odd input never crashes


def test_retrieval_for_chat_is_strict(library):
    _index(library)
    got = indexer.retrieve_for_chat("What did I write about doppler estimation for the radar?")
    assert got and got[0]["name"] in ("radar.md", "thesis plan.md")
    assert indexer.retrieve_for_chat("how are you doing today?") == []
    assert indexer.retrieve_for_chat("hello babilious") == []


def test_graph_and_related(library):
    _index(library)
    g = indexer.graph()
    names = {n["id"]: n["name"] for n in g["nodes"]}
    pairs = {frozenset((names[e["source"]], names[e["target"]])) for e in g["edges"]}
    assert frozenset(("radar.md", "thesis plan.md")) in pairs
    radar = indexer.search("radar.md")[0]
    rel = indexer.related(radar["id"])
    assert rel[0]["name"] == "thesis plan.md" and rel[0]["kind"] == "link"


def test_semantic_search_with_embeddings(library, fake):
    db.set_setting("semantic_search", True)
    indexer.add_folder(str(library))
    indexer.run_indexing_now(embed_client=fake)
    assert indexer.stats()["embedded_chunks"] == indexer.stats()["chunks"]
    top_sim, top = semantic.search(fake, "doppler chirps radar processing", limit=1)[0]
    assert top["name"] in ("radar.md", "thesis plan.md") and top_sim > 0.5


# ---------------------------------------------------------------- chat integration

def test_file_search_commands(library, fake):
    r = intents.handle("Find the latest PDF", fake)
    assert r.action == "file_search_empty"  # nothing indexed yet: says so, doesn't guess
    _index(library)
    r = intents.handle("Find the latest PDF in my notes folder", fake)
    assert r.action == "file_search" and r.data["results"][0]["name"] == "Project_Title.pdf"
    r = intents.handle("Search for python files containing parse_signal", fake)
    assert [x["name"] for x in r.data["results"]] == ["signal.py"]
    r = intents.handle("show me my latest files", fake)
    assert len(r.data["results"]) == 8
    # Not a file request: goes to the model.
    assert intents.handle("Find the error in this application", fake) is None


def test_index_command_with_path(library, fake):
    r = intents.handle(f"index {library}", fake)
    assert r.action == "index_started"
    for _ in range(100):
        if not indexer.job_status().get("running"):
            break
        time.sleep(0.05)
    assert indexer.stats()["files"] == 8
    assert intents.handle("index my notes folder", fake).action == "index_help"


def test_summarise_document_uses_its_text(client, library):
    _index(library)
    r = client.post("/api/chat/stream", json={"message": "Summarise applications.docx"}, headers=H)
    events = [json.loads(line) for line in r.get_data(as_text=True).splitlines()]
    done = events[-1]
    assert done["source"] == "llm" and done["sources"][0]["name"] == "applications.docx"
    msgs = client.fake.last_messages
    assert "ETH Zurich" in msgs[-2]["content"]
    assert msgs[-1]["content"].startswith("Summarise the document applications.docx")
    r = client.post("/api/chat", json={"message": "Summarise the nonexistent.pdf file"}, headers=H).get_json()
    assert r["source"] == "command" and "couldn't find" in r["answer"]


def test_chat_cites_indexed_files(client, library):
    _index(library)
    r = client.post("/api/chat/stream", json={"message": "What are my notes on doppler estimation and FMCW?"},
                    headers=H)
    done = [json.loads(line) for line in r.get_data(as_text=True).splitlines()][-1]
    assert done["sources"] and done["sources"][0]["name"] in ("radar.md", "thesis plan.md")
    assert "file: " in client.fake.last_messages[-2]["content"]
    db.set_setting("use_documents_in_chat", False)
    r = client.post("/api/chat/stream", json={"message": "What are my notes on doppler estimation and FMCW?"},
                    headers=H)
    done = [json.loads(line) for line in r.get_data(as_text=True).splitlines()][-1]
    assert done["sources"] == []


def test_memory_suggestions_need_approval(client):
    r = client.post("/api/chat", json={"message": "I'm applying to master's programs in Germany."}, headers=H)
    assert r.status_code == 200
    assert memory.list_memories("Germany") == []  # nothing saved silently
    events = [json.loads(line) for line in client.post(
        "/api/chat/stream", json={"message": "I prefer short answers."}, headers=H).get_data(as_text=True).splitlines()]
    assert events[-1]["suggest_memory"] == 'User said: "I prefer short answers"'
    assert intents.suggest_memory("Do I prefer tea?") is None
    assert intents.suggest_memory("The weather is nice") is None


# ---------------------------------------------------------------- API

def test_folder_and_search_api(client, library):
    r = client.post("/api/folders", json={"path": str(library)}, headers=H)
    assert r.status_code == 201
    for _ in range(100):
        if not client.get("/api/index/status").get_json().get("running"):
            break
        time.sleep(0.05)
    data = client.get("/api/folders").get_json()
    assert data["folders"][0]["file_count"] == 8 and data["stats"]["with_text"] == 7
    res = client.get("/api/files/search?q=quantum").get_json()["results"]
    assert res[0]["name"] == "Project_Title.pdf"
    doc = client.get(f"/api/documents/{res[0]['id']}").get_json()
    assert "quantum sensing" in doc["preview"]
    assert client.get("/api/documents/99999").status_code == 404
    g = client.get("/api/graph").get_json()
    assert len(g["nodes"]) == 7
    assert client.post("/api/folders", json={"path": "C:\\"}, headers=H).status_code == 400
    fid = data["folders"][0]["id"]
    assert client.delete(f"/api/folders/{fid}", headers=H).status_code == 200


def test_semantic_toggle_requires_embedding_model(client):
    r = client.patch("/api/settings", json={"semantic_search": True}, headers=H)
    assert r.status_code == 400 and "nomic-embed-text" in r.get_json()["error"]
    client.fake.models.append({"name": "nomic-embed-text:latest", "size": 274_000_000, "params": "137M",
                               "quantization": "F16", "family": "nomic-bert", "modified_at": None})
    assert client.patch("/api/settings", json={"semantic_search": True}, headers=H).status_code == 200
    # Embedding models can't become the chat model, and "faster" never picks one.
    assert client.post("/api/models/active", json={"name": "nomic-embed-text:latest"}, headers=H).status_code == 400
