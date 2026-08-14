"""Tests for retrieval component factories."""

from unittest.mock import AsyncMock

import pytest

from synthorg.memory.retrieval.factory import create_hierarchical_retriever
from synthorg.memory.retrieval.hierarchical.default_retriever import (
    DefaultHierarchicalRetriever,
)
from synthorg.memory.retrieval_config import MemoryRetrievalConfig


def _mock_backend() -> AsyncMock:
    backend = AsyncMock()
    backend.retrieve = AsyncMock(return_value=())
    backend.supports_sparse_search = False
    return backend


def _mock_provider() -> AsyncMock:
    return AsyncMock()


class TestCreateHierarchicalRetriever:
    """Tests for create_hierarchical_retriever factory."""

    @pytest.mark.unit
    def test_creates_default_retriever(self) -> None:
        config = MemoryRetrievalConfig(retriever="hierarchical")
        retriever = create_hierarchical_retriever(
            config=config,
            backend=_mock_backend(),
            provider=_mock_provider(),
            model="test-basic-001",
        )
        assert isinstance(retriever, DefaultHierarchicalRetriever)

    @pytest.mark.unit
    def test_creates_with_shared_store(self) -> None:
        config = MemoryRetrievalConfig(retriever="hierarchical")
        shared = AsyncMock()
        retriever = create_hierarchical_retriever(
            config=config,
            backend=_mock_backend(),
            provider=_mock_provider(),
            model="test-basic-001",
            shared_store=shared,
        )
        assert isinstance(retriever, DefaultHierarchicalRetriever)
