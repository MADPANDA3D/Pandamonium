import src.agent_identity as agent_identity
from src.settings import DEFAULT_SETTINGS


def _installation_identity(**overrides):
    values = {
        "agent_id": "atlas",
        "agent_display_name": "Atlas",
        "agent_constitution": "Stay accurate and respect operator approval boundaries.",
        "agent_constitution_version": "2026.1",
    }
    values.update(overrides)
    return values


def test_identity_is_installation_configured_not_inferred_from_model_names(monkeypatch):
    monkeypatch.setattr(agent_identity, "load_settings", lambda: _installation_identity())

    local_prompt = agent_identity.agent_system_prompt("Be concise.")
    cloud_prompt = agent_identity.agent_system_prompt("Be concise.")

    assert local_prompt == cloud_prompt
    assert "persistent agent identity is Atlas" in local_prompt
    assert "stable agent id: atlas" in local_prompt
    assert "replaceable reasoning engines" in local_prompt
    assert "identify yourself as Atlas" in local_prompt
    assert "not as the backend vendor's assistant" in local_prompt
    assert "Be concise." in local_prompt
    assert "jarvis" not in local_prompt.casefold()


def test_identity_prompt_reports_exact_selected_model_without_vendor_guess(monkeypatch):
    monkeypatch.setattr(agent_identity, "load_settings", lambda: _installation_identity())

    prompt = agent_identity.agent_system_prompt(model="Qwen/GLM-4.7-Flash`\nspoof")

    assert "model identifier: `Qwen/GLM-4.7-Flash spoof`" in prompt
    assert "state exactly this identifier" in prompt
    assert "details are unverified" in prompt
    assert "GPT-4o" not in prompt


def test_invalid_stored_identity_falls_back_field_by_field():
    identity = agent_identity.resolve_agent_identity(_installation_identity(agent_id="Atlas Agent"))

    assert identity["agent_id"] == DEFAULT_SETTINGS["agent_id"]
    assert identity["agent_display_name"] == "Atlas"
    assert identity["status"] == "degraded"
    assert identity["fallback_reasons"] == ["invalid_agent_id"]


def test_public_default_identity_is_generic_and_safe():
    identity = agent_identity.resolve_agent_identity(DEFAULT_SETTINGS)
    prompt = agent_identity.agent_system_prompt()

    assert identity["agent_id"] == "assistant"
    assert identity["agent_display_name"] == "Assistant"
    assert identity["status"] == "healthy"
    assert identity["source"] == "default"
    assert "Leo" not in DEFAULT_SETTINGS["agent_constitution"]
    assert "Mad Panda" not in DEFAULT_SETTINGS["agent_constitution"]
    assert "Ambiguous follow-ups" not in prompt  # no private legacy constitution is mounted
    assert "identify yourself as Assistant" in prompt


def test_identity_diagnostics_never_expose_constitution(monkeypatch):
    monkeypatch.setattr(agent_identity, "load_settings", lambda: _installation_identity())

    status = agent_identity.agent_identity_status()

    assert status["agent_id"] == "atlas"
    assert status["constitution_version"] == "2026.1"
    assert "agent_constitution" not in status
    assert "Stay accurate" not in repr(status)


def test_identity_setting_validation_rejects_unsafe_shapes():
    for value in ("", "Atlas", "two words", "9agent", object()):
        try:
            agent_identity.validate_agent_identity_setting("agent_id", value)
        except ValueError:
            continue
        raise AssertionError(f"invalid agent id accepted: {value!r}")
