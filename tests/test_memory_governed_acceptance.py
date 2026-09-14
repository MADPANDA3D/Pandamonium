"""MAD-786 governed memory acceptance: deterministic multi-turn set.

This is the operator-visible acceptance set for memory governance. It proves
useful recall, correction, deletion, and no recall after deletion, plus owner
scoping, provenance, extraction bounds, and the audit safety net. It runs
fully offline with a scripted extractor model and does not modify app state.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest

from services.memory.memory_extractor import CONTEXT_WINDOW, audit_memories, extract_and_store
from src.memory import MEMORY_STATUSES, MemoryManager
from src.memory_provider import NativeMemoryProvider

FIXTURE = Path(__file__).parent / "fixtures" / "memory" / "governed_recall_v1.json"

OWNER = "leo"


class FakeSession:
    """Minimal session surface used by extract_and_store."""

    def __init__(self, owner: str = OWNER, session_id: str = "sess-memory-acceptance"):
        self.owner = owner
        self.session_id = session_id
        self.name = session_id
        self.history: list = []

    def get_context_messages(self):
        return [dict(message) for message in self.history]

    def add_turn(self, user_text: str, assistant_text: str = "Noted."):
        self.history.append({"role": "user", "content": user_text})
        self.history.append({"role": "assistant", "content": assistant_text})


def _load_fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


class ScriptedExtractor:
    """Deterministic stand-in for the background extraction/audit model."""

    def __init__(self, extraction_responses):
        self._extraction = list(extraction_responses)
        self.extraction_calls: list = []
        self.audit_calls: list = []

    async def __call__(self, endpoint_url, model, messages, **kwargs):
        workload = kwargs.get("workload")
        payload = {
            "endpoint_url": endpoint_url,
            "model": model,
            "messages": messages,
            "kwargs": kwargs,
        }
        if workload == "background" and messages and "memory database curator" in messages[0].get("content", ""):
            self.audit_calls.append(payload)
            # A conservative curator echoes the list unchanged (no fact is
            # silently dropped). Returning [] would mean "keep nothing".
            try:
                return messages[1].get("content", "[]")
            except Exception:
                return "[]"
        self.extraction_calls.append(payload)
        if not self._extraction:
            return "[]"
        return json.dumps(self._extraction.pop(0))


@pytest.fixture()
def memory_env(tmp_path, monkeypatch):
    manager = MemoryManager(str(tmp_path))
    provider = NativeMemoryProvider(manager)
    extractor = ScriptedExtractor([turn["extractor"] for turn in _load_fixture()["turns"]])
    monkeypatch.setattr("src.llm_core.llm_call_async", extractor)
    return manager, provider, extractor


def _store_turns(manager, extractor, fixture):
    session = FakeSession()
    for turn in fixture["turns"]:
        session.add_turn(turn["user"])
        asyncio.run(
            extract_and_store(
                session,
                manager,
                None,
                "http://mock-endpoint/v1",
                "mock-extractor",
                headers={},
            )
        )
    return session


def test_provenance_complete_writes(memory_env):
    manager, _provider, extractor = memory_env
    fixture = _load_fixture()
    _store_turns(manager, extractor, fixture)

    entries = manager.load(owner=OWNER)
    assert entries, "extraction stored nothing"
    for entry in entries:
        assert entry["source"] == "auto"
        assert entry["owner"] == OWNER
        assert entry["status"] in MEMORY_STATUSES
        assert entry["status"] == "approved"
        assert entry["source_ref"]
        assert entry["source_time"] > 0
        assert entry["admitted_at"] > 0
        assert entry["admitted_by"] == "policy:auto_memory"
        assert entry.get("confidence") is not None, "memory writes must carry confidence"
        assert 0.0 <= float(entry["confidence"]) <= 1.0


def test_useful_recall_is_relevant_bounded_and_deduplicated(memory_env):
    manager, provider, extractor = memory_env
    fixture = _load_fixture()
    _store_turns(manager, extractor, fixture)

    metrics = {"useful": 0, "queries": 0}
    for case in fixture["useful_recall"]:
        metrics["queries"] += 1
        start = time.perf_counter()
        hits = asyncio.run(provider.recall(case["query"], owner=OWNER, top_k=case["top_k"]))
        metrics["latency_ms"] = max(metrics.get("latency_ms", 0.0), (time.perf_counter() - start) * 1000)
        texts = [hit.memory.text for hit in hits]
        assert len(hits) <= case["top_k"], "recall must stay bounded"
        assert len(texts) == len(set(texts)), "recall must be deduplicated"
        assert all(hit.memory.owner == OWNER for hit in hits)
        if any(any(expected.lower() in text.lower() for expected in case["expect_any"]) for text in texts):
            metrics["useful"] += 1
    assert metrics["useful"] == metrics["queries"], metrics


def test_false_recall_stays_empty(memory_env):
    manager, provider, extractor = memory_env
    fixture = _load_fixture()
    _store_turns(manager, extractor, fixture)

    false_hits = 0
    for case in fixture["false_recall"]:
        hits = asyncio.run(provider.recall(case["query"], owner=OWNER, top_k=5))
        for hit in hits:
            text = hit.memory.text.lower()
            if any(absent.lower() in text for absent in case["expect_absent"]):
                false_hits += 1
    assert false_hits == 0, f"false recall returned {false_hits} unrelated memories"


def test_correction_supersedes_and_recall_uses_new_fact(memory_env):
    manager, provider, extractor = memory_env
    fixture = _load_fixture()
    _store_turns(manager, extractor, fixture)
    correction = fixture["correction"]

    original = next(
        entry for entry in manager.load(owner=OWNER) if correction["old_contains"] in entry["text"]
    )
    replacement = manager.replace_entry(
        original["id"],
        correction["new_text"],
        owner=OWNER,
        admitted_by="operator",
    )
    assert replacement is not None
    assert replacement["supersedes"] == original["id"]
    assert replacement["admitted_by"] == "operator"
    assert float(replacement["confidence"]) == 1.0

    hits = asyncio.run(provider.recall(correction["recall_query"], owner=OWNER, top_k=5))
    texts = [hit.memory.text for hit in hits]
    assert any(correction["expect_new"] in text for text in texts)
    assert not any(correction["old_contains"] in text for text in texts)
    records = {entry["id"]: entry for entry in manager.load_all()}
    assert records[original["id"]]["status"] == "superseded"


def test_deletion_removes_recall_and_keeps_deletion_path(memory_env):
    manager, provider, extractor = memory_env
    fixture = _load_fixture()
    _store_turns(manager, extractor, fixture)
    deletion = fixture["deletion"]

    target = next(
        entry for entry in manager.load(owner=OWNER) if deletion["contains"] in entry["text"]
    )
    assert manager.delete_entry(target["id"], owner=OWNER, deleted_by="operator") is True

    hits = asyncio.run(provider.recall(deletion["recall_query"], owner=OWNER, top_k=5))
    assert not any(deletion["contains"] in hit.memory.text for hit in hits)
    assert manager.load(owner=OWNER) and all(
        entry["id"] != target["id"] for entry in manager.load(owner=OWNER)
    )
    tombstone = next(entry for entry in manager.load_all() if entry["id"] == target["id"])
    assert tombstone["status"] == "deleted"
    assert tombstone["deleted_by"] == "operator"
    assert tombstone["deleted_at"] > 0


def test_no_cross_owner_recall_or_delete(memory_env):
    manager, provider, extractor = memory_env
    fixture = _load_fixture()
    _store_turns(manager, extractor, fixture)
    isolation = fixture["owner_isolation"]

    for fact in isolation["owner_b_facts"]:
        entry = manager.add_entry(fact["text"], source="user", owner="dana", confidence=1.0)
        manager.save(manager.load_all() + [entry])

    dana_hits = asyncio.run(
        provider.recall(isolation["owner_b_query"], owner="dana", top_k=5)
    )
    dana_texts = [hit.memory.text for hit in dana_hits]
    assert any(isolation["owner_b_expect"] in text for text in dana_texts)
    assert not any(isolation["owner_b_must_not_see"] in text for text in dana_texts)

    leo_hits = asyncio.run(provider.recall(isolation["owner_b_query"], owner=OWNER, top_k=5))
    assert not any(isolation["owner_a_must_not_see"] in hit.memory.text for hit in leo_hits)

    dana_entry = next(entry for entry in manager.load(owner="dana"))
    assert manager.delete_entry(dana_entry["id"], owner=OWNER) is False
    assert [entry["id"] for entry in manager.load(owner="dana")] == [dana_entry["id"]]


def test_extraction_is_background_bounded_and_owner_scoped(memory_env):
    manager, provider, extractor = memory_env
    fixture = _load_fixture()
    session = _store_turns(manager, extractor, fixture)
    budget = fixture["extraction_budget"]

    assert extractor.extraction_calls, "extractor was never called"
    for call in extractor.extraction_calls:
        assert call["kwargs"].get("workload") == "background"
        assert call["kwargs"].get("max_retries") == 1
        assert len(call["messages"]) == 2, "one system message plus one flattened transcript"
        transcript_lines = [
            line for line in call["messages"][1]["content"].splitlines() if line.strip()
        ]
        assert len(transcript_lines) <= budget["max_messages_in_window"] + 2
    entries = manager.load_all()
    assert all(entry.get("owner") == OWNER for entry in entries)
    assert len(manager.load(owner=OWNER)) <= budget["max_added_per_turn"] * len(fixture["turns"])

    # The recorder in this fixture outlives the window on purpose; the
    # extractor must only ever see the bounded recent slice.
    assert len(session.history) >= 2


def test_token_cost_stays_within_recalled_memory_budget(memory_env):
    manager, _provider, extractor = memory_env
    fixture = _load_fixture()
    _store_turns(manager, extractor, fixture)

    from src.context_budget import compute_input_token_budget, context_class_budget_percent
    from src.model_context import estimate_tokens

    effective = compute_input_token_budget(0, 32768, False)
    recalled_budget = effective * context_class_budget_percent().get("recalled_memory", 15) // 100
    entries = manager.load(owner=OWNER)
    injected = "\n".join(f"- {entry['text']}" for entry in entries)
    token_cost = estimate_tokens([{"role": "user", "content": injected}])
    assert token_cost <= recalled_budget, (token_cost, recalled_budget)
    assert token_cost > 0


def test_fallback_name_capture_does_not_swallow_following_sentence():
    from services.memory.memory_extractor import _fallback_memory_candidates

    candidates = _fallback_memory_candidates(
        [{"role": "user", "content": "My name is Leo and I live in Austin."}]
    )
    texts = [candidate["text"] for candidate in candidates]
    assert "User's name is Leo" in texts
    assert not any("Austin" in text and "name" in text.lower() for text in texts)


def test_audit_safety_net_refuses_catastrophic_removal(memory_env, monkeypatch):
    manager, _provider, _extractor = memory_env
    for index in range(10):
        entry = manager.add_entry(f"Fact number {index}", source="auto", owner=OWNER)
        manager.save(manager.load_all() + [entry])
    before = len(manager.load(owner=OWNER))
    assert before >= 8

    async def _misfiring_audit(*args, **kwargs):
        return json.dumps([{"id": manager.load(owner=OWNER)[0]["id"], "text": "only one survives"}])

    monkeypatch.setattr("src.llm_core.llm_call_async", _misfiring_audit)
    result = asyncio.run(
        audit_memories(manager, None, "http://mock/v1", "mock-audit", owner=OWNER)
    )
    assert result.get("error") == "unsafe_removal"
    assert len(manager.load(owner=OWNER)) == before


def test_low_confidence_candidates_are_not_recallable_until_promoted(tmp_path):
    from src.memory_import import decide_candidate, stage_manifest

    manager = MemoryManager(str(tmp_path))
    provider = NativeMemoryProvider(manager)
    manifest = {
        "schema_version": "agent-migration.v1",
        "source": {"name": "generic-export", "kind": "generic"},
        "items": [
            {"id": "memory:one", "kind": "memory", "text": "User ships on Fridays", "category": "fact"}
        ],
        "warnings": [],
    }
    staged = stage_manifest(manifest, memory_manager=manager, owner=OWNER)
    assert staged["staged_count"] == 1
    candidate = manager.load(owner=OWNER, statuses=("candidate",))[0]
    assert candidate["status"] == "candidate"

    before = asyncio.run(provider.recall("when do I ship", owner=OWNER, top_k=5))
    assert not any("Fridays" in hit.memory.text for hit in before)

    rejected = decide_candidate(manager, candidate["id"], owner=OWNER, approve=False)
    assert rejected is not None and rejected["status"] == "rejected"
    still_hidden = asyncio.run(provider.recall("when do I ship", owner=OWNER, top_k=5))
    assert not any("Fridays" in hit.memory.text for hit in still_hidden)

    second = stage_manifest(manifest, memory_manager=manager, owner=OWNER)
    assert second["staged_count"] == 0
    pending = manager.load(owner=OWNER, statuses=("candidate",))
    assert pending == [] or pending[0]["id"] != candidate["id"]


def test_acceptance_metrics_are_reported(memory_env, capsys):
    manager, provider, extractor = memory_env
    fixture = _load_fixture()
    _store_turns(manager, extractor, fixture)

    useful = 0
    started = time.perf_counter()
    for case in fixture["useful_recall"]:
        hits = asyncio.run(provider.recall(case["query"], owner=OWNER, top_k=case["top_k"]))
        if any(
            any(expected.lower() in hit.memory.text.lower() for expected in case["expect_any"])
            for hit in hits
        ):
            useful += 1
    latency_ms = (time.perf_counter() - started) * 1000

    false_recall = 0
    for case in fixture["false_recall"]:
        hits = asyncio.run(provider.recall(case["query"], owner=OWNER, top_k=5))
        false_recall += sum(
            1
            for hit in hits
            if any(absent.lower() in hit.memory.text.lower() for absent in case["expect_absent"])
        )

    stale = 0
    for entry in manager.load_all():
        if entry.get("status") in {"superseded", "deleted"} and entry["id"] in {
            hit.memory.id
            for case in fixture["useful_recall"]
            for hit in asyncio.run(provider.recall(case["query"], owner=OWNER, top_k=5))
        }:
            stale += 1

    metrics = {
        "recall_quality": f"{useful}/{len(fixture['useful_recall'])}",
        "false_recall": false_recall,
        "stale_facts": stale,
        "latency_ms": round(latency_ms, 2),
        "memories": len(manager.load(owner=OWNER)),
        "expected_window": CONTEXT_WINDOW,
    }
    print(json.dumps(metrics, sort_keys=True))
    assert useful == len(fixture["useful_recall"])
    assert false_recall == 0
    assert stale == 0
    assert latency_ms >= 0.0