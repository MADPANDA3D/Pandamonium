from src.memory import MemoryManager


def test_legacy_memory_normalizes_into_p3_provenance(tmp_path):
    manager = MemoryManager(str(tmp_path))
    manager.save([{
        "id": "legacy-1",
        "text": "Leo prefers local-first systems",
        "timestamp": 123,
        "owner": "leo",
    }])

    record = manager.load_all()[0]

    assert record["status"] == "approved"
    assert record["source_ref"] == "unknown:legacy-1"
    assert record["source_time"] == 123
    assert record["admitted_at"] == 123
    assert record["admitted_by"] == "legacy"
    assert record["supersedes"] is None


def test_recall_filters_status_before_owner(tmp_path):
    manager = MemoryManager(str(tmp_path))
    approved = manager.add_entry("approved", owner="leo")
    candidate = manager.add_entry("candidate", owner="leo", status="candidate")
    other_owner = manager.add_entry("other", owner="alice")
    manager.save([approved, candidate, other_owner])

    assert [entry["id"] for entry in manager.load(owner="leo")] == [approved["id"]]
    assert [entry["id"] for entry in manager.load(owner="leo", statuses=("candidate",))] == [candidate["id"]]


def test_correction_and_deletion_preserve_provenance_without_recall(tmp_path):
    manager = MemoryManager(str(tmp_path))
    original = manager.add_entry("Old fact", owner="leo")
    manager.save([original])

    replacement = manager.replace_entry(
        original["id"],
        "Corrected fact",
        owner="leo",
        admitted_by="operator",
    )

    assert replacement is not None
    assert replacement["supersedes"] == original["id"]
    assert [entry["id"] for entry in manager.load(owner="leo")] == [replacement["id"]]
    records = {entry["id"]: entry for entry in manager.load_all()}
    assert records[original["id"]]["status"] == "superseded"
    assert records[original["id"]]["superseded_by"] == replacement["id"]

    assert manager.delete_entry(replacement["id"], owner="leo") is True
    assert manager.load(owner="leo") == []
    records = {entry["id"]: entry for entry in manager.load_all()}
    assert records[replacement["id"]]["status"] == "deleted"
