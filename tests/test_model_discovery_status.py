from src import model_discovery


def test_parse_tailscale_status_rejects_wrong_shapes():
    assert model_discovery._parse_tailscale_status("{bad") == {}
    assert model_discovery._parse_tailscale_status("[]") == {}
    assert model_discovery._parse_tailscale_status('{"Self": {}}') == {"Self": {}}


def test_discovery_ignores_invalid_peer_rows(monkeypatch):
    class Result:
        returncode = 0
        stdout = '{"Self":{"TailscaleIPs":["100.1.1.1"]},"Peer":{"bad":"row","ok":{"Online":true,"HostName":"box","OS":"linux","TailscaleIPs":["100.1.1.2"]}}}'

    monkeypatch.setattr(model_discovery.subprocess, "run", lambda *a, **k: Result())
    model_discovery._hosts_cache = []
    model_discovery._hosts_cache_time = 0

    assert model_discovery.discover_tailscale_hosts() == ["100.1.1.1", "100.1.1.2"]


def test_discovery_ignores_invalid_tailscale_ip_shapes(monkeypatch):
    class Result:
        returncode = 0
        stdout = (
            '{"Self":{"TailscaleIPs":"100.1.1.1"},'
            '"Peer":{'
            '"string_ips":{"Online":true,"HostName":"bad","OS":"linux","TailscaleIPs":"100.1.1.2"},'
            '"mixed_ips":{"Online":true,"HostName":"ok","OS":"linux","TailscaleIPs":[null,123,"100.1.1.3"]}'
            '}}'
        )

    monkeypatch.setattr(model_discovery.subprocess, "run", lambda *a, **k: Result())
    model_discovery._hosts_cache = []
    model_discovery._hosts_cache_time = 0

    assert model_discovery.discover_tailscale_hosts() == ["100.1.1.3"]


def test_installation_capabilities_accept_only_supported_health_claims():
    assert model_discovery.installation_capabilities({
        "installation_capabilities": ["claude", "model", "shell", "claude"],
    }) == ["claude", "model"]
    assert model_discovery.installation_capabilities({
        "connection": {"protocol": "codex-bridge"},
    }) == ["codex"]
    assert model_discovery.installation_capabilities({
        "connection": {"protocol": "hermes-runs"},
    }) == ["hermes"]
