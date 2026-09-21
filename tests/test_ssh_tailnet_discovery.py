"""MAD-976: SSH tailnet discovery — bounded peer listing, self-exclusion,
CGNAT filtering, opaque peer ids, and server-side address resolution.

The Tailscale CLI is replaced with a fixture snapshot, so nothing here dials a
real tailnet or a real node.
"""

from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from tests.helpers.sqlite_db import make_temp_sqlite
from tests.helpers.import_state import preserve_import_state

with preserve_import_state(
    "src.ssh_connections",
    "routes.ssh_routes",
    "core.database",
):
    import core.database as database
    import routes.ssh_routes as ssh_routes
    import src.ssh_connections as ssh_connections


NODE_ID = "conn01a2b3"
PRIVATE_KEY = "-----BEGIN OPENSSH PRIVATE KEY-----\nfake\n-----END OPENSSH PRIVATE KEY-----\n"
PUBLIC_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakePublicKeyForTests pandamonium-ssh:conn01a2b3"
HOST_KEY_LINE = "100.106.175.62 ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIHostKeyMaterialForTests"


def _peer(peer_id: str, name: str, ips, os_name: str = "linux", online: bool = True):
    return {
        peer_id: {
            "ID": peer_id,
            "HostName": name,
            "DNSName": f"{name}.tailf5266c.ts.net.",
            "TailscaleIPs": ips,
            "OS": os_name,
            "Online": online,
        }
    }


def _status(peers, self_ips=("100.91.21.24",)):
    return {
        "Self": {"HostName": "odysseus", "TailscaleIPs": list(self_ips), "OS": "linux"},
        "Peer": peers,
    }


@pytest.fixture
def fake_tailscale(monkeypatch):
    snapshot = _status(
        {
            **_peer("n-workstation", "madpanda-workstation", ["100.64.242.88", "fd7a::1"]),
            **_peer("n-charter", "srv779520-charter-vps", ["100.106.175.62"]),
            **_peer("n-oracle", "oracle", ["100.117.131.123"]),
            **_peer("n-offline", "hermes", ["100.119.195.80"], online=False),
            **_peer("n-public", "weird-public", ["203.0.113.9"]),
            **_peer("n-ios", "iphone172", ["100.119.79.92"], os_name="iOS"),
        }
    )
    monkeypatch.setattr(ssh_connections, "_tailscale_status", lambda: snapshot)
    monkeypatch.setattr(ssh_connections, "resolve_tailscale_binary", lambda: "/usr/bin/tailscale")
    monkeypatch.setattr(ssh_connections, "_tailnet_issued", {})
    return snapshot


@pytest.fixture
def ssh_env(tmp_path, monkeypatch):
    SessionLocal, engine, tmpfile = make_temp_sqlite(database.Base.metadata)
    monkeypatch.setattr(database, "SessionLocal", SessionLocal)
    monkeypatch.setattr(ssh_routes, "SessionLocal", SessionLocal)

    import src.secret_storage as secret_storage

    monkeypatch.setattr(secret_storage, "_KEY_PATH", tmp_path / ".app_key")
    monkeypatch.setattr(secret_storage, "_fernet", None)
    monkeypatch.setattr(ssh_connections, "SSH_CONNECTIONS_DIR", str(tmp_path / "ssh_connections"))
    monkeypatch.setattr(ssh_connections, "SSH_AUDIT_FILE", str(tmp_path / "ssh_audit.jsonl"))

    yield SimpleNamespace(SessionLocal=SessionLocal, engine=engine, tmpfile=tmpfile, tmp_path=tmp_path)
    engine.dispose()
    tmpfile.close()


@pytest.fixture
def router(monkeypatch):
    monkeypatch.setattr(ssh_routes, "require_admin", lambda request: None)
    return ssh_routes.setup_ssh_routes()


def _route(router, path: str, method: str):
    for route in router.routes:
        if getattr(route, "path", "") == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError(f"{method} {path} route not found")


def _admin_request(user: str = "admin"):
    return SimpleNamespace(
        state=SimpleNamespace(current_user=user, api_token=False),
        app=SimpleNamespace(state=SimpleNamespace(auth_manager=None)),
        client=SimpleNamespace(host="127.0.0.1"),
    )


# ── discovery parsing ────────────────────────────────────────────────────


def test_discovery_lists_only_online_cgnat_peers(fake_tailscale):
    result = ssh_connections.discover_tailnet_peers()

    assert result["available"] is True
    names = {peer["name"] for peer in result["peers"]}
    assert names == {"madpanda-workstation", "srv779520-charter-vps", "oracle", "iphone172"}
    assert "odysseus" not in names  # self is never a target
    assert "hermes" not in names  # offline excluded
    assert "weird-public" not in names  # non-CGNAT excluded


