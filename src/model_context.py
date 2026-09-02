"""
model_context.py

Query and cache model context window sizes from OpenAI-compatible APIs.
Provides token estimation for context usage tracking.
"""

import ipaddress
import json
import logging
import sys
from typing import Any, Dict, List, Optional, Tuple

from urllib.parse import urlparse

import httpx

from src.context_budget import context_class_budget_percent

logger = logging.getLogger(__name__)

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1", "host.docker.internal"}
_PRIVATE_NETWORKS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
)

# Tailscale uses the CGNAT range 100.64.0.0/10, NOT all of 100.0.0.0/8.
# A bare "100." prefix would classify public addresses (e.g. AWS ranges
# under 100.x outside the CGNAT block) as local; routes/model_routes.py
# already narrows this the same way for endpoint classification.
_TAILSCALE_CGNAT = ipaddress.ip_network("100.64.0.0/10")


def _in_tailscale_range(host: str) -> bool:
    try:
        return ipaddress.ip_address(host) in _TAILSCALE_CGNAT
    except ValueError:
        return False


def _is_private_ip_literal(host: str) -> bool:
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(ip in network for network in _PRIVATE_NETWORKS)


def _normalize_base_for_compare(url: str) -> str:
    url = (url or "").strip().rstrip("/")
    for suffix in ("/chat/completions", "/models", "/completions", "/v1/messages"):
        if url.endswith(suffix):
            url = url[: -len(suffix)].rstrip("/")
    return url


def _configured_endpoint_kind(url: str) -> Optional[str]:
    """Return configured endpoint kind for a chat/base URL when available."""
    target = _normalize_base_for_compare(url)
    if not target:
        return None
    if "core.database" not in sys.modules:
        return None
    try:
        from core.database import SessionLocal, ModelEndpoint
        db = SessionLocal()
        try:
            rows = db.query(ModelEndpoint).filter(ModelEndpoint.is_enabled == True).all()
            for ep in rows:
                base = _normalize_base_for_compare(getattr(ep, "base_url", "") or "")
                if not base:
                    continue
                if target != base and not target.startswith(base + "/"):
                    continue
                kind = (getattr(ep, "endpoint_kind", None) or "auto").strip().lower()
                if kind in ("local", "api", "proxy"):
                    return kind
                if getattr(ep, "api_key", None):
                    parsed = urlparse(base)
                    host = (parsed.hostname or "").lower()
                    path = (parsed.path or "").rstrip("/")
                    if parsed.port != 11434 and "ollama" not in host and (path.endswith("/v1") or "/openai" in path):
                        return "proxy"
                return "auto"
        finally:
            db.close()
    except Exception:
        return None


def is_local_endpoint(url: str) -> bool:
    """Check if URL points to a local/private/tailscale address."""
    kind = _configured_endpoint_kind(url)
    if kind in ("api", "proxy"):
        return False
    if kind == "local":
        return True
    try:
        host = urlparse(url).hostname or ""
        return host in _LOCAL_HOSTS or _is_private_ip_literal(host) or _in_tailscale_range(host)
    except Exception:
        return False

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DEFAULT_CONTEXT = 128000
REQUEST_TIMEOUT = 5

