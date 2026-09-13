"""MAD-860: first-run model readiness is a four-state model.

configured -> discovered -> validated, or failed. Saved endpoint/model fields
alone never mean "ready"; only a successful minimal completion does.
"""
import json
from types import SimpleNamespace

import pytest

import core.database as core_database
import routes.setup_routes as setup_routes
from src import model_response_diagnosis as mrd


class _FakeQuery:
    def __init__(self, endpoints):
        self._endpoints = endpoints

    def filter(self, *args, **kwargs):
        return self

    def all(self):
        return list(self._endpoints)


class _FakeDB:
    def __init__(self, endpoints):
        self._endpoints = endpoints

    def query(self, _model):
        return _FakeQuery(self._endpoints)

    def close(self):
        pass


@pytest.fixture
def isolated_settings(monkeypatch, tmp_path):
    import src.settings as settings_module

    target = tmp_path / "settings.json"
    monkeypatch.setattr(settings_module, "SETTINGS_FILE", target)
    settings_module._invalidate_caches()
    yield target
    settings_module._invalidate_caches()


def _endpoint(**overrides):
    values = {
        "id": "ep-1",
        "is_enabled": True,
        "model_type": "llm",
        "cached_models": '["gpt-4o-mini"]',
        "hidden_models": None,
        "pinned_models": None,
        "owner": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _patch_endpoints(monkeypatch, endpoints):
    monkeypatch.setattr(core_database, "SessionLocal", lambda: _FakeDB(endpoints))


def test_no_endpoints_is_unconfigured(monkeypatch, isolated_settings):
    _patch_endpoints(monkeypatch, [])
    result = setup_routes._project_model("", True)
    assert result["state"] == "unconfigured"
    assert result["usable"] is False


def test_saved_endpoint_without_models_is_configured(monkeypatch, isolated_settings):
    _patch_endpoints(monkeypatch, [_endpoint(cached_models=None, pinned_models=None)])
    result = setup_routes._project_model("", True)
    assert result["state"] == "configured"
    assert result["usable"] is False


def test_cached_models_alone_are_discovered_not_usable(monkeypatch, isolated_settings):
    _patch_endpoints(monkeypatch, [_endpoint()])
    result = setup_routes._project_model("", True)
    assert result["state"] == "discovered"
    assert result["usable"] is False
    assert "validate" in (result.get("guidance") or "").lower()


def test_validated_minimal_completion_marks_usable(monkeypatch, isolated_settings):
    _patch_endpoints(monkeypatch, [_endpoint()])
    mrd.record_model_validation("ep-1", "gpt-4o-mini", "ok")
    result = setup_routes._project_model("", True)
    assert result["state"] == "validated"
    assert result["usable"] is True
    assert result["validated"] == 1


def test_failed_validation_reports_redacted_category_and_guidance(monkeypatch, isolated_settings):
    _patch_endpoints(monkeypatch, [_endpoint()])
    mrd.record_model_validation("ep-1", "gpt-4o-mini", "authentication")
    result = setup_routes._project_model("", True)
    assert result["state"] == "failed"
    assert result["usable"] is False
    assert result["last_failure"]["category"] == "authentication"
    assert "Validate settings" in result["last_failure"]["guidance"]
    assert "base_url" not in json.dumps(result)
    assert "api_key" not in json.dumps(result)


def test_validation_for_hidden_model_does_not_mark_usable(monkeypatch, isolated_settings):
    _patch_endpoints(monkeypatch, [
        _endpoint(cached_models='["hidden-one"]', hidden_models='["hidden-one"]'),
    ])
    mrd.record_model_validation("ep-1", "hidden-one", "ok")
    result = setup_routes._project_model("", True)
    assert result["usable"] is False
    assert result["state"] == "configured"