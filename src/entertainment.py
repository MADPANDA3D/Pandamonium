"""Strict native UI contracts for the two supported upstream media CLIs."""

PROVIDERS = {
    "ani-cli": {
        "label": "Anime",
        "source": "https://github.com/pystardust/ani-cli.git",
        "tools": {
            "status": ("ani_cli__status", "entertainment.ani.status"),
            "search": ("ani_cli__search", "entertainment.ani.search"),
            "episodes": ("ani_cli__episodes", "entertainment.ani.episodes"),
            "resolve": ("ani_cli__resolve", "entertainment.ani.resolve"),
            "history": ("ani_cli__history", "entertainment.ani.history"),
            "continue": ("ani_cli__continue", "entertainment.ani.continue"),
        },
    },
    "pandaflix": {
        "label": "Movies & Shows",
        "source": "https://github.com/MADPANDA3D/pandaflix.git",
        "tools": {
            "status": ("pandaflix__status", "entertainment.pandaflix.status"),
            "search": ("pandaflix__search", "entertainment.pandaflix.search"),
            "seasons": ("pandaflix__seasons", "entertainment.pandaflix.seasons"),
            "episodes": ("pandaflix__episodes", "entertainment.pandaflix.episodes"),
            "resolve": ("pandaflix__resolve", "entertainment.pandaflix.resolve"),
        },
    },
}


def is_provider(manifest: dict) -> bool:
    provider = PROVIDERS.get(manifest.get("extension_id"))
    return bool(provider and manifest.get("source", {}).get("url") == provider["source"])


def operation(manifest: dict, binding: str) -> tuple[str, str] | None:
    if not is_provider(manifest):
        return None
    return next((value for value in PROVIDERS[manifest["extension_id"]]["tools"].values() if value[1] == binding), None)


def _object(properties, required):
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


def _string(maximum=300, **extra):
    return {"type": "string", "maxLength": maximum, **extra}


def _array(items, maximum=100):
    return {"type": "array", "maxItems": maximum, "items": items}


def _playback():
    headers = _object({name: _string(4096) for name in ("Referer", "User-Agent", "Origin", "Cookie")}, [])
    subtitle = _object({"url": _string(8192), "label": _string(80), "language": _string(16)}, ["url", "label", "language"])
    return _object({"title": _string(), "url": _string(8192), "headers": headers,
                    "subtitles": _array(subtitle, 16)}, ["title", "url", "headers", "subtitles"])


def schemas(binding: str):
    empty = _object({}, [])
    status = _object({"version": _string(80)}, ["version"])
    if binding.endswith(".status"):
        return empty, status
    if binding == "entertainment.ani.search":
        params = _object({"query": _string(200, minLength=1), "dub": {"type": "boolean"}}, ["query", "dub"])
        item = _object({"id": {"type": "integer", "minimum": 1, "maximum": 50}, "title": _string()}, ["id", "title"])
        return params, _object({"items": _array(item, 50)}, ["items"])
    ani_selection = {"query": _string(200, minLength=1), "selection_index": {"type": "integer", "minimum": 1, "maximum": 50}, "dub": {"type": "boolean"}}
    if binding == "entertainment.ani.episodes":
        item = _object({"number": _string(16), "label": _string(80)}, ["number", "label"])
        params = {**ani_selection, "offset": {"type": "integer", "minimum": 0, "maximum": 5000}}
        return _object(params, list(params)), _object({"items": _array(item), "total": {"type": "integer", "minimum": 0, "maximum": 5000}, "next_offset": {"type": "integer", "minimum": -1, "maximum": 5000}}, ["items", "total", "next_offset"])
    if binding == "entertainment.ani.resolve":
        params = {**ani_selection, "episode": _string(16), "quality": {"type": "string", "enum": ["best", "1080p", "720p", "480p", "360p"]}}
        return _object(params, list(params)), _playback()
    if binding == "entertainment.ani.history":
        item = _object({"index": {"type": "integer", "minimum": 1, "maximum": 100}, "episode": _string(16), "title": _string()}, ["index", "episode", "title"])
        return empty, _object({"items": _array(item)}, ["items"])
    if binding == "entertainment.ani.continue":
        params = {"history_index": {"type": "integer", "minimum": 1, "maximum": 100}, "dub": {"type": "boolean"}, "quality": {"type": "string", "enum": ["best", "1080p", "720p", "480p", "360p"]}}
        return _object(params, list(params)), _playback()
    if binding == "entertainment.pandaflix.search":
        params = _object({"query": _string(200, minLength=1)}, ["query"])
        item = _object({"selection": _string(400), "kind": {"type": "string", "enum": ["movie", "series"]}, "title": _string()}, ["selection", "kind", "title"])
        return params, _object({"items": _array(item, 50)}, ["items"])
    selection = {"query": _string(200, minLength=1), "selection": _string(400, minLength=1)}
    if binding == "entertainment.pandaflix.seasons":
        item = _object({"number": {"type": "integer", "minimum": 1, "maximum": 100}, "label": _string()}, ["number", "label"])
        return _object(selection, list(selection)), _object({"items": _array(item)}, ["items"])
    if binding == "entertainment.pandaflix.episodes":
        params = {**selection, "season": {"type": "integer", "minimum": 1, "maximum": 100}, "offset": {"type": "integer", "minimum": 0, "maximum": 5000}}
        item = _object({"number": {"type": "integer", "minimum": 1, "maximum": 1000}, "label": _string()}, ["number", "label"])
        return _object(params, list(params)), _object({"items": _array(item), "total": {"type": "integer", "minimum": 0, "maximum": 5000}, "next_offset": {"type": "integer", "minimum": -1, "maximum": 5000}}, ["items", "total", "next_offset"])
    if binding == "entertainment.pandaflix.resolve":
        params = {**selection, "kind": {"type": "string", "enum": ["movie", "series"]},
                  "season": {"type": "integer", "minimum": 0, "maximum": 100},
                  "episode": {"type": "integer", "minimum": 0, "maximum": 1000}}
        return _object(params, list(params)), _playback()
    raise ValueError("Unknown Entertainment binding")