# Known context windows for major API models (used as fallback when /models
# endpoint doesn't report context_length).
# Substring matching — use the shortest unique prefix so variants get caught.
KNOWN_CONTEXT_WINDOWS = {
    # --- Anthropic ---
    'claude-sonnet-4-5': 200000,
    'claude-sonnet-4-6': 200000,
    'claude-sonnet-4': 200000,
    'claude-opus-4': 200000,
    'claude-haiku-4': 200000,
    'claude-haiku-3-5': 200000,
    'claude-3-5-sonnet': 200000,
    'claude-3-5-haiku': 200000,
    'claude-3-opus': 200000,
    'claude-3-sonnet': 200000,
    'claude-3-haiku': 200000,

    # --- OpenAI ---
    'gpt-5': 400000,
    'gpt-4.1': 1047576,
    'gpt-4.1-mini': 1047576,
    'gpt-4.1-nano': 1047576,
    'gpt-4o': 128000,
    'gpt-4o-mini': 128000,
    'gpt-4-turbo': 128000,
    'gpt-4': 8192,
    'gpt-3.5-turbo': 16385,
    'o1': 200000,
    'o1-mini': 128000,
    'o1-pro': 200000,
    'o3': 200000,
    'o3-mini': 200000,
    'o4-mini': 200000,

    # --- DeepSeek ---
    'deepseek-chat': 64000,
    'deepseek-coder': 64000,
    'deepseek-reasoner': 64000,
    'deepseek-r1': 64000,
    'deepseek-v3': 64000,
    'deepseek-v2': 64000,

    # --- Google ---
    'gemini-2.5-pro': 1048576,
    'gemini-2.5-flash': 1048576,
    'gemini-2.0-flash': 1048576,
    'gemini-1.5-pro': 1048576,
    'gemini-1.5-flash': 1048576,
    'gemma-4': 262144,
    'gemma-3': 128000,
    'gemma-2': 8192,

    # --- Mistral ---
    'mistral-large': 128000,
    'mistral-medium': 32000,
    'mistral-small': 32000,
    'mistral-nemo': 128000,
    'mistral-7b': 32000,
    'mixtral': 32000,
    'codestral': 32000,
    'pixtral': 128000,

    # --- xAI ---
    'grok-4': 131072,
    'grok-3': 131072,
    'grok-2': 131072,

    # --- Meta / Llama ---
    'llama-4': 1048576,
    'llama-3.3': 131072,
    'llama-3.2': 131072,
    'llama-3.1': 131072,
    'llama-3': 131072,

    # --- Qwen ---
    'qwen3': 131072,
    'qwen2.5': 131072,
    'qwen2': 32768,
    'qwq': 32768,

    # --- Cohere ---
    'command-r-plus': 128000,
    'command-r': 128000,
    'command-a': 256000,

    # --- Perplexity ---
    'sonar-pro': 200000,
    'sonar': 128000,

    # --- MiniMax ---
    'minimax': 1000000,

    # --- Moonshot / Kimi ---
    'moonshot': 128000,
    'kimi': 128000,

    # --- Microsoft ---
    'phi-4': 16000,
    'phi-3': 128000,

    # --- Nvidia ---
    'nemotron': 131072,

    # --- Yi ---
    'yi-large': 32768,
    'yi-1.5': 16384,

    # --- 01.ai ---
    'yi-lightning': 16384,

    # --- Nous ---
    'hermes': 131072,
    'nous-hermes': 131072,

    # --- Xiaomi ---
    'mimo-v2.5-pro': 1048576,
    'mimo-v2.5': 1048576,

    # --- Open community ---
    'dolphin': 32768,
    'mythomax': 4096,
    'wizard': 32768,
    'openchat': 8192,
    'solar': 32768,
}

# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------
_context_cache: Dict[Tuple[str, str], Tuple[int, bool]] = {}


def _get_context_length_cached(endpoint_url: str, model: str) -> Tuple[int, bool]:
    """Return (context_length, known). ``known`` is False only when the value is a
    bare DEFAULT_CONTEXT fallback (no endpoint report and not in the known table)."""
    configured_kind = _configured_endpoint_kind(endpoint_url)
    is_local = is_local_endpoint(endpoint_url)
    # Key on (endpoint_url, model): the same model id can be served by two
    # different remote endpoints with different real context windows (e.g. a
    # capped proxy vs. the full provider), so caching by model id alone would
    # serve one endpoint's window for the other (issue #2603).
    cache_key = (endpoint_url, model)
    if not is_local and cache_key in _context_cache:
        return _context_cache[cache_key]

    ctx, known = _query_context_length(endpoint_url, model)
    # Only cache non-default values to allow retry on next request.
    # Local endpoints can restart with a different --max-model-len while keeping
    # the same model id, so always re-query them instead of serving stale cache.
    if not is_local and (ctx != DEFAULT_CONTEXT or configured_kind in ("api", "proxy")):
        _context_cache[cache_key] = (ctx, known)
    logger.info(f"Context length for {model}: {ctx}")
    return ctx, known


