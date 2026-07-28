"""Tests for ``ProviderTextEmbedder._extract`` response handling.

The provider (and LiteLLM's cache-merge path) can return embedding items
out of input order or duplicated; ``_extract`` must bind every vector to
its declared ``index`` so a memory is never stored against another's
vector.
"""

from types import SimpleNamespace

import pytest

from synthorg.memory.embedding.config import EmbedderConfig
from synthorg.memory.embedding.text_embedder import ProviderTextEmbedder
from synthorg.memory.errors import MemoryEmbeddingError

pytestmark = pytest.mark.unit


def _embedder(dims: int = 2, *, dims_explicit: bool = False) -> ProviderTextEmbedder:
    return ProviderTextEmbedder(
        EmbedderConfig(
            provider="test-provider",
            model="example-medium-001",
            dims=dims,
            dims_explicit=dims_explicit,
        )
    )


def _response(items: list[dict[str, object]]) -> object:
    return SimpleNamespace(data=items)


class TestExtractOrdering:
    def test_reorders_by_declared_index(self) -> None:
        embedder = _embedder()
        response = _response(
            [
                {"index": 1, "embedding": [3.0, 4.0]},
                {"index": 0, "embedding": [1.0, 2.0]},
            ]
        )
        assert embedder._extract(response, expected=2) == ((1.0, 2.0), (3.0, 4.0))

    def test_duplicate_index_rejected(self) -> None:
        embedder = _embedder()
        response = _response(
            [
                {"index": 0, "embedding": [1.0, 2.0]},
                {"index": 0, "embedding": [3.0, 4.0]},
            ]
        )
        with pytest.raises(MemoryEmbeddingError, match="duplicate"):
            embedder._extract(response, expected=2)

    def test_missing_index_rejected(self) -> None:
        embedder = _embedder()
        response = _response([{"index": 0, "embedding": [1.0, 2.0]}])
        with pytest.raises(MemoryEmbeddingError, match="no vector for input"):
            embedder._extract(response, expected=2)

    def test_out_of_range_index_rejected(self) -> None:
        embedder = _embedder()
        response = _response([{"index": 5, "embedding": [1.0, 2.0]}])
        with pytest.raises(MemoryEmbeddingError, match="outside"):
            embedder._extract(response, expected=1)

    def test_missing_index_field_is_malformed(self) -> None:
        embedder = _embedder()
        response = _response([{"embedding": [1.0, 2.0]}])
        with pytest.raises(MemoryEmbeddingError, match="malformed"):
            embedder._extract(response, expected=1)

    def test_non_numeric_embedding_value_is_malformed(self) -> None:
        embedder = _embedder()
        response = _response([{"index": 0, "embedding": [None, 2.0]}])
        with pytest.raises(MemoryEmbeddingError, match="malformed"):
            embedder._extract(response, expected=1)

    def test_nan_embedding_value_is_malformed(self) -> None:
        embedder = _embedder()
        response = _response([{"index": 0, "embedding": [float("nan"), 2.0]}])
        with pytest.raises(MemoryEmbeddingError, match="malformed"):
            embedder._extract(response, expected=1)

    def test_infinite_embedding_value_is_malformed(self) -> None:
        embedder = _embedder()
        response = _response([{"index": 0, "embedding": [float("inf"), 2.0]}])
        with pytest.raises(MemoryEmbeddingError, match="malformed"):
            embedder._extract(response, expected=1)

    def test_oversized_embedding_value_is_malformed(self) -> None:
        # ``float(10**400)`` raises OverflowError rather than returning inf.
        embedder = _embedder()
        response = _response([{"index": 0, "embedding": [10**400, 2.0]}])
        with pytest.raises(MemoryEmbeddingError, match="malformed"):
            embedder._extract(response, expected=1)


class TestConfiguredWidth:
    """An operator-pinned width narrower than the model's output truncates."""

    def test_wider_vector_truncates_and_renormalises(self) -> None:
        embedder = _embedder(dims=2, dims_explicit=True)
        response = _response([{"index": 0, "embedding": [3.0, 4.0, 9.0, 9.0]}])

        (vector,) = embedder._extract(response, expected=1)

        # The kept head is renormalised so distances stay comparable with
        # every other vector this embedder produced.
        assert vector == pytest.approx((0.6, 0.8))

    def test_zero_head_is_kept_as_is(self) -> None:
        embedder = _embedder(dims=2, dims_explicit=True)
        response = _response([{"index": 0, "embedding": [0.0, 0.0, 1.0]}])

        (vector,) = embedder._extract(response, expected=1)

        assert vector == (0.0, 0.0)

    def test_wider_vector_without_an_explicit_width_is_rejected(self) -> None:
        # No operator asked to narrow this: the model simply disagrees with
        # the catalogued width, which would leave the index incomparable.
        embedder = _embedder(dims=2)
        response = _response([{"index": 0, "embedding": [1.0, 2.0, 3.0]}])

        with pytest.raises(MemoryEmbeddingError, match="incomparable"):
            embedder._extract(response, expected=1)

    def test_narrower_vector_is_always_rejected(self) -> None:
        embedder = _embedder(dims=4, dims_explicit=True)
        response = _response([{"index": 0, "embedding": [1.0, 2.0]}])

        with pytest.raises(MemoryEmbeddingError, match="incomparable"):
            embedder._extract(response, expected=1)
