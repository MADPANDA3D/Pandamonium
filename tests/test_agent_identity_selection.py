"""MAD-929: identity resolution, prompt mounting, and session binding."""

import pytest
from fastapi import HTTPException

import src.agent_identities as identities
import src.agent_identity as agent_identity
from src.settings import DEFAULT_SETTINGS


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(identities, "_IDENTITIES_FILE", str(tmp_path / "agent_identities.json"))
    monkeypatch.setattr(identities, "load_settings", lambda: dict(DEFAULT_SETTINGS))
    return tmp_path / "agent_identities.json"


def _create(display_name, constitution, version="1", profile=None):
    return identities.create_identity(
        {
            "display_name": display_name,
            "constitution": constitution,
            "constitution_version": version,
            "model_profile": profile or {},
        }
    )


def test_resolution_ignores_store_when_no_registry_file_exists(monkeypatch):
    monkeypatch.setattr(
        agent_identity, "load_settings", lambda: {**DEFAULT_SETTINGS, "agent_display_name": "Atlas"}
    )
    monkeypatch.setattr(identities, "_IDENTITIES_FILE", "/nonexistent/agent_identities.json")

    identity = agent_identity.resolve_agent_identity()

    assert identity["agent_display_name"] == "Atlas"


def test_resolution_prefers_persisted_registry_active_entry(store, monkeypatch):
    _create("Jarvis", "Guard the operator's context.", "2")
    identities.set_active_identity("jarvis")
    monkeypatch.setattr(
        agent_identity, "load_settings", lambda: {**DEFAULT_SETTINGS, "agent_display_name": "Atlas"}
    )

    identity = agent_identity.resolve_agent_identity()

    assert identity["agent_display_name"] == "Jarvis"
    assert identity["agent_constitution"] == "Guard the operator's context."
    assert identity["agent_id"] == "jarvis"
    assert identity["status"] == "healthy"


def test_prompt_mounts_session_identity_and_falls_back_when_missing(store):
    _create("Jarvis", "Protect the operator's context first.", "3")
    _create("Friday", "Report every uncertainty as uncertainty.", "4")

    jarvis_prompt = agent_identity.agent_system_prompt("Be concise.", identity_id="jarvis")
    friday_prompt = agent_identity.agent_system_prompt("Be concise.", identity_id="friday")
    fallback_prompt = agent_identity.agent_system_prompt("Be concise.", identity_id="deleted-id")

    assert "identity is Jarvis" in jarvis_prompt
    assert "Protect the operator's context first." in jarvis_prompt
    assert "identity is Friday" in friday_prompt
    assert "Report every uncertainty as uncertainty." in friday_prompt
    assert jarvis_prompt != friday_prompt
    assert "identity is Assistant" in fallback_prompt


def test_installation_status_contract_stays_redacted_when_registry_exists(store, monkeypatch):
    _create("Jarvis", "Secret constitution body.")
    identities.set_active_identity("jarvis")
    monkeypatch.setattr(agent_identity, "load_settings", lambda: dict(DEFAULT_SETTINGS))

    status = agent_identity.agent_identity_status()

    assert status["agent_id"] == "jarvis"
    assert "constitution" not in status
    assert "Secret constitution body." not in repr(status)


def test_session_identity_validation_accepts_saved_ids_and_clears_empty(store):
    import routes.session_routes as session_routes

    _create("Friday", "Stay precise.")

    assert session_routes._validated_session_identity(None) == ""
    assert session_routes._validated_session_identity("") == ""
    assert session_routes._validated_session_identity("friday") == "friday"
    with pytest.raises(HTTPException) as unknown:
        session_routes._validated_session_identity("ghost")
    assert unknown.value.status_code == 400


def test_apply_identity_profile_chat_updates_model_and_persists(store, monkeypatch):
    import routes.session_routes as session_routes

    _create(
        "Friday",
        "Stay precise.",
        profile={"chat": {"endpoint_id": "", "model": "qwen3-14b", "reasoning_level": "low"}},
    )

    class _Row:
        model = ""
        endpoint_url = ""
        headers = {}
        updated_at = None

    class _DB:
        def __init__(self):
            self.row = _Row()

        def query(self, *args, **kwargs):
            return self

        def filter(self, *args, **kwargs):
            return self

        def first(self):
            return self.row

        def commit(self):
            pass

        def close(self):
            pass

    db = _DB()
    monkeypatch.setattr(session_routes, "SessionLocal", lambda: db)

    class _Session:
        model = ""
        endpoint_url = "http://existing/v1/chat/completions"
        headers = {}

    session = _Session()
    applied = session_routes._apply_identity_profile_chat(session, "s1", "friday", owner=None)

    assert applied == {"model": "qwen3-14b"}
    assert session.model == "qwen3-14b"
    assert session.endpoint_url == "http://existing/v1/chat/completions"
    assert db.row.model == "qwen3-14b"


def test_apply_identity_profile_chat_resolves_endpoint_when_configured(store, monkeypatch):
    import routes.session_routes as session_routes
    import src.endpoint_resolver as endpoint_resolver

    _create(
        "Friday",
        "Stay precise.",
        profile={"chat": {"endpoint_id": "ep-9", "model": "qwen3-32b", "reasoning_level": "high"}},
    )
    monkeypatch.setattr(
        endpoint_resolver,
        "resolve_endpoint_by_id",
        lambda ep_id, model=None, owner=None: (
            "http://local/v1/chat/completions",
            "qwen3-32b",
            {"Authorization": "Bearer x"},
        ),
    )

    class _DB:
        def query(self, *args, **kwargs):
            return self

        def filter(self, *args, **kwargs):
            return self

        def first(self):
            return None

        def commit(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(session_routes, "SessionLocal", lambda: _DB())

    class _Session:
        model = ""
        endpoint_url = ""
        headers = {}

    session = _Session()
    applied = session_routes._apply_identity_profile_chat(session, "s1", "friday", owner=None)

    assert applied["model"] == "qwen3-32b"
    assert applied["endpoint_url"] == "http://local/v1/chat/completions"
    assert session.headers == {"Authorization": "Bearer x"}


def test_installation_identity_still_resolves_without_registry(monkeypatch):
    monkeypatch.setattr(agent_identity, "load_settings", lambda: dict(DEFAULT_SETTINGS))

    prompt = agent_identity.agent_system_prompt()

    assert "identify yourself as Assistant" in prompt
    assert agent_identity.resolve_agent_identity(DEFAULT_SETTINGS)["status"] == "healthy"
