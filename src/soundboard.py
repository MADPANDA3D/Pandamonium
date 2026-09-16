"""Myinstants lookup and owner soundboard state; never runs inference or plays audio."""

from __future__ import annotations

import json
import re
from html import unescape
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx
from jsonschema import Draft202012Validator

EXTENSION_ID = "myinstants-api"
ID_PATTERN = r"^[A-Za-z0-9_-]{1,160}$"
MAX_MEDIA_BYTES = 8 * 1024 * 1024
MAX_RECORDS = 300
MAX_FAVORITES = 100
# Match the reviewed upstream PHP client's request header for media as well as search.
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
READ_BINDINGS = frozenset({"soundboard.search", "soundboard.detail", "soundboard.recent", "soundboard.favorites"})


def schemas(binding: str) -> tuple[dict, dict]:
    """Fixed native schemas prevent a package from relabeling arbitrary code read-only."""
    if binding not in READ_BINDINGS:
        raise ValueError("Unknown soundboard operation")
    properties: dict = {}
    required: list[str] = []
    if binding in {"soundboard.search", "soundboard.favorites"}:
        properties["query"] = {"type": "string", "maxLength": 100}
        if binding == "soundboard.search":
            properties["query"]["minLength"] = 1
            required = ["query"]
    if binding == "soundboard.detail":
        properties["id"] = {"type": "string", "minLength": 1, "maxLength": 160}
        required = ["id"]
    parameters = {"type": "object", "properties": properties, "required": required, "additionalProperties": False}
    sound = {
        "type": "object", "properties": {
            "id": {"type": "string", "minLength": 1, "maxLength": 160},
            "title": {"type": "string", "minLength": 1, "maxLength": 200},
            "url": {"type": "string", "maxLength": 512},
            "mp3": {"type": "string", "maxLength": 1024},
            "cue": {"type": "string", "maxLength": 180},
        }, "required": ["id", "title", "url", "mp3", "cue"], "additionalProperties": False,
    }
    return parameters, {"type": "object", "properties": {
        "sounds": {"type": "array", "maxItems": MAX_FAVORITES, "items": sound},
    }, "required": ["sounds"], "additionalProperties": False}


def media_url(value: str) -> str:
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or parsed.hostname != "www.myinstants.com"
            or parsed.port not in {None, 443} or parsed.username or parsed.password
            or not parsed.path.startswith("/media/sounds/") or parsed.query or parsed.fragment
            or not re.fullmatch(r"/media/sounds/[A-Za-z0-9_.%~-]+\.(?:mp3|wav|ogg)", parsed.path)
            or "%2f" in parsed.path.lower() or "%5c" in parsed.path.lower()):
        raise ValueError("Sound provider returned an unsupported audio reference")
    return value


def sound_record(raw: dict) -> dict:
    sound_id = str(raw.get("id", ""))
    title = unescape(str(raw.get("title", ""))).strip()[:200]
    if not re.fullmatch(ID_PATTERN, sound_id) or not title:
        raise ValueError("Sound provider returned an invalid sound")
    return {"id": sound_id, "title": title,
            "url": f"https://www.myinstants.com/en/instant/{sound_id}/",
            "mp3": media_url(str(raw.get("mp3", ""))), "cue": f"[[sound:{sound_id}]]"}


def state_path(runtime: Path) -> Path:
    # Outside package mounts, and independent of package digest: upgrade retains favorites.
    return runtime.parent / ".soundboard.json"


def read_state(runtime: Path) -> dict:
    path = state_path(runtime)
    if not path.exists():
        return {"sounds": {}, "favorites": [], "volume": 0.6, "muted": False}
    if path.stat().st_size > 1024 * 1024:
        raise ValueError("Soundboard state is too large")
    data = json.loads(path.read_text())
    if not isinstance(data, dict) or not isinstance(data.get("sounds"), dict) or not isinstance(data.get("favorites"), list):
        raise TypeError("Soundboard state is invalid; saved data was preserved")
    return data


def save_state(runtime: Path, data: dict) -> None:
    from core.atomic_io import atomic_write_json

    atomic_write_json(str(state_path(runtime)), data)


def active_state(owner: str | None) -> dict | None:
    """Read only this owner's enabled native soundboard; shared by text and voice."""
    from src.authority_protocol import operator_identity
    from src.extension_cli_adapter import _LOCK, GeneratedCliAdapter
    from src.extension_installer import ExtensionLifecycleError
    from src.extension_registry import ExtensionRegistry

    try:
        with _LOCK:
            record = ExtensionRegistry().snapshot()["extensions"].get(EXTENSION_ID)
            identity = operator_identity(owner)
            if not record or not identity:
                return None
            _, runtime, _, contract, _ = GeneratedCliAdapter()._validated_context(record, identity)
            if not all(item["binding"] in READ_BINDINGS for item in contract["interfaces"].values()):
                return None
            return read_state(runtime)
    except (ExtensionLifecycleError, ValueError, TypeError, KeyError, OSError):
        return None  # Unavailable effects never discard speech or chat.


