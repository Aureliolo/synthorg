"""Tests for the conflict-scoring text embedder."""

import pytest

from synthorg.communication.meeting.embedder import (
    _CONFLICT_HASH_DIMS,
    build_text_embedder,
    cosine_similarity,
)
from synthorg.memory.embedding.hashing import HashingTextEmbedder

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


class TestBuildTextEmbedder:
    """The conflict detectors score positions with the built-in embedder.

    It is lexical, not semantic, and there is nothing to select: the
    org's one embedding binding is ``memory.embedder_model``, which
    dispatches to a provider and loads no local model.
    """

    def test_builds_the_builtin_embedder(self) -> None:
        assert isinstance(build_text_embedder(), HashingTextEmbedder)

    def test_scores_at_the_conflict_width(self) -> None:
        vector = build_text_embedder().embed("ship it")

        assert len(vector) == _CONFLICT_HASH_DIMS
