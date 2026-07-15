import subprocess
import json
import hashlib
import hmac
import ipaddress
import httpx
import logging
import os
import re
import secrets
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Any, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_TAILNET_STATUS_TIMEOUT = 3
_TAILNET_SELECTION_LIMIT = 5
_TAILNET_ISSUE_TTL = 120
_TAILNET_PROBE_WORKERS = 4
_TAILNET_PROBE_TIMEOUT = 1.5
_TAILSCALE_CGNAT = ipaddress.ip_network("100.64.0.0/10")
_TAILNET_TARGETS = (
    (8000, "/v1/models", "openai-compatible"),
    (8080, "/v1/models", "llamacpp-compatible"),
    (1234, "/v1/models", "lmstudio-compatible"),
    (11434, "/api/tags", "ollama"),
)
_OPAQUE_PEER_ID = re.compile(r"^[0-9a-f]{32}$")
_SAFE_MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+:/-]{0,127}$")
_KNOWN_PEER_OS = {"android", "darwin", "freebsd", "ios", "linux", "windows"}


def _parse_tailscale_status(raw: str) -> Dict[str, Any]:
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _first_tailscale_ipv4(value: Any) -> Optional[str]:
    if not isinstance(value, list):
        return None
    for value_ip in value:
        if not isinstance(value_ip, str):
            continue
        try:
            address = ipaddress.ip_address(value_ip)
        except ValueError:
            continue
        if address.version == 4 and address in _TAILSCALE_CGNAT:
            return value_ip
    return None


def _tailscale_status() -> Dict[str, Any]:
    """Return one fresh, read-only Tailscale status snapshot."""
    try:
        result = subprocess.run(
            ["tailscale", "status", "--json"],
            capture_output=True,
            text=True,
            timeout=_TAILNET_STATUS_TIMEOUT,
            check=False,
        )
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return {}
    if result.returncode != 0 or len(result.stdout) > 2_000_000:
        return {}
    return _parse_tailscale_status(result.stdout)


def _tailnet_records(data: Dict[str, Any]) -> List[Dict[str, str]]:
    """Extract only online peers with an IPv4 address from a status snapshot."""
    peers = data.get("Peer") if isinstance(data.get("Peer"), dict) else {}
    records = []
    for stable_key, peer in peers.items():
        if not isinstance(peer, dict) or peer.get("Online") is not True:
            continue
        address = _first_tailscale_ipv4(peer.get("TailscaleIPs"))
        if not address:
            continue
        os_name = str(peer.get("OS") or "").strip().lower()
        records.append(
            {
                "key": str(peer.get("ID") or stable_key),
                "address": address,
                "os": os_name if os_name in _KNOWN_PEER_OS else "other",
            }
        )
    return records


def discover_tailscale_hosts() -> List[str]:
    """Compatibility helper for explicit callers; default discovery never calls it."""
    return [record["address"] for record in _tailnet_records(_tailscale_status())]


