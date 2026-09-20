"""MAD-972: strict provider contracts, conditional launcher and playback handoff."""

import asyncio
import importlib.util
import json
from pathlib import Path

import pytest

import routes.entertainment_routes as routes
from src.entertainment import PROVIDERS, is_provider, schemas
from src.extension_cli_adapter import validate_cli_execution
from src.extension_installer import ExtensionLifecycleError
from src.extension_intake import Proposal

ROOT = Path(__file__).resolve().parents[1]


def _proposal(name):
    return json.loads((ROOT / "integrations/repository-packages" / name / "proposal.json").read_text())


def _endpoint(router, path, method):
    return next(route.endpoint for route in router.routes if route.path == path and method in route.methods)


def test_marketplace_recipes_are_complete_upstream_ui_contracts():
    for name, operation_count in (("ani-cli", 6), ("pandaflix", 5)):
        data = _proposal(name)
        Proposal.model_validate(data)
        validate_cli_execution(data["manifest"], data)
        assert is_provider(data["manifest"])
        assert len(data["interfaces"]) == operation_count
        assert data["execution"]["checks"] == [{
            "name": PROVIDERS[name]["tools"]["status"][0], "arguments": {}, "expected": None,
        }]
        for interface in data["interfaces"]:
            assert schemas(interface["binding"]) == (
                interface["tool_schema"]["function"]["parameters"], interface["output_schema"]
            )
    assert "countdown" not in json.dumps(_proposal("ani-cli")).lower()
    assert not (ROOT / "integrations/repository-packages/pandaflix/main.go").exists()


def test_entertainment_contract_rejects_partial_provider_packages():
    data = _proposal("ani-cli")
    data["interfaces"].pop()
    data["manifest"]["capabilities"]["schemas"].pop()
    data["manifest"]["permissions"]["capabilities"].pop("ani_cli__continue")
    with pytest.raises(ExtensionLifecycleError, match="extension_entertainment_contract_invalid"):
        validate_cli_execution(data["manifest"], data)


@pytest.mark.parametrize("ids", [[], ["ani-cli"], ["ani-cli", "pandaflix"]])
def test_status_reports_exactly_the_owner_installed_providers(monkeypatch, ids):
    records = {key: {"enabled": key != "pandaflix"} for key in ids}
    monkeypatch.setattr(routes, "_installed", lambda owner: (owner, records))
    payload = _endpoint(routes.setup_entertainment_routes(), "/api/entertainment", "GET")("owner")
    assert [item["id"] for item in payload["providers"]] == ids
    assert [item["label"] for item in payload["providers"]] == [PROVIDERS[key]["label"] for key in ids]


def test_playback_is_direct_first_and_proxy_tokens_are_owner_scoped(monkeypatch):
    monkeypatch.setattr(routes, "validate_public_http_url", lambda url, **_: url)
    routes._TOKENS.clear()
    direct = asyncio.run(routes._prepare_playback("alice", {
        "title": "Direct", "url": "https://cdn.example/video.mp4", "headers": {}, "subtitles": [],
    }))
    assert direct["url"] == "https://cdn.example/video.mp4"
    assert direct["proxied"] is False and direct["format"] == "file"

    proxied = asyncio.run(routes._prepare_playback("alice", {
        "title": "HLS", "url": "https://cdn.example/master.m3u8",
        "headers": {"Referer": "https://provider.example/", "X-Unsafe": "blocked"},
        "subtitles": [{"url": "https://cdn.example/sub.vtt", "label": "English", "language": "en"}],
    }))
    token = proxied["url"].rsplit("/", 1)[-1]
    assert proxied["proxied"] is True and proxied["format"] == "hls"
    assert routes._TOKENS[token].owner == "alice"
    assert routes._TOKENS[token].headers == {"Referer": "https://provider.example/"}
    assert proxied["subtitles"][0]["url"].startswith("/api/entertainment/proxy/")


def test_playlist_rewrites_segments_and_embedded_subtitle_uris(monkeypatch):
    monkeypatch.setattr(routes, "validate_public_http_url", lambda url, **_: url)
    routes._TOKENS.clear()
    playlist = '#EXTM3U\n#EXT-X-MEDIA:TYPE=SUBTITLES,URI="subs/en.m3u8"\nvideo/720.m3u8\n'
    rewritten = asyncio.run(routes._playlist("owner", "https://cdn.example/master.m3u8", playlist, {"Referer": "https://origin.example/"}))
    assert rewritten.count("/api/entertainment/proxy/") == 2
    assert {target.url for target in routes._TOKENS.values()} == {
        "https://cdn.example/subs/en.m3u8", "https://cdn.example/video/720.m3u8",
    }