def message_metadata(role: str, content, metadata: dict | None, *, owner: str | None,
                     session_id: str, message_id: str) -> dict:
    """Stamp resolved cues at persistence, never trusting caller-supplied cue records."""
    result = dict(metadata or {})
    result.pop("sound_cues", None)
    if role != "assistant" or not isinstance(content, str) or "[[sound:" not in content:
        return result
    state = active_state(owner)
    if state is None:
        return result
    sounds = state["sounds"]
    cues = []
    for match in re.finditer(r"```[\s\S]*?```|~~~[\s\S]*?~~~|`[^`]*`|\[\[sound:([A-Za-z0-9_-]{1,160})\]\]", content):
        sound_id = match.group(1)
        if sound_id not in sounds:
            continue
        # Match browser string offsets, including non-BMP characters before a cue.
        offset = len(content[:match.start()].encode("utf-16-le")) // 2
        cues.append({"cue_id": f"{message_id}:{offset}", "message_id": message_id,
                     "session_id": session_id, "text_offset_utf16": offset,
                     "sound_id": sound_id, "title": sounds[sound_id]["title"]})
        if len(cues) == 100:
            break
    if cues:
        result["sound_cues"] = cues
    return result


def execute(binding: str, arguments: dict, runtime: Path, config: dict, *, validation: bool = False) -> dict:
    Draft202012Validator(schemas(binding)[0]).validate(arguments)
    if binding == "soundboard.detail" and not re.fullmatch(ID_PATTERN, arguments["id"]):
        raise ValueError("Invalid sound identity")
    state = read_state(runtime)
    if binding == "soundboard.favorites":
        query = arguments.get("query", "").casefold()
        return {"sounds": [sound_record(state["sounds"][key]) for key in state["favorites"]
                           if key in state["sounds"] and query in (key + " " + state["sounds"][key]["title"]).casefold()]}
    if binding == "soundboard.detail" and arguments["id"] in state["sounds"] and not validation:
        return {"sounds": [sound_record(state["sounds"][arguments["id"]])]}
    port = int(config["PANDAMONIUM_PORT"])
    if not 1 <= port <= 65535:
        raise ValueError("Soundboard service is unavailable")
    operation = binding.split(".")[1]
    params = {"q": arguments["query"]} if operation == "search" else ({"id": arguments["id"]} if operation == "detail" else {})
    with (httpx.Client(timeout=20, follow_redirects=False, trust_env=False) as client,
          client.stream("GET", f"http://127.0.0.1:{port}/{operation}", params=params) as response):
        response.raise_for_status()
        if response.is_redirect:
            raise ValueError("Unexpected soundboard service redirect")
        body = bytearray()
        for chunk in response.iter_bytes():
            body.extend(chunk)
            if len(body) > 512 * 1024:
                raise ValueError("Sound provider response is too large")
    payload = json.loads(body)["data"]
    rows = [payload] if operation == "detail" else payload
    if not isinstance(rows, list):
        raise TypeError("Sound provider returned an invalid response")
    sounds = [sound_record(row) for row in rows[:20]]
    if operation == "detail" and (not sounds or sounds[0]["id"] != arguments["id"]):
        raise ValueError("Sound identity changed")
    if not validation:
        for sound in sounds:
            state["sounds"].pop(sound["id"], None)
            state["sounds"][sound["id"]] = sound
        for key in list(state["sounds"]):
            if len(state["sounds"]) <= MAX_RECORDS:
                break
            if key not in state["favorites"]:
                del state["sounds"][key]
        save_state(runtime, state)
    return {"sounds": sounds}


def favorite(runtime: Path, sound_id: str, enabled: bool) -> dict:
    state = read_state(runtime)
    if sound_id not in state["sounds"]:
        raise ValueError("Search or resolve this sound before saving it")
    if enabled and sound_id not in state["favorites"]:
        if len(state["favorites"]) >= MAX_FAVORITES:
            raise ValueError("Your soundboard can hold up to 100 favorites")
        state["favorites"].append(sound_id)
    if not enabled and sound_id in state["favorites"]:
        state["favorites"].remove(sound_id)
    save_state(runtime, state)
    return state


