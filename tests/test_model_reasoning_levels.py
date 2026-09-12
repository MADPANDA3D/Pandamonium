"""Reasoning-effort control for API reasoning models (MAD-900).

Covers the provider/model capability resolver, the payload mapping for the
OpenAI-compatible providers, and the source seams that carry an operator
reasoning level from the composer to the outgoing turn.
"""
import asyncio
import json
from pathlib import Path

from src import llm_core

REPO = Path(__file__).resolve().parent.parent


# ── Capability resolver ──

def test_reasoning_levels_openrouter_reasoning_models():
    assert llm_core.reasoning_levels("openrouter", "deepseek/deepseek-v4.1-flash") == ("low", "medium", "high")
    assert llm_core.reasoning_levels("openrouter", "qwen/qwen3-235b-a22b") == ("low", "medium", "high")
    assert llm_core.reasoning_levels("openrouter", "openai/gpt-5-mini") == ("low", "medium", "high")


def test_reasoning_levels_plain_models_empty():
    assert llm_core.reasoning_levels("openrouter", "meta-llama/llama-3.1-8b-instruct") == ()
    assert llm_core.reasoning_levels("openai", "gpt-4o-mini") == ()
    assert llm_core.reasoning_levels("ollama", "qwen3:8b") == ()
    assert llm_core.reasoning_levels("openrouter", "") == ()


def test_reasoning_levels_openai_and_mistral():
    assert llm_core.reasoning_levels("openai", "o3-mini") == ("low", "medium", "high")
    assert llm_core.reasoning_levels("openai", "gpt-5.1") == ("low", "medium", "high")
    assert llm_core.reasoning_levels("mistral", "mistral-medium-latest") == ("low", "medium", "high")
    assert llm_core.reasoning_levels("mistral", "open-mistral-nemo") == ()


def test_reasoning_levels_for_url_uses_provider_detection():
    assert llm_core.reasoning_levels_for_url(
        "https://openrouter.ai/api/v1/chat/completions", "deepseek/deepseek-v4.1-flash",
    ) == ("low", "medium", "high")
    assert llm_core.reasoning_levels_for_url(
        "http://127.0.0.1:11434/v1/chat/completions", "deepseek-r1:8b",
    ) == ()


# ── Payload mapping ──

def test_apply_reasoning_effort_maps_provider_fields():
    payload = {}
    assert llm_core.apply_reasoning_effort(payload, "openrouter", "deepseek/deepseek-v4.1-flash", "high") is True
    assert payload["reasoning"] == {"effort": "high"}

    payload = {}
    assert llm_core.apply_reasoning_effort(payload, "openai", "o3-mini", "low") is True
    assert payload["reasoning_effort"] == "low"

    payload = {}
    assert llm_core.apply_reasoning_effort(payload, "mistral", "mistral-medium-latest", "medium") is True
    assert payload["reasoning_effort"] == "medium"


def test_apply_reasoning_effort_rejects_unknown_or_unsupported():
    payload = {"reasoning_effort": "high"}
    assert llm_core.apply_reasoning_effort(payload, "openrouter", "deepseek/deepseek-v4.1-flash", "ultra") is False
    assert payload == {"reasoning_effort": "high"}  # untouched

    payload = {}
    assert llm_core.apply_reasoning_effort(payload, "openrouter", "meta-llama/llama-3.1-8b-instruct", "high") is False
    assert payload == {}

    payload = {}
    assert llm_core.apply_reasoning_effort(payload, "ollama", "qwen3:8b", "high") is False
    assert payload == {}


# ── Stream payload integration ──

class _FakeResp:
    status_code = 200

    def __init__(self, lines):
        self._lines = lines

    async def aiter_lines(self):
        for ln in self._lines:
            yield ln

    async def aread(self):
        return b""


class _CaptureClient:
    def __init__(self):
        self.payloads = []

    def stream(self, method, url, **kwargs):
        self.payloads.append(kwargs.get("json"))

        class _Ctx:
            async def __aenter__(self_inner):
                return _FakeResp(["data: [DONE]"])

            async def __aexit__(self_inner, *exc):
                return False

        return _Ctx()


def _stream_once(url, model, monkeypatch, **kwargs):
    client = _CaptureClient()
    monkeypatch.setattr(llm_core, "_get_http_client", lambda: client)
    monkeypatch.setattr(llm_core, "_is_host_dead", lambda url: False)
    monkeypatch.setattr(llm_core, "note_model_activity", lambda *a, **k: None)

    async def _go():
        async for _ in llm_core.stream_llm(url, model, [{"role": "user", "content": "hi"}], **kwargs):
            pass

    asyncio.run(_go())
    return client.payloads[0]


def test_stream_payload_gets_reasoning_effort(monkeypatch):
    payload = _stream_once(
        "https://openrouter.ai/api/v1/chat/completions",
        "deepseek/deepseek-v4.1-flash",
        monkeypatch,
        reasoning_effort="high",
    )
    assert payload.get("reasoning") == {"effort": "high"}


def test_stream_payload_without_reasoning_effort_unchanged(monkeypatch):
    payload = _stream_once(
        "https://openrouter.ai/api/v1/chat/completions",
        "deepseek/deepseek-v4.1-flash",
        monkeypatch,
    )
    assert "reasoning" not in payload
    assert "reasoning_effort" not in payload


def test_stream_payload_rejects_unsupported_model(monkeypatch):
    payload = _stream_once(
        "https://openrouter.ai/api/v1/chat/completions",
        "meta-llama/llama-3.1-8b-instruct",
        monkeypatch,
        reasoning_effort="high",
    )
    assert "reasoning" not in payload
    assert "reasoning_effort" not in payload


# ── Source seams ──

def test_chat_routes_validates_and_forwards_reasoning_effort():
    source = (REPO / "routes" / "chat_routes.py").read_text()
    assert "REASONING_EFFORT_LEVELS" in source
    assert "Invalid reasoning effort" in source
    assert "reasoning_effort=reasoning_effort or None" in source


def test_model_routes_exposes_reasoning_levels_map():
    source = (REPO / "routes" / "model_routes.py").read_text()
    assert "reasoning_levels" in source


def test_agent_loop_threads_reasoning_effort():
    source = (REPO / "src" / "agent_loop.py").read_text()
    assert "reasoning_effort: Optional[str] = None" in source
    assert source.count("reasoning_effort=reasoning_effort") >= 3


def test_composer_switches_to_reasoning_mode():
    source = (REPO / "static" / "js" / "conversationContext.js").read_text()
    assert "activeModelReasoningLevels" in source
    assert "reasoning_levels" in source
    assert "getReasoningEffort" in source
    assert "reasoningMode" in source


def test_chat_posts_reasoning_effort():
    source = (REPO / "static" / "js" / "chat.js").read_text()
    assert "getReasoningEffort" in source
    assert "fd.append('reasoning_effort'" in source
