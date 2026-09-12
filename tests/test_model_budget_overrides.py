"""Per-model context-window and input-budget overrides (MAD-897)."""

import src.context_budget as context_budget
import src.model_context as model_context
from src.settings import sanitize_model_number_map


def _settings(monkeypatch, **values):
    payload = {
        "model_context_windows": {},
        "model_input_token_budgets": {},
        "agent_input_token_budget": 6000,
    }
    payload.update(values)
    monkeypatch.setattr(context_budget, "_operator_settings", lambda: payload)
    return payload


def test_unknown_model_without_override_stays_conservative(monkeypatch):
    _settings(monkeypatch)

    assert context_budget.configured_model_window("vendor/mystery-v9") == 0
    assert context_budget.model_input_token_budget("vendor/mystery-v9") == 0


def test_window_override_handles_deepseek_v4_slug(monkeypatch):
    _settings(monkeypatch, model_context_windows={"deepseek-v4.1-flash": 131072})

    assert context_budget.configured_model_window("deepseek/deepseek-v4.1-flash") == 131072
    assert model_context._lookup_known("deepseek/deepseek-v4.1-flash") == 131072


def test_exact_override_beats_substring(monkeypatch):
    _settings(
        monkeypatch,
        model_context_windows={
            "deepseek": 65536,
            "deepseek/deepseek-v4.1-flash": 131072,
        },
    )

    assert context_budget.configured_model_window("deepseek/deepseek-v4.1-flash") == 131072


def test_longest_substring_override_wins(monkeypatch):
    _settings(
        monkeypatch,
        model_context_windows={"deepseek": 65536, "deepseek-v4": 131072},
    )

    assert context_budget.configured_model_window("deepseek/deepseek-v4.1-flash") == 131072


def test_per_model_budget_override_exact(monkeypatch):
    _settings(
        monkeypatch,
        model_input_token_budgets={"deepseek/deepseek-v4.1-flash": 96000},
    )

    assert context_budget.model_input_token_budget("deepseek/deepseek-v4.1-flash") == 96000
    assert context_budget.model_input_token_budget("other-model") == 0


def test_budget_snapshot_reports_settings_window(monkeypatch):
    _settings(monkeypatch, model_context_windows={"deepseek-v4.1-flash": 131072})
    monkeypatch.setattr(model_context, "get_context_length_known", lambda endpoint, model: (0, False))

    snapshot = model_context.model_budget_snapshot(
        "http://api.test/v1", "deepseek/deepseek-v4.1-flash"
    )

    assert snapshot["source"] == "settings"
    assert snapshot["context_window"] == 131072
    assert snapshot["known"] is True
    assert snapshot["effective_input_budget"] == int(131072 * 0.85)


def test_budget_snapshot_unknown_window_stays_small(monkeypatch):
    _settings(monkeypatch)
    monkeypatch.setattr(
        model_context, "get_context_length_known", lambda endpoint, model: (128000, False)
    )

    snapshot = model_context.model_budget_snapshot("http://api.test/v1", "vendor/mystery-v9")

    assert snapshot["known"] is False
    assert snapshot["source"] == "unknown"
    assert snapshot["effective_input_budget"] == 6000


def test_per_model_budget_override_is_explicit(monkeypatch):
    _settings(monkeypatch, model_input_token_budgets={"vendor/mystery-v9": 32000})
    monkeypatch.setattr(
        model_context, "get_context_length_known", lambda endpoint, model: (0, False)
    )

    snapshot = model_context.model_budget_snapshot("http://api.test/v1", "vendor/mystery-v9")

    assert snapshot["explicit"] is True
    assert snapshot["effective_input_budget"] == 32000


def test_model_number_map_sanitizer():
    for bad in ("string", {"m": -1}, {"m": "abc"}, {"": 100}, {1: 100}, {"m": True}):
        try:
            sanitize_model_number_map(bad)
        except ValueError:
            continue
        raise AssertionError(f"invalid map accepted: {bad!r}")

    assert sanitize_model_number_map(None) == {}
    assert sanitize_model_number_map({"deepseek-v4.1-flash": "131072"}) == {
        "deepseek-v4.1-flash": 131072
    }