def audio(runtime: Path, sound_id: str) -> tuple[bytes, str]:
    record = read_state(runtime)["sounds"].get(sound_id)
    if not record:
        raise ValueError("Sound has not been resolved by this plugin")
    url = media_url(record["mp3"])
    with httpx.Client(timeout=15, follow_redirects=False, trust_env=False,
                      headers={"User-Agent": USER_AGENT}) as client:
        for _ in range(4):
            with client.stream("GET", url) as response:
                if response.is_redirect:
                    url = media_url(urljoin(url, response.headers["location"]))
                    continue
                response.raise_for_status()
                content_type = response.headers.get("content-type", "").split(";")[0].lower()
                if content_type not in {"audio/mpeg", "audio/mp3", "audio/wav", "audio/x-wav", "audio/ogg"}:
                    raise ValueError("Provider did not return supported audio")
                result = bytearray()
                for chunk in response.iter_bytes():
                    result.extend(chunk)
                    if len(result) > MAX_MEDIA_BYTES:
                        raise ValueError("Sound exceeds the 8 MB playback limit")
                if not result:
                    raise ValueError("Provider returned empty audio")
                return bytes(result), content_type
    raise ValueError("Too many audio redirects")


def spoken_cues(raw: str, spoken: str, *, owner: str | None, session_id: str,
                turn_id: str, base_offset: int = 0) -> list[dict]:
    """Resolve marker positions against the exact cleaned speech, including repeats."""
    from src.voice_pcm import speech_text

    validated = message_metadata("assistant", raw, {}, owner=owner,
                                 session_id=session_id, message_id=turn_id)
    cues = []
    for cue in validated.get("sound_cues", []):
        prefix = raw.encode("utf-16-le")[:cue["text_offset_utf16"] * 2].decode("utf-16-le")
        clean = " ".join(speech_text(prefix).split())
        words = list(re.finditer(r"\w+(?:['’]\w+)*", clean))
        raw_words = re.findall(r"\w+(?:['’]\w+)*", prefix)
        if (not words or not raw_words or raw_words[-1].casefold() != words[-1][0].casefold()
                or not spoken.startswith(clean)):
            continue  # The selected word was omitted from the actual spoken payload.
        char_end = base_offset + words[-1].end()
        cues.append({**cue, "cue_id": f"{turn_id}:{char_end}:{cue['sound_id']}", "char_end": char_end})
    return cues


def timed_cues(audio: bytes, block: str, cues: list[dict], *, block_offset: int,
               sample_rate: int, samples: int) -> list[dict]:
    """Read bounded Chatterbox RIFF metadata; malformed/missing timing fails effects only."""
    import struct

    selected = [cue for cue in cues if block_offset < cue["char_end"] <= block_offset + len(block)]
    if not selected or audio[:4] != b"RIFF" or audio[8:12] != b"WAVE":
        return []
    try:
        end = struct.unpack_from("<I", audio, 4)[0] + 8
        if end != len(audio):
            return []
        position = 12
        metadata = None
        while position + 8 <= end:
            size = struct.unpack_from("<I", audio, position + 4)[0]
            following = position + 8 + size
            if following > end:
                return []
            if audio[position:position + 4] == b"cbtm":
                if size > 65536 or metadata is not None:
                    return []
                metadata = json.loads(audio[position + 8:following])
            position = following + size % 2
        if (not isinstance(metadata, dict) or metadata.get("version") != 1
                or metadata.get("sample_rate") != sample_rate
                or not isinstance(metadata.get("text"), str)
                or not isinstance(metadata.get("words"), list) or len(metadata["words"]) > 1000):
            return []
        pattern = r"\w+(?:['’]\w+)*"
        original = list(re.finditer(pattern, block))
        normalized = list(re.finditer(pattern, metadata["text"]))
        def normalize(word):
            return word[0].replace("’", "'").casefold()
        if list(map(normalize, original)) != list(map(normalize, normalized)):
            return []  # Never attach a cue to substituted/truncated provider text.
        ends = {}
        previous = 0
        for row in metadata["words"]:
            if (not isinstance(row, list) or len(row) != 3 or any(type(n) is not int for n in row)
                    or not 0 <= row[0] < row[1] <= len(metadata["text"])
                    or not previous <= row[2] <= samples or row[2] == 0):
                return []
            ends[(row[0], row[1])] = row[2]
            previous = row[2]
        result = []
        for cue in selected:
            for source, target in zip(original, normalized, strict=True):
                if source.end() != cue["char_end"] - block_offset:
                    continue
                offset = ends.get((target.start(), target.end()))
                if offset is not None:
                    result.append({"cue_id": cue["cue_id"], "sound_id": cue["sound_id"],
                                   "title": cue["title"], "end_sample": offset})
                break
        return result
    except (ValueError, TypeError, KeyError, UnicodeError, struct.error):
        return []