class ModelDiscovery:
    def __init__(self, default_host: str, openai_api_key: Optional[str] = None):
        self.default_host = default_host
        self.openai_api_key = openai_api_key
        self.openai_compat_path = "/v1/chat/completions"
        # Custom ports from env vars, merged into the scan list by discover_models.
        self._extra_ports: set = set()
        self._tailnet_secret = secrets.token_bytes(32)
        self._tailnet_issued: Dict[str, float] = {}

    def _get_hosts(self) -> List[str]:
        """Get configured/default hosts. Tailnet peers are never added here."""
        self._extra_ports = set()

        def _append_host(out: List[str], host: str) -> None:
            host = (host or "").strip()
            if not host or host in out:
                return
            out.append(host)

        def _append_env_hosts(out: List[str]) -> None:
            """Add hosts (and any custom ports) from provider-specific env vars."""
            for env_name in ("OLLAMA_BASE_URL", "OLLAMA_URL", "LM_STUDIO_URL"):
                raw = os.getenv(env_name, "").strip()
                if not raw:
                    continue
                try:
                    parsed = urlparse(raw if "://" in raw else "http://" + raw)
                    _append_host(out, parsed.hostname or "")
                    if parsed.port:
                        self._extra_ports.add(parsed.port)
                except Exception:
                    pass

        # Manual override takes priority
        extra = os.getenv("LLM_HOSTS", "").strip()
        if extra:
            hosts = [h.strip() for h in extra.split(",") if h.strip()]
            # Always include the default host too
            if self.default_host not in hosts:
                hosts.insert(0, self.default_host)
            _append_host(hosts, "host.docker.internal")
            _append_env_hosts(hosts)
            return hosts

        hosts = [self.default_host]
        # Docker desktop/Linux compose maps this to the host machine. That is
        # the common "I started Ollama normally on this computer" case.
        _append_host(hosts, "host.docker.internal")
        _append_env_hosts(hosts)
        return hosts

    def _tailnet_peer_id(self, record: Dict[str, str]) -> str:
        source = f"{record['key']}\0{record['address']}".encode("utf-8")
        return hmac.new(self._tailnet_secret, source, hashlib.sha256).hexdigest()[:32]

    def list_tailnet_peers(self) -> Dict[str, Any]:
        """List online Tailnet peers without probing or exposing network identity."""
        now = time.monotonic()
        self._tailnet_issued = {
            peer_id: expires
            for peer_id, expires in self._tailnet_issued.items()
            if expires > now
        }
        peers = []
        for record in _tailnet_records(_tailscale_status()):
            peer_id = self._tailnet_peer_id(record)
            self._tailnet_issued[peer_id] = now + _TAILNET_ISSUE_TTL
            peers.append({"id": peer_id, "os": record["os"], "status": "online"})
        peers.sort(key=lambda peer: peer["id"])
        return {"mode": "tailnet_peers", "peers": peers, "requires_selection": True}

    @staticmethod
    def _public_model_ids(payload: Any, *, ollama: bool = False) -> List[str]:
        if not isinstance(payload, dict):
            return []
        items = payload.get("models") if ollama else payload.get("data")
        if not isinstance(items, list):
            return []
        result = []
        for item in items[:100]:
            if not isinstance(item, dict):
                continue
            raw = item.get("model") or item.get("name") if ollama else item.get("id")
            raw_model_id = str(raw or "").strip()
            model_id = raw_model_id.lstrip("/")
            try:
                ipaddress.ip_address(model_id)
                is_address = True
            except ValueError:
                is_address = False
            if (
                model_id
                and not raw_model_id.startswith(("/", "\\"))
                and _SAFE_MODEL_ID.fullmatch(model_id)
                and "://" not in model_id
                and not re.match(r"^[A-Za-z]:/", model_id)
                and not is_address
                and model_id not in result
            ):
                result.append(model_id)
        return result

    def _probe_tailnet_target(
        self, record: Dict[str, str], peer_id: str, target: tuple
    ) -> Optional[Dict[str, Any]]:
        port, path, provider = target
        try:
            response = httpx.get(
                f"http://{record['address']}:{port}{path}",
                timeout=_TAILNET_PROBE_TIMEOUT,
            )
            if not response.is_success:
                return None
            models = self._public_model_ids(response.json(), ollama=provider == "ollama")
        except Exception:
            return None
        if not models:
            return None
        return {
            "peer_id": peer_id,
            "provider": provider,
            "models": models,
            "capabilities": ["model-list"],
        }

    def discover_tailnet_models(self, peer_ids: List[str]) -> Dict[str, Any]:
        """Probe only a bounded set of opaque IDs issued by ``list_tailnet_peers``."""
        selected = list(dict.fromkeys(str(peer_id) for peer_id in (peer_ids or [])))
        if not selected or len(selected) > _TAILNET_SELECTION_LIMIT:
            raise ValueError(f"select between 1 and {_TAILNET_SELECTION_LIMIT} peers")

        now = time.monotonic()
        if any(
            not _OPAQUE_PEER_ID.fullmatch(peer_id)
            or self._tailnet_issued.get(peer_id, 0) <= now
            for peer_id in selected
        ):
            raise ValueError("peer selection was not issued or has expired")

        current = {
            self._tailnet_peer_id(record): record
            for record in _tailnet_records(_tailscale_status())
        }
        if any(peer_id not in current for peer_id in selected):
            raise ValueError("peer selection is no longer available")

        targets = [
            (current[peer_id], peer_id, target)
            for peer_id in selected
            for target in _TAILNET_TARGETS
        ]
        candidates = []
        with ThreadPoolExecutor(max_workers=_TAILNET_PROBE_WORKERS) as pool:
            futures = [
                pool.submit(self._probe_tailnet_target, record, peer_id, target)
                for record, peer_id, target in targets
            ]
            for future in as_completed(futures):
                result = future.result()
                if result:
                    candidates.append(result)
        candidates.sort(key=lambda item: (item["peer_id"], item["provider"], item["models"]))
        return {
            "mode": "tailnet_probe",
            "selected_count": len(selected),
            "candidates": candidates,
        }

    def _fingerprint_provider(self, host: str, port: int) -> Optional[str]:
        """Identify the server software via its native API, independent of port."""
        try:
            r = httpx.get(f"http://{host}:{port}/api/v1/models", timeout=1.5)
            if r.is_success:
                models = (r.json() or {}).get("models")
                if (
                    isinstance(models, list)
                    and models
                    and isinstance(models[0], dict)
                    and "key" in models[0]
                    and "architecture" in models[0]
                ):
                    return "lmstudio"
        except Exception:
            pass
        # llama.cpp's llama-server exposes a native /props endpoint (no /v1 prefix)
        # describing the loaded model, slots, and chat template — distinct from
        # LM Studio (/api/v1/models) and vLLM (/version, /metrics).
        try:
            r = httpx.get(f"http://{host}:{port}/props", timeout=1.5)
            if r.is_success:
                props = r.json() or {}
                if isinstance(props, dict) and (
                    "default_generation_settings" in props
                    or "total_slots" in props
                    or "chat_template" in props
                ):
                    return "llamacpp"
        except Exception:
            pass
        return None

    def _check_port(self, host: str, port: int) -> Optional[Dict[str, Any]]:
        """Check a single host:port for models."""
        base = f"http://{host}:{port}/v1"
        try:
            r = httpx.get(f"{base}/models", timeout=3)
            if not r.is_success:
                return None
            data = r.json()
            # Some OpenAI-compatible servers return a bare list, not {"data": [...]}.
            items = data if isinstance(data, list) else ((data or {}).get("data") or [])
            ids = [m.get("id") for m in items if isinstance(m, dict) and m.get("id")]
            if ids:
                return {
                    "host": host,
                    "port": port,
                    "url": f"http://{host}:{port}{self.openai_compat_path}",
                    "models": ids,
                    "models_display": [i.lstrip("/") for i in ids],
                    "provider": self._fingerprint_provider(host, port),
                }
        except Exception:
            pass
        return None

    def discover_models(self) -> Dict[str, List[Dict[str, Any]]]:
        """Discover available models from all reachable hosts."""
        hosts = self._get_hosts()
        items = []

        logger.info(f"Scanning {len(hosts)} hosts for models: {hosts}")

        # Well-known ports: 8000-8020 (vLLM, SGLang, Cookbook), 8080 (llama.cpp /
        # llama-server default), 1234 (LM Studio), 11434 (Ollama), 11435 for APFEL
        # as its default port is occupied by Ollama. The env vars can add more
        # ports which will be merged in.
        ports = list(range(8000, 8021)) + [8080, 1234, 11434, 11435]
        ports += [p for p in sorted(self._extra_ports) if p not in ports]
        targets = [(h, p) for h in hosts for p in ports]

        seen_models = (
            set()
        )  # dedupe by (port, model_ids) to avoid same machine via different IPs

        with ThreadPoolExecutor(max_workers=50) as pool:
            futures = {pool.submit(self._check_port, h, p): (h, p) for h, p in targets}
            for future in as_completed(futures):
                result = future.result()
                if result:
                    key = (result["port"], tuple(sorted(result["models"])))
                    if key not in seen_models:
                        seen_models.add(key)
                        items.append(result)

        # Sort by host then port for consistent ordering
        items.sort(key=lambda x: (x["host"], x["port"]))

        logger.info(
            f"Discovered {len(items)} model endpoints across {len(hosts)} hosts"
        )
        return {"hosts": hosts, "items": items}

    def warmup_ping_urls(self, limit: int = 5) -> List[str]:
        """The ``/models`` URLs of up to ``limit`` discovered endpoints.

        Used by the startup warmup / keepalive loop to prime connections. Each
        discovered item already carries a ``/v1/chat/completions`` url; swap the
        suffix for the cheap ``/models`` probe. Failures degrade to an empty list
        so warmup never crashes the caller.
        """
        try:
            items = (self.discover_models() or {}).get("items", [])
        except Exception:
            return []
        urls: List[str] = []
        for ep in items[:limit]:
            url = (ep.get("url") or "").replace("/chat/completions", "/models")
            if url:
                urls.append(url)
        return urls

    def get_providers(self) -> Dict[str, Any]:
        """Get all available providers"""
        discovery = self.discover_models()
        items = discovery["items"]
        providers = [{"provider": "vllm", "hosts": discovery["hosts"], "items": items}]

        if self.openai_api_key:
            openai_models = [
                "gpt-5.2-codex",
                "gpt-4o-mini",
                "gpt-image-1.5",
                "gpt-4o",
                "gpt-5.2",
                "gpt-5.2-pro",
            ]
            providers.append(
                {
                    "provider": "openai",
                    "items": [
                        {
                            "url": "https://api.openai.com/v1/chat/completions",
                            "models": openai_models,
                        }
                    ],
                }
            )

        return {"providers": providers}
