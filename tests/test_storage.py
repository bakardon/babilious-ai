import pytest

from babilious import db, memory, tasks


def test_legacy_json_imported_once():
    mems = memory.list_memories()
    contents = [m["content"] for m in mems]
    assert "Build a memorable AI companion" in contents
    # Duplicate "Started ... Babilious" entries in the old log are de-duplicated by text.
    assert len(contents) == len(set(c.lower() for c in contents))
    titles = [t["title"] for t in tasks.list_tasks()]
    assert "Push Babilious to GitHub" in titles
    before = (len(mems), len(titles))
    db.migrate_legacy_json()  # second run is a no-op
    assert (len(memory.list_memories()), len(tasks.list_tasks())) == before


def test_settings_roundtrip_and_defaults():
    assert db.get_setting("num_ctx") == 4096
    db.set_setting("num_ctx", 2048)
    assert db.get_setting("num_ctx") == 2048
    assert db.all_settings()["num_ctx"] == 2048


def test_task_lifecycle_and_validation():
    tid = tasks.create_task("Finish GitHub portfolio", priority=1, due_date="2026-12-01")
    assert tasks.current_task()["id"] == tid  # high priority goes first
    assert tasks.update_task(tid, status="done")
    done = [t for t in tasks.list_tasks("done") if t["id"] == tid][0]
    assert done["completed_at"]
    with pytest.raises(ValueError):
        tasks.create_task("   ")
    with pytest.raises(ValueError):
        tasks.create_task("x", due_date="2026-13-40")
    with pytest.raises(ValueError):
        tasks.update_task(tid, status="nonsense")
    assert tasks.delete_task(tid)
    assert not tasks.delete_task(tid)


def test_memory_crud():
    mid = memory.add_memory("Prefers dark mode", kind="preference")
    assert memory.update_memory(mid, content="Prefers dark mode everywhere", pinned=True)
    top = memory.list_memories()[0]
    assert top["id"] == mid and top["pinned"] == 1
    assert "dark mode everywhere" in memory.memory_context()
    with pytest.raises(ValueError):
        memory.update_memory(mid, kind="bogus")
    assert memory.delete_memory(mid)


def test_conversations_and_titles():
    cid = memory.create_conversation()
    memory.add_message(cid, "user", "Help me organise my master's applications please")
    memory.add_message(cid, "assistant", "Sure.")
    conv = [c for c in memory.list_conversations() if c["id"] == cid][0]
    assert conv["title"].startswith("Help me organise")
    assert conv["message_count"] == 2
    assert [m["role"] for m in memory.get_messages(cid, limit=1)] == ["assistant"]
    assert memory.delete_conversation(cid)
    assert memory.get_messages(cid) == []
