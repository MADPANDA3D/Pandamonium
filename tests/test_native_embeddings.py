"""A laptop profile never downloads a local embedding model implicitly."""

from unittest.mock import patch

import pytest

from src.embeddings import FastEmbedClient


def test_disabled_local_embeddings_stop_before_import_or_cache_creation(monkeypatch):
    monkeypatch.setenv("PANDAMONIUM_LOCAL_EMBEDDINGS", "false")
    with patch("src.embeddings.os.makedirs") as mkdir:
        with pytest.raises(RuntimeError, match="Local embeddings are disabled"):
            FastEmbedClient()
        mkdir.assert_not_called()
