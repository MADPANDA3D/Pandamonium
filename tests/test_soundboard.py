"""Soundboard ownership, lookup, media bounds and native permission regression."""

import copy
import json
from pathlib import Path

import httpx
import pytest

from src import soundboard as sb
from src.extension_cli_adapter import validate_cli_execution
from src.extension_installer import ExtensionLifecycleError

SOUND = {"id": "vine-boom-123", "title": "Vine boom", "mp3": "https://www.myinstants.com/media/sounds/vine-boom.mp3"}


def test_search_favorites_owner_upgrade_and_no_repeat_search(tmp_path, monkeypatch):
    requests = []
    def upstream(request):
        requests.append(request)
        return httpx.Response(200, json={"data": [SOUND]})
    client = httpx.Client
    monkeypatch.setattr(sb.httpx, "Client", lambda **kwargs: client(transport=httpx.MockTransport(upstream), **kwargs))
    runtime = tmp_path / "owner" / "myinstants-api" / "revision-one"
    result = sb.execute("soundboard.search", {"query": "vine boom"}, runtime, {"PANDAMONIUM_PORT": "23456"})
    assert result["sounds"][0]["cue"] == "[[sound:vine-boom-123]]"
    assert requests[0].url.params["q"] == "vine boom"
    sb.favorite(runtime, SOUND["id"], True)
    upgraded = runtime.with_name("revision-two")
    assert sb.execute("soundboard.favorites", {"query": "BOOM"}, upgraded, {}) == result
    assert sb.execute("soundboard.detail", {"id": SOUND["id"]}, upgraded, {}) == result
    assert len(requests) == 1
    foreign = tmp_path / "other-owner" / "myinstants-api" / "revision-two"
    assert sb.execute("soundboard.favorites", {}, foreign, {}) == {"sounds": []}
    with pytest.raises(ValueError, match="before saving"):
        sb.favorite(foreign, SOUND["id"], True)
    sb.favorite(upgraded, SOUND["id"], False)
    assert sb.execute("soundboard.favorites", {}, runtime, {}) == {"sounds": []}


def test_validation_does_not_seed_favorites_or_history(tmp_path, monkeypatch):
    client = httpx.Client
    monkeypatch.setattr(sb.httpx, "Client", lambda **kwargs: client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={"data": [SOUND]})), **kwargs))
    runtime = tmp_path / "package" / "revision"
    assert sb.execute("soundboard.recent", {}, runtime, {"PANDAMONIUM_PORT": "23456"}, validation=True)["sounds"]
    assert not sb.state_path(runtime).exists()


@pytest.mark.parametrize("url", ["http://www.myinstants.com/media/sounds/x.mp3", "https://localhost/media/sounds/x.mp3",
    "https://www.myinstants.com@127.0.0.1/media/sounds/x.mp3", "https://www.myinstants.com:8080/media/sounds/x.mp3",
    "https://www.myinstants.com/media/sounds/%2fetc.mp3", "https://www.myinstants.com/media/sounds/x.mp3?redirect=evil",
    "https://www.myinstants.com/elsewhere/x.mp3"])
def test_media_rejects_untrusted_destinations(url):
    with pytest.raises(ValueError):
        sb.media_url(url)


def test_audio_requires_resolved_sound_and_rechecks_redirect_and_size(tmp_path, monkeypatch):
    runtime = tmp_path / "package" / "revision"
    with pytest.raises(ValueError, match="not been resolved"):
        sb.audio(runtime, "unknown")
    state = sb.read_state(runtime)
    state["sounds"][SOUND["id"]] = sb.sound_record(SOUND)
    sb.save_state(runtime, state)
    response = httpx.Response(302, headers={"location": "http://127.0.0.1/secrets"})
    client = httpx.Client
    monkeypatch.setattr(sb.httpx, "Client", lambda **kwargs: client(transport=httpx.MockTransport(lambda request: response), **kwargs))
    with pytest.raises(ValueError, match="unsupported"):
        sb.audio(runtime, SOUND["id"])
    response = httpx.Response(200, headers={"content-type": "text/html"}, content=b"<script>bad</script>")
    with pytest.raises(ValueError, match="supported audio"):
        sb.audio(runtime, SOUND["id"])
    monkeypatch.setattr(sb, "MAX_MEDIA_BYTES", 4)
    response = httpx.Response(200, headers={"content-type": "audio/mpeg"}, content=b"12345")
    with pytest.raises(ValueError, match="8 MB"):
        sb.audio(runtime, SOUND["id"])
    response = httpx.Response(200, headers={"content-type": "audio/mpeg"}, content=b"ID3")
    assert sb.audio(runtime, SOUND["id"]) == (b"ID3", "audio/mpeg")


