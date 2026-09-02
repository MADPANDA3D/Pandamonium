import asyncio

import src.llm_core
from services.memory import memory_extractor


def test_fingerprint_entries_skips_invalid_rows():
    value = memory_extractor._fingerprint_entries([
        {"id": "1", "text": "User likes small PRs.", "category": "preference"},
        "bad-row",
        None,
    ])

    expected = memory_extractor._fingerprint_entries([
        {"id": "1", "text": "User likes small PRs.", "category": "preference"},
    ])

    assert value == expected


def test_duplicate_check_skips_invalid_rows():
    existing = [
        "bad-row",
        {"text": "User likes small pull requests."},
        None,
    ]

    assert memory_extractor._is_text_duplicate("User likes small pull requests.", existing)


def test_automatic_audit_uses_background_single_attempt(monkeypatch, tmp_path):
    calls = []

    async def fake_llm_call_async(*args, **kwargs):
        calls.append(kwargs)
        raise RuntimeError("stop after capturing call options")

    class MemoryManager:
        memory_file = str(tmp_path / "memory.json")

        @staticmethod
        def load(owner=None):
            return [{"id": "m1", "text": "User likes maps.", "category": "preference"}]

    monkeypatch.setattr(src.llm_core, "llm_call_async", fake_llm_call_async)

    result = asyncio.run(memory_extractor.audit_memories(
        MemoryManager(),
        None,
        "http://endpoint",
        "test-model",
        owner="alice",
        background=True,
    ))

    assert result["error"] == "stop after capturing call options"
    assert calls[0]["workload"] == "background"
    assert calls[0]["max_retries"] == 1
