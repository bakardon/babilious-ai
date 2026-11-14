"""Milestone 6: web research with numbered sources, honesty checks, storage and the chat/API paths."""
import json

import pytest

from conftest import REAL_SOURCES, FakeOllama
from babilious import assistant, memory
from babilious.research import engine, sources
from babilious.research.sources import Hit


# ---------------------------------------------------------------- parsing requests
@pytest.mark.parametrize("text,topic,depth,papers", [
    ("research mmWave radar for cars", "mmWave radar for cars", "normal", False),
    ("Quick research on Rust vs Go", "Rust vs Go", "quick", False),
    ("do some research on solid state batteries", "solid state batteries", "normal", False),
    ("deep research into transformer attention", "transformer attention", "deep", False),
    ("look up the James Webb telescope online", "James Webb telescope", "quick", False),
    ("what's the latest on Ollama?", "Ollama", "quick", False),
    ("find papers on radar gesture recognition", "radar gesture recognition", "normal", True),
    ("what does the web say about intermittent fasting", "intermittent fasting", "normal", False),
    ("compare sources on nuclear fusion timelines", "nuclear fusion timelines", "normal", False),
])
def test_parse_request(text, topic, depth, papers):
    r = engine.parse_request(text)
    assert r == {"topic": topic, "depth": depth, "papers": papers}


@pytest.mark.parametrize("text", ["research it", "what is research?", "I did some research yesterday",
                                  "search the web for cats", "look up my notes about radar", "hello"])
def test_parse_request_ignores_other_messages(text):
    assert engine.parse_request(text) is None


# ---------------------------------------------------------------- page text & passages
def test_extract_text_keeps_content_and_drops_boilerplate():
    html = """<html><head><title>Radar basics</title><script>var x = 'Radar uses radio waves to detect things';</script></head>
    <body><nav><a>Home</a> <a>About us and everything else in the navigation menu</a></nav>
    <main><h1>Radar</h1><p>Radar uses radio waves to detect objects and measure their distance and speed.</p>
    <p>Automotive radar commonly operates in the 77 GHz band for long-range detection.</p></main>
    <footer>All rights reserved, this footer line should never show up in the text.</footer></body></html>"""
    title, text = sources.extract_text(html)
    assert title == "Radar basics"
    assert "77 GHz band" in text and "measure their distance" in text
    assert "var x" not in text and "navigation menu" not in text and "rights reserved" not in text


def test_select_passages_drops_irrelevant_pages_and_respects_budget():
    on = Hit("Radar", "https://a.com/x", text=("Automotive radar at 77 GHz detects cars and pedestrians. " * 30))
    off = Hit("Cooking", "https://b.com/y", text=("This pasta recipe needs garlic, olive oil and basil. " * 30))
    picked = engine.select_passages("automotive radar", [on, off], 1200)
    assert [p["hit"].site for p in picked] == ["a.com"]
    assert len(picked[0]["text"]) <= 1200 // 2 + 200


def test_fix_citations_removes_numbers_without_a_source():
    text, used = engine.fix_citations("Fact one [1]. Fact two [7]. Both [1, 2]. Range [2-3]. Nothing [9][9].", 3)
    assert used == {1, 2, 3}
    assert "[7]" not in text and "[9]" not in text
    assert "[1][2]" in text and "[2][3]" in text


def test_unverified_numbers_flags_invented_figures_only():
    corpus = "The sensor runs at 77 GHz and the market reached 4,500 units in 2023."
    summary = ("1. It runs at 77 GHz [1].\n2. Sales hit 4500 units in 2023 [2].\n"
               "3. Range is 250 m and accuracy is 99.5% [1]. It has 3 antennas.")
    flagged = engine.unverified_numbers(summary, corpus, "radar")
    assert "250 m" in flagged and "99.5%" in flagged
    assert not any(f.startswith(("77", "4500", "2023", "3")) and f not in ("250 m",) for f in flagged if f != "99.5%")


