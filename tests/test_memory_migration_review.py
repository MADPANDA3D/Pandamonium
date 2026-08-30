from src.memory import MemoryManager
from src.memory_import import decide_candidate, preview_manifest, stage_manifest


def _manifest(source_kind, items):
    return {
        "schema_version": "agent-migration.v1",
        "source": {"name": f"{source_kind}-export", "kind": source_kind},
        "items": items,
        "warnings": [],
    }


def test_chatgpt_manifest_preview_is_a_write_free_dry_run(tmp_path):
    manager = MemoryManager(str(tmp_path))
    manifest = _manifest("chatgpt", [
        {
            "id": "memory:one",
            "kind": "memory",
            "text": "Leo prefers concise technical answers",
            "category": "preference",
            "metadata": {"source_conversation_id": "thread-123"},
        },
        {"id": "thread:one", "kind": "conversation_thread"},
    ])

    preview = preview_manifest(manifest, memory_manager=manager, owner="leo")

    assert preview["writes"] == 0
    assert preview["counts"]["memory_candidates"] == 1
    assert preview["counts"]["conversation_threads"] == 1
    assert preview["candidates"][0]["source_ref"] == (
        "agent-migration:chatgpt-export:thread-123"
    )
    assert manager.load_all() == []


def test_generic_manifest_stages_candidates_and_requires_review(tmp_path):
    manager = MemoryManager(str(tmp_path))
    manifest = _manifest("generic", [
        {
            "id": "memory:one",
            "kind": "memory",
            "text": "Leo uses Qdrant as a disposable projection",
            "category": "decision",
            "metadata": {"source_id": "note-7"},
        },
        {
            "id": "memory:secret",
            "kind": "memory",
            "text": "api_key = do-not-import",
            "category": "fact",
        },
    ])

    staged = stage_manifest(manifest, memory_manager=manager, owner="leo")

    assert staged["staged_count"] == 1
    assert manager.load(owner="leo") == []
    candidate = manager.load(owner="leo", statuses=("candidate",))[0]
    assert candidate["source_ref"] == "agent-migration:generic-export:note-7"

    approved = decide_candidate(
        manager,
        candidate["id"],
        owner="leo",
        approve=True,
    )

    assert approved["status"] == "approved"
    assert manager.load(owner="leo")[0]["id"] == candidate["id"]
    assert stage_manifest(manifest, memory_manager=manager, owner="leo")["staged_count"] == 0
