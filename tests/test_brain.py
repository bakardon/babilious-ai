"""Persona, knowledge retrieval, commands and model selection."""
import character
from babilious import db, intents, knowledge, memory, models, persona, tasks


def test_system_prompt_is_model_independent():
    prompt = persona.system_prompt("Abubakar")
    assert "Babilious" in prompt and "Abubakar" in prompt
    assert "Never invent memories" in prompt
    assert "shirtless" in prompt and "jaded genius" in prompt
    # Nothing in the persona mentions a specific model, so switching models keeps identity.
    for name in ("llama", "qwen", "gemma", "phi"):
        assert name not in prompt.lower()
    db.set_setting("active_model", "qwen2.5-coder:3b")
    assert persona.system_prompt("Abubakar") == prompt


def test_legacy_persona_constant_still_exported():
    assert character.BABILIOUS_PERSONA and character.CHARACTER_NAME == "Babilious"


def test_knowledge_search_finds_projects():
    hits = knowledge.search("What projects has he built?")
    assert hits and hits[0][1].source == "projects.md"
    notes = knowledge.relevant_notes("Tell me about City Sentinel")
    assert any("City Sentinel" in src for src, _ in notes)
    assert knowledge.relevant_notes("asdf qwerty zzz") == []


def test_intent_create_and_list_tasks(fake):
    r = intents.handle("Babilious, create a task to finish my GitHub portfolio", fake)
    assert r.action == "task_created"
    assert any(t["title"] == "Finish my GitHub portfolio" for t in tasks.list_tasks())
    r = intents.handle("What's unfinished?", fake)
    assert r.action == "task_list" and "Finish my GitHub portfolio" in r.text


def test_intent_remember(fake):
    r = intents.handle("Remember that I prefer short answers", fake)
    assert r.action == "memory_saved"
    saved = memory.list_memories("short answers")[0]
    assert saved["kind"] == "preference" and saved["source"] == "chat"


def test_switch_to_faster_model_skips_coder_and_tiny_differences(fake):
    r = intents.handle("Babilious, switch to the faster model.", fake)
    assert r.action == "model_switch"
    assert models.active_model() == "llama3.2:1b"
    assert "llama3.2:latest" in fake.unloaded  # old model freed from RAM
    # From 1b there is nothing faster.
    r = intents.handle("switch to the faster model", fake)
    assert r.action == "model_switch_failed"


def test_switch_without_small_model_explains(fake):
    fake.models = [m for m in fake.models if m["name"] != "llama3.2:1b"]
    r = intents.handle("switch to the faster model", fake)
    # qwen2.5-coder is only ~5% smaller and is a coding model: not "faster".
    assert r.action == "model_switch_failed" and "llama3.2:1b" in r.text
    assert models.active_model() == "llama3.2:latest"


def test_switch_by_name_and_coding(fake):
    assert intents.handle("use the coding model", fake).data["model"] == "qwen2.5-coder:3b"
    assert intents.handle("switch to llama3.2:latest", fake).data["model"] == "llama3.2:latest"
    assert "llama3.2:latest" in intents.handle("which model are you using?", fake).text


def test_unbuilt_abilities_are_honest(fake, monkeypatch):
    import re
    monkeypatch.setattr(intents, "PENDING", [(re.compile(r"\bteleport\b", re.I), "Teleporting", 9)])
    r = intents.handle("teleport me to Lahore", fake)
    assert r and r.action == "unsupported"
    assert "can't do that yet" in r.text and "Milestone 9" in r.text
    # Milestone 6 abilities are handled elsewhere now, not refused here.
    for text in ("research quantum radar", "What's playing right now?"):
        assert intents.handle(text, fake) is None, text
    # Milestone 4 abilities are no longer claimed to be missing.
    for text in ("Open VS Code", "Take a screenshot", "Create a folder called Research"):
        assert intents.handle(text, fake) is None, text


def test_normal_chat_is_not_hijacked(fake):
    for text in ("How do I open a file in Python?", "What are we doing today?", "Explain recursion"):
        assert intents.handle(text, fake) is None, text


def test_mood_heuristics():
    assert intents.mood_for("You're annoying", "And yet...") == "annoyed"
    assert intents.mood_for("thanks!", "Anytime") == "happy"
    assert intents.mood_for("hm", "Which one do you mean?") == "curious"
