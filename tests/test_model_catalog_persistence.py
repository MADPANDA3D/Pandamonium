"""Provider catalog windows persist per endpoint so budgets stay model-aware."""

import json

import src.model_context as model_context


def _response(models):
    class _Response:
        is_success = True

        def json(self):
            return {
                "data": [{"id": mid, "context_length": ctx} for mid, ctx in models.items()]
            }

    return _Response()


def _store(tmp_path, endpoints=None):
    path = tmp_path / "model_context_catalog.json"
    if endpoints is not None:
        path.write_text(
            json.dumps({"version": 1, "endpoints": endpoints}), encoding="utf-8"
        )
    return path


def test_successful_catalog_fetch_persists_windows(monkeypatch, tmp_path):
    store = _store(tmp_path)
    monkeypatch.setattr(model_context, "_CATALOG_STORE_FILE", store)
    monkeypatch.setattr(model_context, "_configured_endpoint_kind", lambda url: "proxy")
    monkeypatch.setattr(
        model_context.httpx,
        "get",
        lambda url, timeout=5: _response({"deepseek/deepseek-v4.1-flash": 1048576}),
    )
    model_context._reset_catalog_state()

    ctx, known = model_context._query_context_length(
        "https://openrouter.ai/api/v1", "deepseek/deepseek-v4.1-flash"
    )

    assert (ctx, known) == (1048576, True)
    saved = json.loads(store.read_text(encoding="utf-8"))
    assert (
        saved["endpoints"]["https://openrouter.ai/api/v1"]["deepseek/deepseek-v4.1-flash"]
        == 1048576
    )


def test_persisted_window_survives_restart_and_failed_fetch(monkeypatch, tmp_path):
    store = _store(
        tmp_path,
        {"https://openrouter.ai/api/v1": {"deepseek/deepseek-v4.1-flash": 1048576}},
    )
    monkeypatch.setattr(model_context, "_CATALOG_STORE_FILE", store)
    monkeypatch.setattr(model_context, "_configured_endpoint_kind", lambda url: "proxy")

    def _fail(url, timeout=5):
        raise RuntimeError("network down")

    monkeypatch.setattr(model_context.httpx, "get", _fail)
    model_context._reset_catalog_state()

    ctx, known = model_context._query_context_length(
        "https://openrouter.ai/api/v1", "deepseek/deepseek-v4.1-flash"
    )

    assert (ctx, known) == (1048576, True)


def test_operator_override_beats_persisted(monkeypatch, tmp_path):
    store = _store(
        tmp_path,
        {"https://openrouter.ai/api/v1": {"deepseek/deepseek-v4.1-flash": 65536}},
    )
    monkeypatch.setattr(model_context, "_CATALOG_STORE_FILE", store)
    monkeypatch.setattr(model_context, "configured_model_window", lambda model: 131072)
    model_context._reset_catalog_state()

    ctx, known = model_context._query_context_length(
        "https://openrouter.ai/api/v1", "deepseek/deepseek-v4.1-flash"
    )

    assert (ctx, known) == (131072, True)


def test_persisted_beats_stale_table(monkeypatch, tmp_path):
    store = _store(
        tmp_path,
        {"https://openrouter.ai/api/v1": {"deepseek/deepseek-chat": 163840}},
    )
    monkeypatch.setattr(model_context, "_CATALOG_STORE_FILE", store)
    model_context._reset_catalog_state()

    ctx, known = model_context._query_context_length(
        "https://openrouter.ai/api/v1", "deepseek/deepseek-chat"
    )

    assert (ctx, known) == (163840, True)  # built-in table says 64000


def test_persisted_trailing_segment_match(monkeypatch, tmp_path):
    store = _store(tmp_path, {"https://api.test/v1": {"openai/gpt-4o": 111111}})
    monkeypatch.setattr(model_context, "_CATALOG_STORE_FILE", store)
    model_context._reset_catalog_state()

    ctx, known = model_context._query_context_length("https://api.test/v1", "gpt-4o")

    assert (ctx, known) == (111111, True)


def test_unknown_stays_unknown_without_persisted(monkeypatch, tmp_path):
    store = _store(tmp_path)
    monkeypatch.setattr(model_context, "_CATALOG_STORE_FILE", store)
    monkeypatch.setattr(model_context, "_configured_endpoint_kind", lambda url: "proxy")
    monkeypatch.setattr(
        model_context.httpx,
        "get",
        lambda url, timeout=5: _response({"vendor/other-model": 32000}),
    )
    model_context._reset_catalog_state()

    ctx, known = model_context._query_context_length("https://api.test/v1", "vendor/mystery-v9")

    assert known is False
    assert ctx == model_context.DEFAULT_CONTEXT


def test_persisted_store_contains_no_secrets(monkeypatch, tmp_path):
    store = _store(tmp_path)
    monkeypatch.setattr(model_context, "_CATALOG_STORE_FILE", store)
    model_context._reset_catalog_state()

    model_context._persist_catalog("https://api.test/v1", {"vendor/model": 64000})

    raw = store.read_text(encoding="utf-8")
    lowered = raw.lower()
    assert "sk-" not in raw and "token" not in lowered and "api_key" not in lowered
    saved = json.loads(raw)
    assert saved["endpoints"]["https://api.test/v1"] == {"vendor/model": 64000}
