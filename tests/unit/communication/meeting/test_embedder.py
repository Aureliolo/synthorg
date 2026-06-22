"""Tests for the text-embedding backends and selection factory."""

import sys
import types

import numpy as np
import numpy.typing as npt
import pytest

from synthorg.communication.meeting.embedder import (
    HashingTextEmbedder,
    build_text_embedder,
    cosine_similarity,
)
from synthorg.communication.meeting.errors import MeetingEmbedderUnavailableError
from synthorg.core.registry.errors import StrategyFactoryNotFoundError
from synthorg.memory.embedding.sentence_transformer import (
    SentenceTransformerEmbedder,
)

pytestmark = pytest.mark.unit


class TestHashingTextEmbedder:
    def test_deterministic(self) -> None:
        embedder = HashingTextEmbedder()
        assert embedder.embed("ship the feature now") == embedder.embed(
            "ship the feature now"
        )

    def test_l2_normalised(self) -> None:
        vector = HashingTextEmbedder().embed("approve the budget increase")
        magnitude = sum(component * component for component in vector) ** 0.5
        assert magnitude == pytest.approx(1.0)

    def test_empty_text_is_zero_vector(self) -> None:
        vector = HashingTextEmbedder().embed("")
        assert all(component == 0.0 for component in vector)

    def test_dimensionality(self) -> None:
        assert len(HashingTextEmbedder(dims=64).embed("hello world")) == 64


class TestCosineSimilarity:
    def test_identical_vectors(self) -> None:
        vector = HashingTextEmbedder().embed("same text here")
        assert cosine_similarity(vector, vector) == pytest.approx(1.0)

    def test_disjoint_text_is_low(self) -> None:
        embedder = HashingTextEmbedder()
        left = embedder.embed("alpha beta gamma delta epsilon")
        right = embedder.embed("one two three four five six seven")
        assert cosine_similarity(left, right) < 0.7

    def test_zero_vector_is_zero(self) -> None:
        assert cosine_similarity((0.0, 0.0), (1.0, 0.0)) == 0.0


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


class TestBuildTextEmbedder:
    def test_hashing_is_default_backend(self) -> None:
        assert isinstance(build_text_embedder("hashing"), HashingTextEmbedder)

    def test_sentence_transformer_selection(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setitem(
            sys.modules, "sentence_transformers", _fake_sentence_transformers()
        )
        assert isinstance(
            build_text_embedder("sentence_transformer"),
            SentenceTransformerEmbedder,
        )

    def test_sentence_transformer_unavailable_raises_meeting_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The factory translates a missing extra into the meeting error."""
        monkeypatch.setitem(sys.modules, "sentence_transformers", None)
        with pytest.raises(MeetingEmbedderUnavailableError):
            build_text_embedder("sentence_transformer")

    def test_unknown_strategy_raises(self) -> None:
        with pytest.raises(StrategyFactoryNotFoundError):
            build_text_embedder("bogus")
