"""Tests for the built-in feature-hashing embedder.

It serves two ports from one implementation (the synchronous ``embed`` the
meeting detectors call and the asynchronous ``embed_many`` the memory
substrate calls), so both are exercised here, along with the width
guarantee the vector column depends on.
"""

import pytest

from synthorg.core.vector_limits import HNSW_VECTOR_MAX_DIMENSIONS
from synthorg.memory.embedding.hashing import (
    BUILTIN_EMBEDDER_DIMS,
    BUILTIN_EMBEDDER_REF,
    HashingTextEmbedder,
)

pytestmark = pytest.mark.unit


class TestVectors:
    def test_deterministic(self) -> None:
        embedder = HashingTextEmbedder()
        assert embedder.embed("ship the feature now") == embedder.embed(
            "ship the feature now"
        )

    def test_l2_normalised(self) -> None:
        vector = HashingTextEmbedder().embed("approve the budget increase")
        magnitude = sum(component * component for component in vector) ** 0.5
        assert magnitude == pytest.approx(1.0)

    def test_empty_text_is_a_zero_vector(self) -> None:
        vector = HashingTextEmbedder().embed("")
        assert all(component == 0.0 for component in vector)

    def test_shared_vocabulary_scores_higher_than_disjoint(self) -> None:
        """The one quality claim the built-in actually makes: it matches
        shared terms, not shared meaning."""
        embedder = HashingTextEmbedder()

        def _cosine(left: str, right: str) -> float:
            a, b = embedder.embed(left), embedder.embed(right)
            return sum(x * y for x, y in zip(a, b, strict=True))

        overlapping = _cosine("deploy the release", "deploy the release now")
        disjoint = _cosine("deploy the release", "hire a designer")
        assert overlapping > disjoint

    def test_rejects_a_width_below_one(self) -> None:
        with pytest.raises(ValueError, match="dims must be"):
            HashingTextEmbedder(dims=0)

    @pytest.mark.parametrize("dims", [32, 256, 1024])
    def test_sign_is_independent_of_bucket(self, dims: int) -> None:
        """Colliding tokens must be able to cancel.

        Signed feature hashing buys one thing over unsigned: two distinct
        tokens landing in the same bucket cancel in expectation instead of
        reinforcing, so a collision costs precision rather than inventing
        similarity. That holds only while the sign is independent of the
        bucket. Drawing both from one value made the sign a pure function
        of the bucket at every power-of-two width, which is every width
        used here, so the property silently did not hold.
        """
        embedder = HashingTextEmbedder(dims=dims)
        signs_per_bucket: dict[int, set[float]] = {}
        for index in range(20_000):
            vector = embedder.embed(f"token{index}")
            bucket = next(i for i, value in enumerate(vector) if value != 0.0)
            signs_per_bucket.setdefault(bucket, set()).add(
                1.0 if vector[bucket] > 0.0 else -1.0
            )
        both_signs = [b for b, signs in signs_per_bucket.items() if len(signs) == 2]
        assert both_signs, (
            f"every one of {len(signs_per_bucket)} populated buckets at "
            f"dims={dims} produced a single sign; collisions can only "
            "reinforce, never cancel"
        )


class TestPort:
    def test_default_width_is_indexable(self) -> None:
        """The built-in must never be the reason dense search degrades to an
        exact scan, so its width stays under the full-precision ceiling."""
        assert BUILTIN_EMBEDDER_DIMS <= HNSW_VECTOR_MAX_DIMENSIONS

    def test_dimensions_matches_the_vectors_produced(self) -> None:
        embedder = HashingTextEmbedder(dims=64)
        assert embedder.dimensions == 64
        assert len(embedder.embed("hello world")) == 64

    def test_model_ref_names_the_builtin(self) -> None:
        assert HashingTextEmbedder().model_ref == BUILTIN_EMBEDDER_REF

    async def test_embed_many_preserves_order(self) -> None:
        embedder = HashingTextEmbedder(dims=32)
        texts = ("first text", "second text", "third text")
        vectors = await embedder.embed_many(texts)
        assert len(vectors) == len(texts)
        assert all(len(vector) == 32 for vector in vectors)
        assert vectors[1] == embedder.embed("second text")

    async def test_embed_many_of_nothing_is_empty(self) -> None:
        assert await HashingTextEmbedder().embed_many(()) == ()
