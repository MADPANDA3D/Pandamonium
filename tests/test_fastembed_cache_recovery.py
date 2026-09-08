import sys
import types

import pytest

from routes import embedding_routes
from src import embeddings


def _install_fastembed(monkeypatch, *, failures):
    module = types.ModuleType("fastembed")

    class TextEmbedding:
        calls = 0

        @staticmethod
        def list_supported_models():
            return [{"model": "test-model", "sources": {"hf": "org/test-model"}}]

        def __init__(self, **_kwargs):
            type(self).calls += 1
            if type(self).calls <= failures:
                raise RuntimeError("model snapshot is incomplete")

        def embed(self, texts):
            return ([float(len(text)), 1.0] for text in texts)

    module.TextEmbedding = TextEmbedding
    monkeypatch.setitem(sys.modules, "fastembed", module)
    return TextEmbedding


def _broken_cache(tmp_path):
    cache = tmp_path / "cache"
    model = cache / "models--org--test-model"
    (model / "blobs").mkdir(parents=True)
    (model / "blobs" / "digest").write_bytes(b"preserved model bytes")
    (model / "snapshots" / "revision").mkdir(parents=True)
    (model / "refs").mkdir()
    (model / "refs" / "main").write_text("revision", encoding="utf-8")
    return cache, model


def test_failed_fastembed_init_quarantines_only_broken_metadata_and_retries_once(tmp_path, monkeypatch):
    cache, model = _broken_cache(tmp_path)
    text_embedding = _install_fastembed(monkeypatch, failures=1)
    monkeypatch.setattr(embeddings, "FASTEMBED_CACHE_DIR", str(cache))

    client = embeddings.FastEmbedClient(model="test-model")
    vector = client.encode(["hello"], normalize_embeddings=False)

    assert text_embedding.calls == 2
    assert vector.tolist() == [[5.0, 1.0]]
    assert (model / "blobs" / "digest").read_bytes() == b"preserved model bytes"
    parked = list((cache / ".recovery-quarantine").glob("models--org--test-model-*"))
    assert len(parked) == 1
    assert parked[0].joinpath("snapshots", "revision").is_dir()
    assert parked[0].joinpath("refs", "main").read_text(encoding="utf-8") == "revision"


def test_fastembed_recovery_never_retries_or_moves_a_healthy_snapshot(tmp_path, monkeypatch):
    cache, model = _broken_cache(tmp_path)
    snapshot = model / "snapshots" / "revision" / "model.onnx"
    snapshot.write_bytes(b"healthy")
    text_embedding = _install_fastembed(monkeypatch, failures=2)
    monkeypatch.setattr(embeddings, "FASTEMBED_CACHE_DIR", str(cache))

    with pytest.raises(RuntimeError, match="incomplete"):
        embeddings.FastEmbedClient(model="test-model")

    assert text_embedding.calls == 1
    assert snapshot.read_bytes() == b"healthy"
    assert not (cache / ".recovery-quarantine").exists()


def test_fastembed_recovery_attempt_is_bounded_when_retry_fails(tmp_path, monkeypatch):
    cache, _model = _broken_cache(tmp_path)
    text_embedding = _install_fastembed(monkeypatch, failures=2)
    monkeypatch.setattr(embeddings, "FASTEMBED_CACHE_DIR", str(cache))

    with pytest.raises(RuntimeError, match="incomplete"):
        embeddings.FastEmbedClient(model="test-model")

    assert text_embedding.calls == 2


def test_fastembed_recovery_refuses_model_root_symlink_escape(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    outside = tmp_path / "outside"
    cache.mkdir()
    (outside / "blobs").mkdir(parents=True)
    (outside / "blobs" / "keep").write_bytes(b"outside")
    (outside / "snapshots").mkdir()
    try:
        (cache / "models--org--test-model").symlink_to(outside, target_is_directory=True)
    except (NotImplementedError, OSError) as error:
        pytest.skip(f"symlinks unavailable: {error}")
    text_embedding = _install_fastembed(monkeypatch, failures=2)
    monkeypatch.setattr(embeddings, "FASTEMBED_CACHE_DIR", str(cache))

    with pytest.raises(RuntimeError, match="incomplete"):
        embeddings.FastEmbedClient(model="test-model")

    assert text_embedding.calls == 1
    assert (outside / "blobs" / "keep").read_bytes() == b"outside"


def test_embedding_download_status_requires_a_resolvable_onnx_snapshot(tmp_path, monkeypatch):
    cache, model = _broken_cache(tmp_path)
    monkeypatch.setattr(embedding_routes, "_cache_dir", lambda: str(cache))

    assert embedding_routes._is_downloaded("org/test-model") is False

    model_file = model / "snapshots" / "revision" / "model.onnx"
    try:
        model_file.symlink_to("../../blobs/digest")
    except (NotImplementedError, OSError) as error:
        pytest.skip(f"symlinks unavailable: {error}")
    assert embedding_routes._is_downloaded("org/test-model") is True