def test_realistic_playlist_retry_reuses_tokens_without_revalidating_segments(monkeypatch):
    validated = []
    monkeypatch.setattr(
        routes,
        "validate_public_http_url",
        lambda url, **_: validated.append(url) or url,
    )
    routes._TOKENS.clear()
    try:
        playback = asyncio.run(routes._prepare_playback("owner", {
            "title": "Episode", "url": "https://cdn.example/episode.m3u8",
            "headers": {"Referer": "https://origin.example/"}, "subtitles": [],
        }))
        root_token = playback["url"].rsplit("/", 1)[-1]
        playlist = "#EXTM3U\n" + "".join(
            f"#EXTINF:4.0,\nsegment-{index}.ts\n" for index in range(309)
        )

        first = asyncio.run(routes._playlist(
            "owner", "https://cdn.example/episode.m3u8", playlist,
            {"Referer": "https://origin.example/"},
        ))
        second = asyncio.run(routes._playlist(
            "owner", "https://cdn.example/episode.m3u8", playlist,
            {"Referer": "https://origin.example/"},
        ))

        assert first == second
        assert root_token in routes._TOKENS
        assert len(routes._TOKENS) == 310
        assert validated == ["https://cdn.example/episode.m3u8"]
    finally:
        routes._TOKENS.clear()


def test_proxy_open_rejects_private_targets_before_transport():
    target = routes._ProxyTarget("owner", "http://127.0.0.1/media.ts", {}, float("inf"))
    with pytest.raises(ValueError, match="public HTTP"):
        asyncio.run(routes._open(target, None))


def test_generated_player_argument_parsers_keep_real_headers_and_subtitles(tmp_path):
    cases = (
        ("ani-cli", ["--referrer=https://hianime.at/", "--sub-file=https://cdn.example/sub.vtt", "--force-media-title=Show Episode 1", "https://cdn.example/master.m3u8"]),
        ("pandaflix", ["https://cdn.example/master.m3u8", "--referrer=https://cinejoy.to/", "--user-agent=Browser", "--http-header-fields=Origin: https://cinejoy.to", "--sub-file=https://cdn.example/sub.vtt", "--force-media-title=Playing Movie"]),
    )
    for name, argv in cases:
        path = ROOT / "integrations/repository-packages" / name / "adapter.py"
        spec = importlib.util.spec_from_file_location("adapter_" + name.replace("-", "_"), path)
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        (tmp_path / "playback.json").write_text(json.dumps(argv))
        result = module._playback(tmp_path)
        assert result["url"].endswith("master.m3u8")
        assert result["headers"]["Referer"].startswith("https://")
        assert result["subtitles"][0]["url"].endswith("sub.vtt")


def test_browser_surface_has_conditional_launcher_and_no_voice_hook():
    source = (ROOT / "static/js/entertainment.js").read_text()
    settings = (ROOT / "static/js/settings.js").read_text()
    index = (ROOT / "static/index.html").read_text()
    assert "providers.length === 1" in source
    assert "providers.length > 1" in source
    assert "providers.length > 0" in source
    style = (ROOT / "static/style.css").read_text()
    # The Anime hero uses the provided key art; the next episode is warmed into
    # the idle player; the ENTERTAINMENT pill plays the fanfare.
    assert "hero-anime.webp" in style and "--ent-hero-art" in style
    assert "schedulePreload" in source and 'id="entertainment-video-next"' in index
    assert 'id="entertainment-fanfare"' in index and "entertainment-fanfare-btn" in source
    assert "requestFullscreen" in source and "window.Hls" in source
    assert "voice" not in source.lower()
    # The launcher lives in the sidebar; Settings owns the defaults panel.
    assert "refreshEntertainment" in settings and "openEntertainment" in source
    # The launcher is a solo clickable sidebar row, not a collapsible section.
    assert 'class="list-item hidden" id="entertainment-section"' in index
    assert 'id="tool-entertainment-btn"' not in index
    assert '<span class="grow">Open Entertainment</span>' not in index
    assert 'data-settings-tab="entertainment"' in index
    assert 'data-settings-panel="entertainment"' in index
    # Player controls: next/previous, autoplay, and episode jump.
    assert "entertainment-next" in index and "entertainment-autoplay" in index
    assert "entertainment-jump" in index and "entertainment-prev" in index
    assert "nextEpisode" in source and "previousEpisode" in source and "episodeTarget" in source
    assert "pendingResume" in source and "data-ent-fav" in source and "rememberResume" in source