# ---------------------------------------------------------------- search parsers (fake HTTP)
class _Resp:
    def __init__(self, text="", status=200, content=b"", js=None, headers=None):
        self.text, self.status_code, self.content, self._js = text, status, content or text.encode(), js
        self.headers = headers or {}

    def json(self):
        return self._js

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


DDG = """<div class="result results_links results_links_deep web-result ">
<h2 class="result__title"><a rel="nofollow" class="result__a" href="https://www.ti.com/radar">mmWave radar | <b>TI</b></a></h2>
<a class="result__snippet" href="x">Making <b>mmWave</b> radar attainable</a></div>
<div class="result results_links results_links_deep web-result ">
<h2 class="result__title"><a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org%2Fpage&rut=1">Second &amp; result</a></h2>
</div>"""


def test_search_web_parses_duckduckgo(monkeypatch):
    monkeypatch.setattr(sources, "_ddg_blocked_until", 0.0)
    monkeypatch.setattr(sources.requests, "post", lambda *a, **k: _Resp(DDG))
    hits = REAL_SOURCES["search_web"]("radar")
    assert [(h.url, h.title) for h in hits] == [("https://www.ti.com/radar", "mmWave radar | TI"),
                                                ("https://example.org/page", "Second & result")]
    assert hits[0].snippet == "Making mmWave radar attainable" and hits[0].site == "ti.com"


def test_search_web_falls_back_when_duckduckgo_blocks(monkeypatch):
    monkeypatch.setattr(sources, "_ddg_blocked_until", 0.0)
    monkeypatch.setattr(sources.requests, "post", lambda *a, **k: _Resp("<html>challenge</html>", 202))
    monkeypatch.setattr(sources, "search_web_rendered", lambda q, n=8: [Hit("Bing hit", "https://bing-result.com/a")])
    assert REAL_SOURCES["search_web"]("radar")[0].url == "https://bing-result.com/a"
    # ...and keeps using the fallback for a while instead of hammering DuckDuckGo.
    monkeypatch.setattr(sources.requests, "post", lambda *a, **k: pytest.fail("DuckDuckGo asked again"))
    assert REAL_SOURCES["search_web"]("radar")[0].url == "https://bing-result.com/a"


def test_unwrap_bing_links():
    import base64
    enc = base64.urlsafe_b64encode(b"https://real.example/page?x=1").decode().rstrip("=")
    assert sources._unwrap_bing(f"https://www.bing.com/ck/a?!&&p=abc&u=a1{enc}&ntb=1") == "https://real.example/page?x=1"
    assert sources._unwrap_bing("https://plain.example/") == "https://plain.example/"


def test_search_wikipedia_and_arxiv(monkeypatch):
    wiki = {"query": {"pages": [{"index": 2, "title": "Radar gun", "extract": "A radar gun measures speed."},
                                {"index": 1, "title": "Radar", "extract": "Radar is a system that uses radio waves."}]}}
    monkeypatch.setattr(sources.requests, "get", lambda *a, **k: _Resp(js=wiki))
    hits = REAL_SOURCES["search_wikipedia"]("radar")
    assert hits[0].title == "Radar (Wikipedia)" and hits[0].url == "https://en.wikipedia.org/wiki/Radar"
    assert hits[0].text.startswith("Radar is") and hits[0].kind == "wikipedia"
    atom = b"""<?xml version='1.0'?><feed xmlns="http://www.w3.org/2005/Atom"><entry>
      <id>http://arxiv.org/abs/2401.00001v1</id><published>2024-01-02T00:00:00Z</published>
      <title>Radar Gesture
      Recognition</title><summary>We recognise hand gestures with 60 GHz radar.</summary>
      <author><name>A. Author</name></author><author><name>B. Author</name></author></entry></feed>"""
    monkeypatch.setattr(sources.requests, "get", lambda *a, **k: _Resp(content=atom))
    paper = REAL_SOURCES["search_arxiv"]("radar gestures")[0]
    assert paper.title == "Radar Gesture Recognition (A. Author et al., 2024, arXiv)"
    assert paper.text == "Abstract: We recognise hand gestures with 60 GHz radar." and paper.kind == "arxiv"


