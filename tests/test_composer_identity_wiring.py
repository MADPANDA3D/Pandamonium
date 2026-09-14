"""MAD-930: composer identity/model/reasoning wiring source contract.

These checks pin the product wiring that the browser spec exercises: the new
identity-default model chip, the saved-identity selector backed by the MAD-929
endpoints, the per-session identity binding, and the reasoning chip reading the
session's real level instead of a bare "Default".
"""

from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "static"


def test_composer_has_identity_model_chip():
    index = (STATIC / "index.html").read_text(encoding="utf-8")
    assert 'id="identity-model-btn"' in index
    assert 'id="identity-model-label"' in index
    # The chip sits between the identity selector and the reasoning chip.
    assert index.index('id="model-picker-btn"') < index.index('id="identity-model-btn"') < index.index('id="composer-effort-btn"')


def test_model_picker_wires_saved_identities_and_session_binding():
    picker = (STATIC / "js" / "modelPicker.js").read_text(encoding="utf-8")
    assert "/api/auth/identities" in picker
    assert "identity_id" in picker
    assert "identity-model-btn" in picker
    assert "reasoning_level" in picker


def test_sessions_materialize_the_pending_identity():
    sessions = (STATIC / "js" / "sessions.js").read_text(encoding="utf-8")
    assert "identity_id" in sessions
    assert "identityId" in sessions


def test_reasoning_chip_reads_session_level_and_never_bare_default():
    context = (STATIC / "js" / "conversationContext.js").read_text(encoding="utf-8")
    assert "sessionReasoningLevel" in context
    assert "reasoning_level" in context
    # Reasoning mode's unset fallback is the model's own default, not "Default";
    # the work-budget branch intentionally keeps its legacy label.
    assert "'Model default'" in context
