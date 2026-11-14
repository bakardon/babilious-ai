import json

from babilious import db
from conftest import H


def _events(resp):
    return [json.loads(line) for line in resp.get_data(as_text=True).splitlines() if line.strip()]


def test_home_page_renders_dog(client):
    html = client.get("/").get_data(as_text=True)
    assert 'id="dog"' in html and "Second Brain" in html


def test_state_changes_require_header(client):
    assert client.post("/api/tasks", json={"title": "x"}).status_code == 403
    r = client.post("/api/tasks", json={"title": "x"}, headers={**H, "Origin": "https://evil.example"})
    assert r.status_code == 403
    assert client.post("/api/tasks", json={"title": "x"}, headers=H).status_code == 201


def test_chat_stream_uses_llm_with_persona_and_context(client):
    r = client.post("/api/chat/stream", json={"message": "Tell me about City Sentinel"}, headers=H)
    events = _events(r)
    types = [e["type"] for e in events]
    assert types[0] == "conversation" and "token" in types and types[-1] == "done"
    done = events[-1]
    assert done["source"] == "llm" and done["text"].startswith("Fine.")
    msgs = client.fake.last_messages
    assert msgs[0]["role"] == "system" and "Babilious" in msgs[0]["content"]
    assert "City Sentinel" in msgs[-2]["content"]  # retrieved note in per-turn context
    assert msgs[-1] == {"role": "user", "content": "Tell me about City Sentinel"}
    # history persisted
    cid = events[0]["id"]
    convo = client.get(f"/api/conversations/{cid}").get_json()
    assert [m["role"] for m in convo["messages"]] == ["user", "assistant"]


def test_follow_up_includes_history(client):
    first = _events(client.post("/api/chat/stream", json={"message": "My favourite language is Python"}, headers=H))
    cid = first[0]["id"]
    _events(client.post("/api/chat/stream", json={"message": "What did I just say?", "conversation_id": cid},
                        headers=H))
    contents = [m["content"] for m in client.fake.last_messages]
    assert "My favourite language is Python" in contents
    assert contents.count("What did I just say?") == 1


def test_offline_fallback(client):
    client.fake.available = False
    r = client.post("/api/chat", json={"message": "What projects has he built?"}, headers=H)
    data = r.get_json()
    assert r.status_code == 200 and data["source"] == "offline"
    assert "offline" in data["answer"] and "projects.md" in data["answer"]


def test_missing_model_error_is_helpful(client):
    client.fake.error = "model 'llama3.2:latest' not found"
    r = client.post("/api/chat", json={"message": "hello there"}, headers=H)
    assert r.status_code == 502 and "ollama pull" in r.get_json()["error"]


def test_commands_through_api(client):
    r = client.post("/api/chat", json={"message": "add a task to email professor"}, headers=H).get_json()
    assert r["source"] == "command"
    titles = [t["title"] for t in client.get("/api/tasks").get_json()["tasks"]]
    assert "Email professor" in titles


def test_empty_and_huge_messages_rejected(client):
    assert client.post("/api/chat/stream", json={"message": "  "}, headers=H).status_code == 400
    assert client.post("/api/chat/stream", json={"message": "x" * 9000}, headers=H).status_code == 400


def test_tasks_and_memories_api(client):
    tid = client.post("/api/tasks", json={"title": "Write README", "due_date": "2026-11-20"}, headers=H).get_json()["id"]
    assert client.patch(f"/api/tasks/{tid}", json={"status": "in_progress"}, headers=H).status_code == 200
    assert client.patch(f"/api/tasks/{tid}", json={"status": "wat"}, headers=H).status_code == 400
    assert client.delete(f"/api/tasks/{tid}", headers=H).status_code == 200
    assert client.delete(f"/api/tasks/{tid}", headers=H).status_code == 404

    mid = client.post("/api/memories", json={"content": "Studies at GIKI", "kind": "fact"}, headers=H).get_json()["id"]
    assert any(m["id"] == mid for m in client.get("/api/memories?q=GIKI").get_json()["memories"])
    assert client.delete(f"/api/memories/{mid}", headers=H).status_code == 200


def test_models_api(client):
    data = client.get("/api/models").get_json()
    assert data["ollama"] and data["active"] == "llama3.2:latest"
    assert any(m["active"] for m in data["installed"])
    assert all(not s["installed"] for s in data["suggestions"])
    assert client.post("/api/models/active", json={"name": "qwen2.5-coder:3b"}, headers=H).get_json()["active"] == "qwen2.5-coder:3b"
    assert client.post("/api/models/active", json={"name": "not-a-model"}, headers=H).status_code == 400
    bench = client.post("/api/models/benchmark", json={"name": "llama3.2:1b"}, headers=H).get_json()
    assert bench["tokens_per_sec"] == 12.0
    # cannot remove the active model
    assert client.delete("/api/models/qwen2.5-coder:3b", headers=H).status_code == 400
    assert client.delete("/api/models/llama3.2:1b", headers=H).status_code == 200


def test_settings_validation(client):
    assert client.patch("/api/settings", json={"temperature": "0.3"}, headers=H).status_code == 200
    assert db.get_setting("temperature") == 0.3
    assert client.patch("/api/settings", json={"num_ctx": "lots"}, headers=H).status_code == 400
    assert client.patch("/api/settings", json={"active_model": "x"}, headers=H).status_code == 400


def test_status_and_health(client):
    s = client.get("/api/status").get_json()
    assert s["ollama"] and "cpu_percent" in s["resources"]
    checks = {c["name"]: c["status"] for c in client.get("/api/health").get_json()["checks"]}
    assert checks["Ollama server"] == "ok" and checks["Database"] == "ok"
    client.fake.available = False
    checks = {c["name"]: c["status"] for c in client.get("/api/health").get_json()["checks"]}
    assert checks["Ollama server"] == "error"
