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


def _embedder(dims: int = 2) -> ProviderTextEmbedder:
    return ProviderTextEmbedder(
        EmbedderConfig(
            provider="test-provider",
            model="example-medium-001",
            dims=dims,
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