def get_context_length(endpoint_url: str, model: str) -> int:
    """Get the context window size for a model.

    Queries /v1/models on the endpoint and looks for context_length
    or context_window fields. Caches result per (endpoint, model).
    Falls back to DEFAULT_CONTEXT if unavailable.
    """
    return _get_context_length_cached(endpoint_url, model)[0]


def get_context_length_known(endpoint_url: str, model: str) -> Tuple[int, bool]:
    """Like ``get_context_length`` but also returns whether the window was actually
    discovered (endpoint-reported or in the known-models table) rather than the bare
    DEFAULT_CONTEXT fallback. Callers that *scale* a budget off the window must not
    trust an unknown value — a fallback 128K isn't proof the model holds 128K
    (review on #4122)."""
    return _get_context_length_cached(endpoint_url, model)


def budget_context_for_model(endpoint_url: str, model: str, *, fallback: int = 0) -> int:
    """Context window to scale the agent input budget against.

    Returns the *freshly discovered* window when it was actually proven
    (endpoint-reported / known table), else 0 so auto-scaling stays conservative.
    Crucially this binds the ``known`` flag to the value it proves — callers must
    not pair this flag with a context length from a *different* lookup (a stale
    local re-query, or a caller that didn't pass one), which would budget off an
    unproven number (review on #4122). On probe error, returns ``fallback`` (the
    caller's best-known value) to preserve prior behaviour."""
    try:
        ctx, known = get_context_length_known(endpoint_url, model)
        return ctx if known else 0
    except Exception:
        return fallback


def _lookup_known(model: str) -> Optional[int]:
    """Check known context windows by substring match.

    Picks the LONGEST matching key so a short key never shadows a more specific
    one. Without this, 'o1' (200k) precedes 'o1-mini' (128k) in the table and a
    first-match return would report o1-mini's window as 200k.
    """
    name = model.lower()
    basename = name.split("/")[-1] if "/" in name else name
    basename = basename.split(":")[0]  # strip :free, :extended etc.
    best_key: Optional[str] = None
    best_ctx: Optional[int] = None
    for key, ctx in KNOWN_CONTEXT_WINDOWS.items():
        if key in basename or key in name:
            if best_key is None or len(key) > len(best_key):
                best_key, best_ctx = key, ctx
    return best_ctx


def _model_ctx_from_entry(m: dict) -> Optional[int]:
    """Extract a positive context window from one /models catalog entry.

    Checks the common top-level fields first, then a nested meta/model_extra
    object. Returns None when no positive window is reported.
    """
    if not isinstance(m, dict):
        return None
    for field in (
        "context_length",
        "context_window",
        "max_model_len",
        "max_context_length",
        "max_seq_len",
    ):
        val = m.get(field)
        if val and isinstance(val, (int, float)) and val > 0:
            return int(val)
    meta = m.get("meta") or m.get("model_extra") or {}
    if isinstance(meta, dict):
        # n_ctx is the actual serving context (set via -c flag in llama.cpp)
        for field in ("n_ctx", "context_length", "context_window", "max_model_len"):
            val = meta.get(field)
            if val and isinstance(val, (int, float)) and val > 0:
                return int(val)
    return None


