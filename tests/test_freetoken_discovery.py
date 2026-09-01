"""FreeToken model discovery regressions."""

from src.model_discovery import ModelDiscovery


def test_model_discovery_scans_freetoken_port(monkeypatch):
    discovery = ModelDiscovery(default_host="localhost")
    scanned_ports = []

    monkeypatch.setattr(
        "src.model_discovery.discover_tailscale_hosts", lambda: []
    )
    monkeypatch.setattr(
        discovery,
        "_check_port",
        lambda _host, port: scanned_ports.append(port),
    )

    discovery.discover_models()

    assert 1919 in scanned_ports
