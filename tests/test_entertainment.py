"""MAD-972: strict provider contracts, conditional launcher and playback handoff."""

import asyncio
import importlib.util
import json
import time
from pathlib import Path

import pytest
from fastapi import HTTPException

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


class _FakeJson:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _FakeResponse:
    def __init__(self, status_code, content_type, body=b"", headers=None):
        self.status_code = status_code
        self.headers = {"content-type": content_type, **(headers or {})}
        self._body = body
        self.closed = False

    async def aiter_bytes(self):
        yield self._body

    async def aclose(self):
        self.closed = True


def test_metadata_route_wraps_covers_in_owner_scoped_image_tokens(monkeypatch):
    routes._TOKENS.clear()

    async def fake_metadata(provider, items):
        assert provider == "ani-cli"
        return {"Naruto": "https://s4.anilist.co/cover.jpg"}

    monkeypatch.setattr(routes, "_metadata", fake_metadata)
    endpoint = _endpoint(routes.setup_entertainment_routes(), "/api/entertainment/metadata", "POST")
    payload = asyncio.run(endpoint({"provider": "ani-cli", "items": [{"title": "Naruto"}]}, "alice"))
    url = payload["covers"]["Naruto"]
    token = url.rsplit("/", 1)[-1]
    assert url.startswith("/api/entertainment/image/")
    assert routes._TOKENS[token].owner == "alice"
    assert routes._TOKENS[token].url == "https://s4.anilist.co/cover.jpg"
    assert routes._TOKENS[token].headers == {"User-Agent": routes._IMAGE_UA}


def test_metadata_route_rejects_unknown_provider():
    endpoint = _endpoint(routes.setup_entertainment_routes(), "/api/entertainment/metadata", "POST")
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(endpoint({"provider": "nope", "items": []}, "alice"))
    assert excinfo.value.status_code == 400


def test_metadata_cache_avoids_repeat_lookups(monkeypatch):
    routes._METADATA.clear()
    calls = []

    async def fake_anilist(titles):
        calls.append(list(titles))
        return {titles[0]: "https://s4.anilist.co/cover.jpg"}

    monkeypatch.setattr(routes, "_anilist_covers", fake_anilist)
    first = asyncio.run(routes._metadata("ani-cli", [{"title": "Naruto"}]))
    second = asyncio.run(routes._metadata("ani-cli", [{"title": "Naruto"}]))
    assert first == second == {"Naruto": "https://s4.anilist.co/cover.jpg"}
    assert calls == [["Naruto"]]


def test_tmdb_cover_builds_native_tmdb_poster_url(monkeypatch):
    class _Client:
        async def get(self, url, params=None):
            assert url == routes._TMDB_SEARCH + "/search/movie"
            assert params["query"] == "The Blacklist"
            assert params["year"] == "2021"
            return _FakeJson({"results": [{"title": "The Blacklist", "poster_path": "/wanted.jpg"}]})

    monkeypatch.setattr(routes, "_meta_client", lambda: _Client())
    cover = asyncio.run(routes._tmdb_cover(_Client(), "The Blacklist (2021)", "movie", "key"))
    assert cover == routes._TMDB_IMAGE + "/wanted.jpg"


def test_tmdb_key_reads_the_installed_pandaflix_source(monkeypatch, tmp_path):
    monkeypatch.delenv("PANDAMONIUM_TMDB_API_KEY", raising=False)
    monkeypatch.delenv("TMDB_API_KEY", raising=False)
    source = tmp_path / "installed/pandaflix/revisions/abc/core/tmdb.go"
    source.parent.mkdir(parents=True)
    key = "".join(["653bb8af", "90162bd9", "8fc7ee32", "bcbbfb3d"])
    source.write_text(f'const TMDB_API_KEY = "{key}"')
    monkeypatch.setattr(routes, "_TMDB_KEY_CACHE", None)
    monkeypatch.setattr("src.extension_installer.default_extensions_root", lambda: tmp_path)
    assert routes._tmdb_key() == key


def test_split_year_removes_the_trailing_release_year():
    assert routes._split_year("The Blacklist (2013)") == ("The Blacklist", "2013")
    assert routes._split_year("Naruto: Shippuden") == ("Naruto: Shippuden", "")


def test_split_year_removes_the_trailing_release_year():
    assert routes._split_year("The Blacklist (2013)") == ("The Blacklist", "2013")
    assert routes._split_year("Naruto: Shippuden") == ("Naruto: Shippuden", "")


def test_wikipedia_cover_resolves_poster_thumbnail(monkeypatch):
    class _Client:
        async def get(self, url, params=None):
            if url == routes._WIKIPEDIA_SEARCH:
                return _FakeJson({"query": {"search": [{"title": "Arrival (film)"}]}})
            return _FakeJson({"thumbnail": {"source": "https://upload.wikimedia.org/arrival.jpg"}})

    monkeypatch.setattr(routes, "_meta_client", lambda: _Client())
    assert asyncio.run(routes._wikipedia_cover("Arrival", "movie")) == "https://upload.wikimedia.org/arrival.jpg"