def _freetoken_runtime_context(base_url: str, model: str) -> Optional[int]:
    """Return FreeToken's currently allocated per-request context capacity.

    FreeToken can advertise the configured model maximum through ``/v1/models``
    while allocating a smaller KV cache after GPU/MoE placement. Its runtime
    stats expose both values, so use the smaller one when the stats belong to
    the requested model. Other local OpenAI-compatible servers simply 404 and
    continue through the existing discovery path.
    """
    try:
        response = httpx.get(f"{base_url}/v1/stats", timeout=REQUEST_TIMEOUT)
        if not response.is_success:
            return None
        data = response.json()
        runtime_model = data.get("model") if isinstance(data, dict) else None
        kv = data.get("kv") if isinstance(data, dict) else None
        if not isinstance(runtime_model, dict) or not isinstance(kv, dict):
            return None

        runtime_id = str(runtime_model.get("id") or "")
        if runtime_id != model and runtime_id.split("/")[-1] != model.split("/")[-1]:
            return None

        declared = runtime_model.get("ctx")
        total_pages = kv.get("total_pages")
        page_size = kv.get("page_size", 1)
        if not all(isinstance(value, (int, float)) and value > 0 for value in (declared, total_pages, page_size)):
            return None

        allocated = int(total_pages * page_size)
        return min(int(declared), allocated)
    except Exception:
        return None


# Per-endpoint cache of the {model_id: context_length} map parsed from a
# proxy/api catalog. api/proxy endpoints skip the /models download on every
# lookup because a large catalog is expensive; caching the whole map lets us
# pay that download at most once per endpoint instead of once per model.
_catalog_ctx_cache: Dict[str, Dict[str, int]] = {}


def _proxy_catalog_context(endpoint_url: str, model: str) -> Optional[int]:
    """Context window for a model read from the endpoint's /models catalog.

    Fetches the catalog once per endpoint and caches the full id->context map,
    so an api/proxy endpoint serving a model that isn't in KNOWN_CONTEXT_WINDOWS
    (e.g. a new OpenRouter model) still reports its real window instead of the
    bare default. Returns None when the catalog can't be read or doesn't list a
    positive window for the model.
    """
    cat = _catalog_ctx_cache.get(endpoint_url)
    if cat is None:
        from src.endpoint_resolver import build_models_url
        try:
            r = httpx.get(build_models_url(endpoint_url), timeout=REQUEST_TIMEOUT)
        except Exception as e:
            logger.debug(f"Failed to fetch proxy catalog for context length: {e}")
            return None
        if not r.is_success:
            return None
        cat = {}
        try:
            for m in (r.json().get("data") or []):
                mid = m.get("id") if isinstance(m, dict) else None
                ctx = _model_ctx_from_entry(m) if mid else None
                if mid and ctx:
                    cat[mid] = ctx
        except Exception as e:
            logger.debug(f"Failed to parse proxy catalog for context length: {e}")
            return None
        _catalog_ctx_cache[endpoint_url] = cat

    if model in cat:
        return cat[model]
    # Catalog ids may carry a provider prefix (e.g. "openai/gpt-4o") while the
    # session stores the bare id; match on the trailing segment as a fallback.
    base = model.split("/")[-1]
    for mid, ctx in cat.items():
        if mid.split("/")[-1] == base:
            return ctx
    return None


