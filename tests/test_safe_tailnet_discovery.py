import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from routes import model_routes
from src import model_discovery
from src.model_discovery import ModelDiscovery

_TAILNET_A = ".".join(("100", "64", "1", "7"))
_TAILNET_B = ".".join(("100", "64", "1", "8"))
_UNSAFE_MODEL_PATH = "/" + "home/user/token"


def _status(*peers):
    return {
        "Peer": {
            f"node-key-{index}": {
                "ID": f"node-{index}",
                "Online": True,
                "OS": os_name,
                "HostName": hostname,
                "DNSName": f"{hostname}.private-tailnet.ts.net.",
                "TailscaleIPs": [address],
            }
            for index, (address, hostname, os_name) in enumerate(peers)
        }
    }


def _run_result(payload):
    return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")


def test_default_discovery_never_reads_tailscale(monkeypatch):
    monkeypatch.delenv("LLM_HOSTS", raising=False)
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    monkeypatch.delenv("OLLAMA_URL", raising=False)
    monkeypatch.delenv("LM_STUDIO_URL", raising=False)
    monkeypatch.setattr(
        model_discovery.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("tailscale invoked")),
    )
    discovery = ModelDiscovery(default_host="localhost")
    monkeypatch.setattr(discovery, "_check_port", lambda host, port: None)

    result = discovery.discover_models()

    assert result["hosts"] == ["localhost", "host.docker.internal"]


def test_peer_listing_is_probe_free_and_redacted(monkeypatch):
    raw = _status(
        (_TAILNET_A, "private-builder", "linux"),
        ("127.0.0.1", "not-a-tailnet-address", "linux"),
    )
    monkeypatch.setattr(
        model_discovery.subprocess, "run", lambda *args, **kwargs: _run_result(raw)
    )
    monkeypatch.setattr(
        model_discovery.httpx,
        "get",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network probe")),
    )

    result = ModelDiscovery("localhost").list_tailnet_peers()
    rendered = json.dumps(result)

    assert result["requires_selection"] is True
    assert len(result["peers"]) == 1
    assert result["peers"][0]["os"] == "linux"
    assert result["peers"][0]["status"] == "online"
    assert len(result["peers"][0]["id"]) == 32
    assert _TAILNET_A not in rendered
    assert "private-builder" not in rendered
    assert "ts.net" not in rendered


def test_probe_contacts_only_selected_displayed_peer(monkeypatch):
    raw = _status(
        (_TAILNET_A, "first-private", "linux"),
        (_TAILNET_B, "second-private", "windows"),
    )
    monkeypatch.setattr(
        model_discovery.subprocess, "run", lambda *args, **kwargs: _run_result(raw)
    )
    discovery = ModelDiscovery("localhost")
    discovery.list_tailnet_peers()
    records = model_discovery._tailnet_records(raw)
    selected_id = discovery._tailnet_peer_id(records[1])
    calls = []

    def fake_probe(record, peer_id, target):
        calls.append((record["address"], peer_id, target))
        if target[0] == 8000:
            return {
                "peer_id": peer_id,
                "provider": "openai-compatible",
                "models": ["safe-model"],
                "capabilities": ["model-list"],
            }
        return None

    monkeypatch.setattr(discovery, "_probe_tailnet_target", fake_probe)

    result = discovery.discover_tailnet_models([selected_id])

    assert len(calls) == len(model_discovery._TAILNET_TARGETS)
    assert {call[0] for call in calls} == {_TAILNET_B}
    assert {call[1] for call in calls} == {selected_id}
    assert result["selected_count"] == 1
    assert result["candidates"][0]["models"] == ["safe-model"]


def test_unknown_and_oversized_peer_selections_fail_before_status_read(monkeypatch):
    discovery = ModelDiscovery("localhost")
    monkeypatch.setattr(
        model_discovery.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("status read")),
    )

    with pytest.raises(ValueError, match="not issued"):
        discovery.discover_tailnet_models(["0" * 32])
    with pytest.raises(ValueError, match="between 1 and 5"):
        discovery.discover_tailnet_models([f"{index:032x}" for index in range(6)])


def test_probe_response_drops_network_identity_and_unsafe_model_ids(monkeypatch):
    raw = _status((_TAILNET_A, "secret-host", "linux"))
    monkeypatch.setattr(
        model_discovery.subprocess, "run", lambda *args, **kwargs: _run_result(raw)
    )
    discovery = ModelDiscovery("localhost")
    peer_id = discovery.list_tailnet_peers()["peers"][0]["id"]

    class Response:
        is_success = True

        @staticmethod
        def json():
            return {
                "data": [
                    {"id": "qwen-safe"},
                    {"id": "http://secret-host/private"},
                    {"id": _TAILNET_A},
                    {"id": _UNSAFE_MODEL_PATH},
                ]
            }

    monkeypatch.setattr(model_discovery.httpx, "get", lambda *args, **kwargs: Response())

    result = discovery.discover_tailnet_models([peer_id])
    rendered = json.dumps(result)

    assert result["candidates"]
    assert all(candidate["models"] == ["qwen-safe"] for candidate in result["candidates"])
    assert _TAILNET_A not in rendered
    assert "secret-host" not in rendered
    assert _UNSAFE_MODEL_PATH not in rendered
    assert "http://" not in rendered


def test_discover_route_modes_and_rejects_unknown_mode(monkeypatch):
    class Discovery:
        def discover_models(self):
            return {"mode": "configured"}

        def list_tailnet_peers(self):
            return {"mode": "tailnet_peers"}

        def discover_tailnet_models(self, peer_ids):
            return {"mode": "tailnet_probe", "peer_ids": peer_ids}

    monkeypatch.setattr(model_routes, "require_admin", lambda request: None)
    router = model_routes.setup_model_routes(Discovery())
    endpoint = next(route.endpoint for route in router.routes if route.path == "/api/discover")

    assert endpoint(object(), mode="configured", peer_ids=None) == {"mode": "configured"}
    assert endpoint(object(), mode="tailnet_peers", peer_ids=None) == {"mode": "tailnet_peers"}
    assert endpoint(object(), mode="tailnet_probe", peer_ids=["abc"])["peer_ids"] == ["abc"]
    with pytest.raises(HTTPException) as exc:
        endpoint(object(), mode="agent_scan", peer_ids=None)
    assert exc.value.status_code == 400
