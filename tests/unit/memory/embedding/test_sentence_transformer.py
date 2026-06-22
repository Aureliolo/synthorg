"""Tests for the shared sentence-transformers embedder adapter."""

import sys
import types

import numpy as np
import numpy.typing as npt
import pytest

from synthorg.memory.embedding.sentence_transformer import (
    SentenceTransformerEmbedder,
)
from synthorg.memory.errors import MemoryEmbedderUnavailableError

pytestmark = pytest.mark.unit


def _fake_sentence_transformers() -> types.ModuleType:
    module = types.ModuleType("sentence_transformers")

    class _FakeModel:
        def __init__(self, name: str) -> None:
            self._name = name

        def encode(
            self, text: str, *, normalize_embeddings: bool = True
        ) -> npt.NDArray[np.float64]:
            _ = text, normalize_embeddings
            return np.array([0.1, 0.2, 0.3], dtype=np.float64)

    module.SentenceTransformer = _FakeModel  # type: ignore[attr-defined]
    return module


class TestSentenceTransformerEmbedder:
    def test_raises_when_extra_absent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setitem(sys.modules, "sentence_transformers", None)
        with pytest.raises(MemoryEmbedderUnavailableError):
            SentenceTransformerEmbedder()

    def test_embeds_when_extra_present(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setitem(
            sys.modules, "sentence_transformers", _fake_sentence_transformers()
        )
        embedder = SentenceTransformerEmbedder(model_name="fake-model")
        assert embedder.embed("anything") == (0.1, 0.2, 0.3)