def test_wikipedia_cover_rejects_unrelated_search_hit(monkeypatch):
    class _Client:
        async def get(self, url, params=None):
            if url == routes._WIKIPEDIA_SEARCH:
                return _FakeJson({"query": {"search": [{"title": "Designated Survivor (TV series)"}]}})
            if "Designated" in url:
                return _FakeJson({"thumbnail": {"source": "https://upload.wikimedia.org/wrong.jpg"}})
            return _FakeJson({})

    monkeypatch.setattr(routes, "_meta_client", lambda: _Client())
    assert asyncio.run(routes._wikipedia_cover("The Wake: Blacklist", "movie")) is None


def test_tvmaze_cover_scores_the_show_name(monkeypatch):
    class _Client:
        async def get(self, url, params=None):
            return _FakeJson([
                {"show": {"name": "The Blacklist", "image": {"original": "https://static.tvmaze.com/blacklist.jpg"}}},
                {"show": {"name": "Black-ish", "image": {"original": "https://static.tvmaze.com/blackish.jpg"}}},
            ])

    monkeypatch.setattr(routes, "_meta_client", lambda: _Client())
    assert asyncio.run(routes._tvmaze_cover("The Blacklist")) == "https://static.tvmaze.com/blacklist.jpg"


def test_image_proxy_serves_only_public_image_content(monkeypatch):
    routes._TOKENS.clear()
    token = asyncio.run(routes._token("alice", "https://s4.anilist.co/cover.jpg", {}))
    good = _FakeResponse(200, "image/jpeg", b"\xff\xd8\xff")

    async def fake_open(target, range_header):
        return None, good, target.url

    monkeypatch.setattr(routes, "_open", fake_open)
    endpoint = _endpoint(routes.setup_entertainment_routes(), "/api/entertainment/image/{token}", "GET")
    response = asyncio.run(endpoint(token, "alice"))
    assert response.media_type == "image/jpeg"
    assert response.headers["cache-control"].startswith("private")

    bad = _FakeResponse(200, "text/html", b"<html>")

    async def fake_open_bad(target, range_header):
        return None, bad, target.url

    monkeypatch.setattr(routes, "_open", fake_open_bad)
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(endpoint(token, "alice"))
    assert excinfo.value.status_code == 502
    assert bad.closed


def test_active_streams_refresh_their_token_expiry():
    routes._TOKENS.clear()
    token = asyncio.run(routes._token("alice", "https://cdn.example/seg.ts", {}))
    stale = routes._ProxyTarget("alice", "https://cdn.example/seg.ts", {}, time.monotonic() + 5)
    routes._TOKENS[token] = stale
    asyncio.run(routes._touch(token, stale))
    assert routes._TOKENS[token].expires > time.monotonic() + routes._TOKEN_TTL / 2


def test_anilist_variants_strip_noise_for_fuzzy_match():
    variants = routes._anilist_variants("Naruto Shippuden the Movie 2 -Bonds-")
    assert "Naruto Shippuden Bonds" in variants
    assert routes._title_tokens("Naruto Shippuden the Movie: Bonds") >= {"naruto", "shippuden", "bonds"}
    assert routes._anilist_variants("Jujutsu Kaisen 0: The Movie")[-1] == "Jujutsu Kaisen"


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
    # Favorites and Watchlist are separate libraries; a library entry always
    # leaves the collection view before resolving, so clicking a favorite from
    # the same provider is not a silent no-op.
    assert "prefs.watchlist" in source and "data-ent-watch" in source and "toggleWatchlist" in source
    assert "showProvider(entry.provider)" in source and "showProvider(resume.provider)" in source
    # Cover art: AniList/iTunes metadata is proxied and rendered on cards and
    # library rows, with the initials placeholder as the fallback.
    assert "enrichCovers" in source and "enrichLibrary" in source and "/metadata" in source
    assert "ent-card-cover" in style and "ent-collection-thumb" in style
    # Recently Watched resumes from history with progress; it is the sidebar tab
    # for continuing a show without searching again.
    assert "recentItem" in source and "historyKey" in source and "ent-recent-bar" in style
    assert 'data-ent-dest="recent"' in index and "Recently Watched" in source
    # The player bar can re-resolve a stalled stream, and fatal HLS network
    # errors self-heal once instead of retrying a dead token forever.
    assert "entertainment-refresh" in index and "refreshPlayback" in source
    assert "ErrorTypes.NETWORK_ERROR" in source and "lastAutoRefresh" in source
    # Autoplay moves playback to the preloaded element, which must still get
    # controls; refresh shows visible progress feedback in the player.
    assert "video.controls = isActive" in source
    assert "entertainment-player-status" in index and "ent-player-status" in style
    assert "⟳ Refreshing…" in source
    assert "ent-card-tools" in source and "ent-card-tools" in style
    # Open Pandaflix plays the operator-supplied Hollywood sting.
    assert "entertainment-welcome-hollywood" in index and "welcome-to-hollywood.mp3" in index
    assert "entertainment-welcome-hollywood" in source
    assert (ROOT / "static/entertainment/welcome-to-hollywood.mp3").is_file()
    # Starting an anime episode from the browser plays the 2-second sting.
    assert "entertainment-anime-play" in index and "anime-play.mp3" in index
    assert "entertainment-anime-play" in source
    assert (ROOT / "static/entertainment/anime-play.mp3").is_file()