def _query_context_length(endpoint_url: str, model: str) -> Tuple[int, bool]:
    """Query the model API for context length. Returns (context_length, known) where
    ``known`` is False only for the bare DEFAULT_CONTEXT fallback."""
    known = _lookup_known(model)
    api_ctx = None
    configured_kind = _configured_endpoint_kind(endpoint_url)

    # Large OpenAI-compatible proxies can make /models expensive. If the
    # endpoint is explicitly configured as API/proxy, prefer known context
    # metadata (or the default) over downloading the full catalog.
    if configured_kind in ("api", "proxy"):
        if known:
            logger.info(f"Using known context window for {model}: {known}")
            return known, True
        # Not in the known table: read the real window from the catalog (cached
        # once per endpoint) instead of capping every unknown model at the
        # default — that under-reported large windows on aggregators like
        # OpenRouter (issue #4886).
        api_ctx = _proxy_catalog_context(endpoint_url, model)
        if api_ctx:
            logger.info(f"Proxy catalog reports context window for {model}: {api_ctx}")
            return api_ctx, True
        return DEFAULT_CONTEXT, False

    # Try llama.cpp /slots endpoint first — reports actual serving context
    if is_local_endpoint(endpoint_url):
        base = endpoint_url.split("/v1")[0] if "/v1" in endpoint_url else endpoint_url.rsplit("/", 1)[0]
        try:
            r = httpx.get(f"{base}/slots", timeout=REQUEST_TIMEOUT)
            if r.is_success:
                slots = r.json()
                if isinstance(slots, list) and slots:
                    n_ctx = slots[0].get("n_ctx")
                    if n_ctx and isinstance(n_ctx, int) and n_ctx > 0:
                        logger.info(f"llama.cpp /slots reports n_ctx={n_ctx} for {model}")
                        return n_ctx, True
        except Exception:
            pass

        runtime_context = _freetoken_runtime_context(base, model)
        if runtime_context:
            logger.info(
                "FreeToken runtime reports effective context capacity=%s for %s",
                runtime_context,
                model,
            )
            return runtime_context, True

    # GitHub Copilot's /models requires auth + X-GitHub-Api-Version headers that
    # aren't available here; an unauthenticated probe just 400s. All Copilot
    # picker models are major API models covered by the known-context table, so
    # rely on that instead of a doomed network call.
    from src.copilot import is_copilot_base
    if is_copilot_base(endpoint_url):
        if known:
            logger.info(f"Using known context window for {model}: {known}")
            return known, True
        return DEFAULT_CONTEXT, False

    from src.endpoint_resolver import build_models_url

    models_url = build_models_url(endpoint_url)
    try:
        r = httpx.get(models_url, timeout=REQUEST_TIMEOUT)
        if r.is_success:
            data = r.json()
            models_list = data.get("data") or []

            for m in models_list:
                mid = m.get("id", "")
                if mid == model or mid.split("/")[-1] == model.split("/")[-1]:
                    api_ctx = _model_ctx_from_entry(m)
                    break
    except Exception as e:
        logger.debug(f"Failed to query context length for {model}: {e}")

    # For local/self-hosted endpoints, trust the API value (user set --max-model-len)
    # For cloud APIs, use the larger value (API can report low defaults)
    if api_ctx and known:
        _is_local = is_local_endpoint(endpoint_url)
        if _is_local and api_ctx < known:
            logger.info(f"Local endpoint reports {api_ctx} for {model} (known max: {known}) — using API value")
            return api_ctx, True
        result = max(api_ctx, known)
        if api_ctx < known:
            logger.info(f"API reported {api_ctx} for {model}, using known {known} instead")
        return result, True
    if api_ctx:
        return api_ctx, True
    if known:
        logger.info(f"Using known context window for {model}: {known}")
        return known, True

    return DEFAULT_CONTEXT, False


def estimate_tokens(messages: List[Dict]) -> int:
    """Rough token estimate for a list of messages.

    Uses chars * 0.3 which is closer to real BPE tokenizer output
    than the commonly-cited chars/4 (which underestimates by ~20-30%).
    Also adds ~4 tokens per message for role/formatting overhead, and counts
    assistant tool_calls (name + arguments) — a tool-only turn carries
    content=None with the real payload in tool_calls, so ignoring them made the
    estimate (and the compaction/trim gates that rely on it) blind to large
    tool arguments.
    """
    total = 0
    for msg in messages:
        total += 4  # per-message overhead (role, separators)
        content = msg.get("content", "")
        if isinstance(content, str):
            total += int(len(content) * 0.3)
        elif isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    total += int(len(item.get("text", "")) * 0.3)
        # Tool calls carry real payload too: a tool-only assistant turn is stored
        # with content=None and the actual args (e.g. a create_document body) in
        # tool_calls[].function.arguments. Ignoring them made large tool arguments
        # read as ~0 tokens, so the compaction/trim gates missed genuine overflow.
        tool_calls = msg.get("tool_calls")
        if isinstance(tool_calls, list):
            for tc in tool_calls:
                if not isinstance(tc, dict):
                    continue
                fn = tc.get("function") if isinstance(tc.get("function"), dict) else tc
                name = fn.get("name", "") or ""
                args = fn.get("arguments", "") or ""
                if not isinstance(args, str):
                    args = str(args)  # some shapes store arguments as a dict
                total += 4  # per tool-call overhead (id, type, wrapper)
                total += int((len(str(name)) + len(args)) * 0.3)
    return total