def test_skip_reason():
    assert sources.skip_reason("https://www.youtube.com/watch?v=1")
    assert sources.skip_reason("https://site.com/paper.pdf")
    assert sources.skip_reason("https://site.com/article") == ""


# ---------------------------------------------------------------- the whole run
PAGES = {
    "https://en.wikipedia.org/wiki/Automotive_radar": "Automotive radar is used in cars for adaptive cruise control. "
        "Most modern automotive radar sensors operate in the 77 GHz band, which allows high resolution.\n" * 3,
    "https://vendor.example/radar": "Our automotive radar module detects vehicles up to 200 m away. "
        "Automotive radar works in rain, fog and darkness, unlike cameras.\n" * 3,
    "https://blog.example/fmcw": "FMCW automotive radar measures distance and velocity at the same time. "
        "Engineers combine automotive radar with cameras for emergency braking.\n" * 3,
}


def _fake_web(monkeypatch):
    monkeypatch.setattr(sources, "search_web", lambda q, n=8: [
        Hit("Radar module", "https://vendor.example/radar"), Hit("FMCW explained", "https://blog.example/fmcw"),
        Hit("Watch: radar video", "https://www.youtube.com/watch?v=x"), Hit("Broken page", "https://down.example/")])
    monkeypatch.setattr(sources, "search_wikipedia", lambda q, n=2: [
        Hit("Automotive radar (Wikipedia)", "https://en.wikipedia.org/wiki/Automotive_radar", kind="wikipedia",
            text=PAGES["https://en.wikipedia.org/wiki/Automotive_radar"])])
    monkeypatch.setattr(sources, "search_arxiv", lambda q, n=2: [])

    def fetch(hit):
        if hit.url not in PAGES:
            raise RuntimeError("connection reset")
        hit.text = hit.text or PAGES[hit.url]
        return hit
    monkeypatch.setattr(sources, "fetch", fetch)


def test_run_reads_sources_and_checks_the_summary(monkeypatch):
    _fake_web(monkeypatch)
    fake = FakeOllama(reply="Automotive radar mostly uses 77 GHz [1] and sees through fog [2]. "
                            "It reaches 300 m [2] and costs 50 dollars [5].")
    events = list(engine.run("automotive radar", fake, "normal"))
    kinds = [e["type"] for e in events]
    assert kinds[0] == "state" and kinds[-1] == "done" and "token" in kinds
    read = [e for e in events if e["type"] == "source"]
    assert {e["site"] for e in read if e["ok"]} == {"en.wikipedia.org", "vendor.example", "blog.example"}
    assert any(not e["ok"] and e["site"] == "down.example" for e in read)
    assert not any("youtube" in e["site"] for e in read)   # video pages are never read
    done = events[-1]
    assert [s["site"] for s in done["web_sources"]] == ["en.wikipedia.org", "vendor.example", "blog.example"]
    assert "[5]" not in done["text"]                       # no such source: citation removed
    assert any("300 m" in w and "50" in w for w in done["warnings"])   # figures not in any page
    assert "77 GHz" not in " ".join(done["warnings"])
    # The model only saw what was read, numbered the same way.
    ctx = fake.last_messages[1]["content"]
    assert "[1] Automotive radar (Wikipedia)" in ctx and "[3] FMCW explained" in ctx
    saved = engine.get(done["research_id"])
    assert saved["topic"] == "automotive radar" and len(saved["sources"]) == 3 and saved["warnings"]
    assert "Sources:\n[1]" in done["stored_text"]