def test_only_native_fixed_bindings_can_be_read_only():
    proposal = json.loads(Path("integrations/repository-packages/myinstants-api/proposal.json").read_text())
    validate_cli_execution(proposal["manifest"], proposal)
    tampered = copy.deepcopy(proposal)
    tampered["manifest"]["permissions"]["default"] = "read_only"
    with pytest.raises(ExtensionLifecycleError, match="effectful_authority"):
        validate_cli_execution(tampered["manifest"], tampered)
    tampered = copy.deepcopy(proposal)
    tampered["interfaces"][0]["binding"] = "GET /arbitrary-executable"
    with pytest.raises(ExtensionLifecycleError, match="effectful_authority"):
        validate_cli_execution(tampered["manifest"], tampered)
    tampered = copy.deepcopy(proposal)
    tampered["interfaces"][0]["tool_schema"]["function"]["parameters"]["properties"]["url"] = {"type": "string"}
    tampered["manifest"]["capabilities"]["schemas"][0] = tampered["interfaces"][0]["tool_schema"]
    with pytest.raises(ExtensionLifecycleError, match="soundboard_contract"):
        validate_cli_execution(tampered["manifest"], tampered)


def test_settings_routes_enforce_owner_lifecycle_and_preferences(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from routes import soundboard_routes as routes
    from src import extension_cli_adapter

    runtime = tmp_path / "owner" / "revision"
    record = {"manifest": {"extension_id": sb.EXTENSION_ID}, "enabled": True}
    (tmp_path / "lifecycle.json").write_text(json.dumps({"extensions": {
        sb.EXTENSION_ID: {"owner_scope": "alice", "active_revision": "revision"}}}))

    class Adapter:
        root = tmp_path

        def _runtime(self, *_args):
            return runtime

        def _validated_context(self, *_args):
            if not record["enabled"]:
                raise ExtensionLifecycleError("extension_disabled")
            return None, runtime, None, {"interfaces": {"search": {"binding": "soundboard.search"}}}, {}

    monkeypatch.setattr(routes, "GeneratedCliAdapter", Adapter)
    monkeypatch.setattr(extension_cli_adapter, "GeneratedCliAdapter", Adapter)
    monkeypatch.setattr(routes.ExtensionRegistry, "snapshot", lambda _: {"extensions": {sb.EXTENSION_ID: record}})
    app = FastAPI()
    app.include_router(routes.setup_soundboard_routes())
    owner = "alice"
    app.dependency_overrides[routes.require_user] = lambda: owner
    client = TestClient(app)
    assert client.get("/api/soundboard").json()["installed"]
    assert client.put("/api/soundboard/preferences", json={"volume": 2, "muted": False}).status_code == 422
    assert client.put("/api/soundboard/preferences", json={"volume": 0.3, "muted": True}).status_code == 200
    assert client.get("/api/soundboard").json()["volume"] == 0.3
    state = sb.read_state(runtime)
    state["sounds"][SOUND["id"]] = sb.sound_record(SOUND)
    sb.save_state(runtime, state)
    token = "[[sound:vine-boom-123]]"
    content = f"💥 boom {token} boom {token} `{token}` [[sound:unknown]]"
    stamped = sb.message_metadata("assistant", content, {"sound_cues": ["forged"]},
                                  owner="alice", session_id="s1", message_id="m1")
    cues = stamped["sound_cues"]
    assert len(cues) == 2 and len({cue["cue_id"] for cue in cues}) == 2
    assert cues[0]["text_offset_utf16"] == 8
    assert all(cue["session_id"] == "s1" and cue["message_id"] == "m1" for cue in cues)
    assert sb.message_metadata("user", content, stamped, owner="alice", session_id="s1", message_id="m2") == {}
    owner = "bob"
    assert client.get("/api/soundboard").json() == {"installed": False, "enabled": False}
    assert client.put("/api/soundboard/preferences", json={"volume": 1, "muted": False}).status_code == 404
    owner = "alice"
    record["enabled"] = False
    assert client.get("/api/soundboard").json()["muted"] is True
    assert client.get("/api/soundboard/sounds").status_code == 503
    sb.state_path(runtime).write_text("corrupt data")
    assert client.get("/api/soundboard").status_code == 503
    assert sb.state_path(runtime).read_text() == "corrupt data"


def test_voice_cues_keep_repeated_word_identity_and_validate_riff(monkeypatch):
    import io
    import struct
    import wave

    raw = 'First **boom**, then another **boom**[[sound:vine-boom-123]] and onward.'
    prefix = raw.split('[[sound:')[0]
    monkeypatch.setattr(sb, 'message_metadata', lambda *args, **kwargs: {'sound_cues': [{
        'cue_id': 'raw', 'text_offset_utf16': len(prefix), 'sound_id': 'vine-boom-123', 'title': 'Boom',
    }]})
    from src.voice_pcm import speech_text, wav_to_pcm16

    spoken = speech_text(raw)
    cues = sb.spoken_cues(raw, spoken, owner='owner', session_id='session', turn_id='turn')
    assert len(cues) == 1
    assert cues[0]['char_end'] == spoken.index('boom', spoken.index('boom') + 1) + 4
    assert sb.spoken_cues(raw, 'The result is in chat.', owner='owner', session_id='session', turn_id='turn') == []
    out = io.BytesIO()
    with wave.open(out, 'wb') as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(24000)
        writer.writeframes(b'\0\0' * 72000)
    body = out.getvalue()
    target = cues[0]['char_end']
    payload = json.dumps({'version': 1, 'text': spoken, 'sample_rate': 24000,
                          'words': [[target - 4, target, 48000]]}).encode()
    enriched = body + b'cbtm' + struct.pack('<I', len(payload)) + payload + b'\0' * (len(payload) % 2)
    enriched = enriched[:4] + struct.pack('<I', len(enriched) - 8) + enriched[8:]
    timed = sb.timed_cues(enriched, spoken, cues, block_offset=0, sample_rate=24000, samples=72000)
    assert timed == [{'cue_id': cues[0]['cue_id'], 'sound_id': 'vine-boom-123', 'title': 'Boom', 'end_sample': 48000}]
    assert wav_to_pcm16(enriched) == wav_to_pcm16(body)
    assert sb.timed_cues(enriched[:-1], spoken, cues, block_offset=0, sample_rate=24000, samples=72000) == []
    assert sb.timed_cues(enriched, spoken.replace('another', 'different'), cues, block_offset=0, sample_rate=24000, samples=72000) == []
    assert sb.timed_cues(body, spoken, cues, block_offset=0, sample_rate=24000, samples=72000) == []



def test_voice_mounts_only_the_current_owners_enabled_soundboard(monkeypatch):
    from routes.voice_routes import _engaged_extension_ids

    monkeypatch.setattr(sb, 'active_state', lambda owner: {} if owner == 'installed-owner' else None)
    assert _engaged_extension_ids({'owner': 'installed-owner'}) == {'myinstants-api'}
    assert _engaged_extension_ids({'owner': 'other-owner'}) == set()
    assert _engaged_extension_ids({'owner': 'other-owner', 'oracle_protocol_active': True}) == {'oracle'}


def test_streamed_marker_after_sentence_keeps_its_preceding_word(monkeypatch):
    from routes.voice_routes import _SpeechTurn

    monkeypatch.setattr(sb, "active_state", lambda owner: {"sounds": {"boom-123": {"title": "Boom"}}})
    turn = _SpeechTurn("session", "turn")
    turn.owner = "owner"
    assert turn.feed("First boom, then boom.")
    assert not turn.feed(" [[sound:boom-123]]")
    assert turn.sound_cues[0]["char_end"] == len("First boom, then boom")
    turn.feed(" More words.")
    assert len(turn.sound_cues) == 1
