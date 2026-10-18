"""Runtime diagnostics preserve safe codes without recording provider secrets."""

import asyncio
import logging

import pytest
from fastapi import HTTPException
from jsonschema import ValidationError

import routes.entertainment_routes as routes
import src.extension_cli_adapter as cli
from src.extension_installer import ExtensionLifecycleError


@pytest.mark.parametrize(
    "failure,code",
    [
        (
            ExtensionLifecycleError("extension_cli_failed:https://example.com/private"),
            "extension_cli_failed",
        ),
        (ValidationError("private input failed integer validation"), "extension_cli_schema_invalid"),
        (RuntimeError("private upstream response"), "extension_cli_unavailable"),
    ],
)
def test_cli_boundary_adds_a_local_failure_code(monkeypatch, failure, code):
    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(cli.GeneratedCliAdapter, "execute", fail)
    result = asyncio.run(cli.execute_cli_tool({}, "tool", {}, "operator"))
    assert result["exit_code"] == 1
    assert result["error_code"] == code
    assert result["error"]  # Retain the existing internal caller contract.


@pytest.mark.parametrize(
    "returned_code,logged_code",
    [
        ("extension_cli_schema_invalid", "extension_cli_schema_invalid"),
        ("private\nhttps://example.com/credential", "extension_cli_unavailable"),
    ],
)
def test_entertainment_logs_only_the_failure_code(monkeypatch, caplog, returned_code, logged_code):
    monkeypatch.setattr(routes, "_installed", lambda owner: (owner, {"pandaflix": {"enabled": True}}))

    async def failed(*args, **kwargs):
        return {"exit_code": 1, "error_code": returned_code, "error": "private-provider-secret"}

    monkeypatch.setattr(routes, "execute_cli_tool", failed)
    endpoint = next(
        route.endpoint
        for route in routes.setup_entertainment_routes().routes
        if route.path == "/api/entertainment/{provider_id}/{operation}"
    )
    with caplog.at_level(logging.WARNING), pytest.raises(HTTPException) as error:
        asyncio.run(endpoint("pandaflix", "resolve", {}, "operator"))
    assert error.value.status_code == 503
    assert f"code={logged_code}" in caplog.text
    assert "private" not in caplog.text
    assert "https://" not in caplog.text