def test_run_without_ollama_lists_what_each_source_says(monkeypatch):
    _fake_web(monkeypatch)
    done = list(engine.run("automotive radar", FakeOllama(available=False), "quick"))[-1]
    assert "offline" in done["text"] and "[1] Automotive radar (Wikipedia)" in done["text"]
    assert done["warnings"] == [] and len(done["web_sources"]) == 3


def test_run_offline_says_so_and_offers_local_notes():
    from babilious.manager import notes
    notes.create("Radar notes: the demo uses 77 GHz #radar")
    done = list(engine.run("radar", FakeOllama(), "normal"))[-1]
    assert done["action"] == "research_offline" and "no internet" in done["text"]
    assert "Note:" in done["text"] and done["web_sources"] == []
    assert engine.list_research() == []   # nothing fake was saved


def test_run_with_no_relevant_pages_refuses_to_summarise(monkeypatch):
    monkeypatch.setattr(sources, "search_web", lambda q, n=8: [Hit("Pasta", "https://food.example/pasta")])
    monkeypatch.setattr(sources, "search_wikipedia", lambda q, n=2: [])
    monkeypatch.setattr(sources, "fetch", lambda h: setattr(h, "text", "Boil the pasta with garlic and basil. " * 40) or h)
    fake = FakeOllama()
    done = list(engine.run("quantum radar", fake, "normal"))[-1]
    assert done["action"] == "research_empty" and "won't pretend" in done["text"]
    assert fake.last_messages is None


def test_research_in_chat_and_follow_up_context(monkeypatch):
    _fake_web(monkeypatch)
    fake = FakeOllama(reply="Radar uses 77 GHz [1].")
    cid = memory.create_conversation()
    events = list(assistant.respond(cid, "research automotive radar", fake))
    done = events[-1]
    assert done["type"] == "done" and done["source"] == "research" and "stored_text" not in done
    assert len(done["web_sources"]) == 3 and done["research_id"]
    stored = memory.get_messages(cid)[-1]
    assert stored["source"] == "research" and "Sources:" in stored["content"] and "https://vendor.example/radar" in stored["content"]
    # A follow-up question in the same chat gets the research as context.
    msgs, _ = assistant.build_messages(cid, "which source was about FMCW?", fake)
    assert "research you just did on automotive radar" in msgs[-2]["content"]


def test_research_api(client, monkeypatch):
    _fake_web(monkeypatch)
    client.fake.reply = "Radar uses 77 GHz [1]."
    h = {"X-Babilious": "1"}
    r = client.post("/api/research", json={"topic": "x"}, headers=h)
    assert r.status_code == 400
    r = client.post("/api/research", json={"topic": "automotive radar", "depth": "quick"}, headers=h)
    events = [json.loads(line) for line in r.data.decode().splitlines()]
    rid = events[-1]["research_id"]
    listed = client.get("/api/research?q=radar").get_json()["research"]
    assert [x["id"] for x in listed] == [rid] and listed[0]["depth"] == "quick"
    note = client.post(f"/api/research/{rid}/note", headers=h).get_json()
    assert note["title"].startswith("Research: automotive radar") and "#research" in note["body"]
    assert "https://vendor.example/radar" in note["body"]
    assert client.delete(f"/api/research/{rid}", headers=h).get_json()["ok"]
    assert client.get(f"/api/research/{rid}").status_code == 404


def test_history_search_finds_research(monkeypatch):
    from babilious.manager import briefing
    engine.save("solid state batteries", "They use a solid electrolyte [1].", [], [], "quick", "m", None)
    hits = briefing.search_history("electrolyte")
    assert any(h["kind"] == "research" for h in hits)


def test_spoken_reply_drops_citation_numbers():
    from babilious.voice.tts import clean_for_speech
    assert clean_for_speech("Radar uses 77 GHz [1][2]. It works in fog [3].") == "Radar uses 77 GHz. It works in fog."