def estimate_tool_schema_tokens(schemas: Optional[List[Dict]]) -> int:
    """Estimate provider-input tokens consumed by native function schemas."""
    valid = [schema for schema in schemas or [] if isinstance(schema, dict)]
    return int(len(json.dumps(valid, sort_keys=True, default=str)) * 0.3)


def cap_tool_schemas(
    schemas: Optional[List[Dict]],
    input_budget: int,
    *,
    priority_names: Optional[set[str]] = None,
) -> Tuple[List[Dict], List[str]]:
    """Fit whole native tool schemas inside the JOS tool-catalog ceiling.

    Schemas are never truncated because malformed JSON schema is worse than an
    omitted capability. Explicit/extension names may be ranked first, but the
    ceiling remains hard so a single pathological schema cannot take the turn.
    """
    valid = [schema for schema in schemas or [] if isinstance(schema, dict)]
    limit = max(
        int(max(int(input_budget or 0), 0) * context_class_budget_percent()["tool_catalog"] / 100),
        64,
    )
    priority = set(priority_names or set())

    def _name(schema: Dict) -> str:
        function = schema.get("function") if isinstance(schema.get("function"), dict) else {}
        return str(function.get("name") or schema.get("name") or "")

    ranked = sorted(
        enumerate(valid),
        key=lambda item: (0 if _name(item[1]) in priority else 1, item[0]),
    )
    kept_ranked: List[Tuple[int, Dict]] = []
    dropped: List[str] = []
    used = 2  # list wrapper overhead
    for original_index, schema in ranked:
        schema_tokens = max(int(len(json.dumps(schema, sort_keys=True, default=str)) * 0.3), 1)
        if used + schema_tokens <= limit:
            kept_ranked.append((original_index, schema))
            used += schema_tokens
        else:
            dropped.append(_name(schema) or f"schema_{original_index}")
    kept = [schema for _, schema in sorted(kept_ranked, key=lambda item: item[0])]
    return kept, dropped


_UNTRUSTED_SOURCE_CLASSES = (
    (("karpathywiki", "derived wiki"), "derived_knowledge"),
    (("saved memory",), "recalled_memory"),
    (("active editor", "active email", "uploaded files"), "working_state"),
    (("tool execution results", "tool result"), "tool_result"),
    (("mcp tools", "integrations", "skills", "available skills"), "tool_catalog"),
    (("extension.", "oracle"), "extension_state"),
)

_SAFE_SOURCE_PREFIXES = (
    ("karpathywiki", "wiki.derived"),
    ("derived wiki", "wiki.derived"),
    ("saved memory: pinned", "memory.pinned"),
    ("saved memory: retrieved", "memory.recalled"),
    ("retrieved documents", "documents.rag"),
    ("web search results", "web.search"),
    ("web page:", "web.page"),
    ("prefetched search context", "web.prefetched"),
    ("youtube transcript", "youtube.transcript"),
    ("research context", "research.context"),
    ("injected research context", "research.context"),
    ("active editor document", "working_state.document"),
    ("active email reader", "working_state.email"),
    ("current chat uploaded files", "working_state.uploads"),
    ("tool execution results", "tool.execution_result"),
    ("available skills index", "skills.catalog"),
    ("skills", "skills.context"),
    ("integrations", "integrations.catalog"),
    ("mcp tools", "mcp.catalog"),
)


def _safe_source_name(source: str, fallback: str) -> str:
    lowered = source.strip().lower()
    for prefix, safe_name in _SAFE_SOURCE_PREFIXES:
        if lowered.startswith(prefix):
            return safe_name
    return fallback