def test_discovery_never_leaks_addresses_or_peer_keys(fake_tailscale):
    result = ssh_connections.discover_tailnet_peers()

    encoded = json.dumps(result)
    assert "100.64.242.88" not in encoded
    assert "100.106.175.62" not in encoded
    assert "n-workstation" not in encoded
    for peer in result["peers"]:
        assert len(peer["id"]) == 32
        assert peer["id"] not in {"n-workstation", "n-charter", "n-oracle", "n-ios"}
        assert peer["os"] in {"android", "darwin", "freebsd", "ios", "linux", "windows", "other"}


def test_discovery_marks_ios_peer_os(fake_tailscale):
    result = ssh_connections.discover_tailnet_peers()
    ios = [peer for peer in result["peers"] if peer["name"] == "iphone172"]
    assert ios and ios[0]["os"] == "ios"


def test_resolve_returns_address_for_scanned_peer(fake_tailscale):
    result = ssh_connections.discover_tailnet_peers()
    resolved = {ssh_connections.resolve_tailnet_peer(peer["id"]) for peer in result["peers"]}
    assert resolved == {"100.64.242.88", "100.106.175.62", "100.117.131.123", "100.119.79.92"}


def test_resolve_rejects_unknown_peer(fake_tailscale):
    with pytest.raises(HTTPException) as excinfo:
        ssh_connections.resolve_tailnet_peer("0" * 32)
    assert excinfo.value.status_code == 400


def test_resolve_rejects_expired_peer(fake_tailscale, monkeypatch):
    peer = ssh_connections.discover_tailnet_peers()["peers"][0]
    monkeypatch.setattr(ssh_connections, "_tailnet_issued", {peer["id"]: 0.0})
    with pytest.raises(HTTPException) as excinfo:
        ssh_connections.resolve_tailnet_peer(peer["id"])
    assert excinfo.value.status_code == 400


def test_discovery_reports_unavailable_without_tailscale(monkeypatch):
    monkeypatch.setattr(ssh_connections, "resolve_tailscale_binary", lambda: None)
    monkeypatch.setattr(ssh_connections, "_tailnet_issued", {})

    result = ssh_connections.discover_tailnet_peers()

    assert result["available"] is False
    assert result["peers"] == []
    assert result["message"]


# ── routes ───────────────────────────────────────────────────────────────


def test_discover_route_returns_peers(fake_tailscale, router):
    endpoint = _route(router, "/api/ssh/discover", "GET")
    data = endpoint(_admin_request())
    assert data["available"] is True
    assert len(data["peers"]) == 4


def test_discover_route_requires_admin(monkeypatch, router):
    def _deny(request):
        raise HTTPException(403, "admin required")

    monkeypatch.setattr(ssh_routes, "require_admin", _deny)
    endpoint = _route(router, "/api/ssh/discover", "GET")
    with pytest.raises(HTTPException) as excinfo:
        endpoint(_admin_request())
    assert excinfo.value.status_code == 403


def test_add_connection_from_tailnet_peer_resolves_host(ssh_env, fake_tailscale, router, monkeypatch):
    peer = next(p for p in ssh_connections.discover_tailnet_peers()["peers"] if p["name"] == "oracle")
    monkeypatch.setattr(ssh_connections, "generate_keypair", lambda cid: {"private_key": PRIVATE_KEY, "public_key": PUBLIC_KEY})
    monkeypatch.setattr(ssh_connections, "scan_host_key", lambda host, port: [
        {"key_type": "ssh-ed25519", "fingerprint": "SHA256:TEST", "known_hosts_line": HOST_KEY_LINE}
    ])

    endpoint = _route(router, "/api/ssh/connections", "POST")
    payload = endpoint(
        _admin_request(),
        label="Oracle",
        host="",
        user="root",
        port="22",
        keyless="true",
        tailnet_peer_id=peer["id"],
    )

    assert payload["host"] == "100.117.131.123"
    assert payload["host_key_pinned"] is True
    encoded = json.dumps(payload)
    assert '"private_key"' not in encoded
    assert "BEGIN OPENSSH PRIVATE KEY" not in encoded


def test_add_connection_still_requires_host_without_tailnet_peer(ssh_env, router, monkeypatch):
    endpoint = _route(router, "/api/ssh/connections", "POST")
    with pytest.raises(HTTPException) as excinfo:
        endpoint(
            _admin_request(),
            label="Manual",
            host="",
            user="root",
            port="22",
            keyless="false",
            tailnet_peer_id="",
        )
    assert excinfo.value.status_code == 400