def _context_tag(message: Dict[str, Any]) -> Dict[str, str]:
    """Return the server-owned JOS context tag for one internal message."""
    metadata = message.get("metadata") if isinstance(message.get("metadata"), dict) else {}
    explicit = metadata.get("jos_context") if isinstance(metadata.get("jos_context"), dict) else {}
    if explicit.get("class"):
        tag = {
            "class": str(explicit["class"]),
            "source": str(explicit.get("source") or metadata.get("source") or "unknown"),
            "trust": str(explicit.get("trust") or "unknown"),
        }
        extension_id = explicit.get("extension_id") or metadata.get("extension_id")
        if extension_id:
            tag["extension_id"] = str(extension_id)
        return tag

    source = str(metadata.get("source") or "").strip()
    source_lower = source.lower()
    if metadata.get("trusted") is False:
        context_class = "retrieved_knowledge"
        for prefixes, candidate in _UNTRUSTED_SOURCE_CLASSES:
            if source_lower.startswith(prefixes):
                context_class = candidate
                break
        return {
            "class": context_class,
            "source": _safe_source_name(source, "external.data"),
            "trust": "untrusted_data",
        }

    role = str(message.get("role") or "")
    content = message.get("content")
    if metadata.get("research_spinoff_from"):
        return {
            "class": "retrieved_knowledge",
            "source": "research.primer",
            "trust": "untrusted_data",
        }
    if metadata.get("compacted") or (
        isinstance(content, str) and content.startswith("[Conversation summary")
    ):
        return {
            "class": "conversation",
            "source": "session.compaction_summary",
            "trust": "derived",
        }
    if role == "tool":
        return {
            "class": "tool_result",
            "source": source or "tool.execution_result",
            "trust": "untrusted_data",
        }
    if role == "system":
        return {
            "class": "identity_policy",
            "source": source or "odysseus.system",
            "trust": "system_authority",
        }
    return {
        "class": "conversation",
        "source": "session.history",
        "trust": "canonical_history",
    }


def annotate_context_messages(messages: List[Dict]) -> List[Dict]:
    """Copy messages and add internal JOS tags without touching provider content."""
    copied = [dict(message) for message in messages or [] if isinstance(message, dict)]
    latest_operator = None
    for index in range(len(copied) - 1, -1, -1):
        message = copied[index]
        metadata = message.get("metadata") if isinstance(message.get("metadata"), dict) else {}
        if message.get("role") == "user" and metadata.get("trusted") is not False:
            explicit = metadata.get("jos_context") if isinstance(metadata.get("jos_context"), dict) else {}
            if explicit.get("class") not in {
                "time", "working_state", "retrieved_knowledge",
                "recalled_memory", "tool_catalog",
            }:
                latest_operator = index
                break

    for index, message in enumerate(copied):
        metadata = dict(message.get("metadata") or {})
        if not isinstance(metadata.get("jos_context"), dict):
            tag = _context_tag(message)
            tag["inferred"] = True
            if index == latest_operator and tag["class"] == "conversation":
                tag = {
                    "class": "operator_intent",
                    "source": "operator.current_turn",
                    "trust": "operator_authority",
                    "inferred": True,
                }
            metadata["jos_context"] = tag
        message["metadata"] = metadata
    return copied


def _summarize_context(messages: List[Dict]) -> Dict[str, Any]:
    classes: Dict[str, Dict[str, int]] = {}
    trust: Dict[str, Dict[str, int]] = {}
    sources: Dict[Tuple[str, str, str, str], Dict[str, Any]] = {}
    annotated = annotate_context_messages(messages)
    for message in annotated:
        tag = message["metadata"]["jos_context"]
        tokens = estimate_tokens([message])
        class_row = classes.setdefault(tag["class"], {"messages": 0, "tokens": 0})
        class_row["messages"] += 1
        class_row["tokens"] += tokens
        trust_row = trust.setdefault(tag["trust"], {"messages": 0, "tokens": 0})
        trust_row["messages"] += 1
        trust_row["tokens"] += tokens
        extension_id = str(tag.get("extension_id") or "")
        key = (tag["class"], tag["source"], tag["trust"], extension_id)
        source_row = sources.setdefault(key, {
            "class": tag["class"],
            "source": tag["source"],
            "trust": tag["trust"],
            "messages": 0,
            "tokens": 0,
        })
        if extension_id:
            source_row["extension_id"] = extension_id
        source_row["messages"] += 1
        source_row["tokens"] += tokens
    return {
        "messages": len(annotated),
        "tokens": estimate_tokens(annotated),
        "classes": dict(sorted(classes.items())),
        "trust": dict(sorted(trust.items())),
        "sources": sorted(sources.values(), key=lambda row: (row["class"], row["source"])),
    }


def _tool_catalog_report(tool_catalog: Optional[Dict[str, List[Dict]]]) -> Dict[str, Any]:
    report: Dict[str, Any] = {"schema_tokens": 0, "total": 0}
    for source, schemas in sorted((tool_catalog or {}).items()):
        valid = [schema for schema in schemas or [] if isinstance(schema, dict)]
        names = sorted({
            str((schema.get("function") or {}).get("name") or schema.get("name") or "")
            for schema in valid
            if (schema.get("function") or {}).get("name") or schema.get("name")
        })
        report[source] = {"count": len(names), "names": names}
        report["total"] += len(names)
        report["schema_tokens"] += estimate_tool_schema_tokens(valid)
    return report


def build_context_manifest(
    messages: List[Dict],
    context_length: int,
    *,
    before_messages: Optional[List[Dict]] = None,
    was_compacted: bool = False,
    omissions: Optional[List[str]] = None,
    tool_catalog: Optional[Dict[str, List[Dict]]] = None,
    extensions: Optional[Dict[str, Dict[str, Any]]] = None,
    input_budget: Optional[int] = None,
) -> Dict[str, Any]:
    """Build a content-free JOS-P2A report for the exact mounted context."""
    mounted = _summarize_context(messages)
    before = _summarize_context(before_messages if before_messages is not None else messages)
    dropped: Dict[str, Dict[str, int]] = {}
    for name, row in before["classes"].items():
        after_row = mounted["classes"].get(name, {})
        message_delta = max(row["messages"] - int(after_row.get("messages", 0)), 0)
        token_delta = max(row["tokens"] - int(after_row.get("tokens", 0)), 0)
        if message_delta or token_delta:
            dropped[name] = {"messages": message_delta, "tokens": token_delta}

    summaries = [
        message for message in annotate_context_messages(messages)
        if message["metadata"]["jos_context"]["source"] == "session.compaction_summary"
    ]
    safe_extensions = {
        str(name): {
            "engaged": bool((state or {}).get("engaged")),
            "state_mounted": bool((state or {}).get("state_mounted")),
            "tool_count": max(int((state or {}).get("tool_count") or 0), 0),
            "skill_count": max(int((state or {}).get("skill_count") or 0), 0),
        }
        for name, state in sorted((extensions or {}).items())
        if isinstance(state, dict)
    }
    class_percent = context_class_budget_percent()
    effective_budget = max(int(input_budget if input_budget is not None else context_length or 0), 0)
    return {
        "version": "jos-p2a.1",
        "context_window": max(int(context_length or 0), 0),
        "budget": {
            "input_tokens": effective_budget,
            "class_percent": class_percent,
            "class_token_limits": {
                name: int(effective_budget * percent / 100)
                for name, percent in class_percent.items()
            },
        },
        "mounted": mounted,
        "trimming": {
            "ran": before["messages"] != mounted["messages"] or before["tokens"] != mounted["tokens"],
            "before_messages": before["messages"],
            "after_messages": mounted["messages"],
            "before_tokens": before["tokens"],
            "after_tokens": mounted["tokens"],
            "dropped_by_class": dropped,
        },
        "compaction": {
            "ran": bool(was_compacted),
            "summary_messages": len(summaries),
            "summarized_messages": sum(
                int((message.get("metadata") or {}).get("summarized_count") or 0)
                for message in summaries
            ),
        },
        "omissions": sorted(
            {str(reason) for reason in omissions or [] if reason}
            | {f"{name}_budget_limited" for name in dropped}
        ),
        "tools": _tool_catalog_report(tool_catalog),
        "extensions": safe_extensions,
    }
