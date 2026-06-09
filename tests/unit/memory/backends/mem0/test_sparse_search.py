"""Tests for Qdrant sparse search operations."""

from unittest.mock import MagicMock, patch

import pytest
from qdrant_client.models import ScoredPoint

from synthorg.core.memory_enums import MemoryCategory
from synthorg.core.types import NotBlankStr
from synthorg.memory.backends.mem0.adapter import Mem0MemoryBackend
from synthorg.memory.backends.mem0.config import (
    Mem0BackendConfig,
    Mem0EmbedderConfig,
)
from synthorg.memory.backends.mem0.sparse_search import (
    ensure_sparse_field,
    scored_points_to_entries,
    search_sparse,
    upsert_sparse_vector,
)
from synthorg.memory.models import MemoryQuery
from synthorg.memory.sparse import SparseVector


def _test_embedder() -> Mem0EmbedderConfig:
    return Mem0EmbedderConfig(provider="test-provider", model="test-embedding-001")


def _sparse_config() -> Mem0BackendConfig:
    return Mem0BackendConfig(
        data_dir="/tmp/test-memory",  # noqa: S108
        embedder=_test_embedder(),
        sparse_search_enabled=True,
    )


def _make_scored_point(  # noqa: PLR0913
    *,
    point_id: str = "mem-001",
    score: float = 0.85,
    user_id: str = "agent-1",
    memory: str = "test content",
    category: str = "episodic",
    created_at: str = "2026-03-12T10:00:00+00:00",
) -> ScoredPoint:
    """Build a Qdrant ScoredPoint for sparse-search tests."""
    return ScoredPoint(
        id=point_id,
        version=0,
        score=score,
        payload={
            "user_id": user_id,
            "data": memory,
            "metadata": {
                "_synthorg_category": category,
                "_synthorg_confidence": 1.0,
            },
            "created_at": created_at,
        },
    )


# -- ensure_sparse_field ---------------------------------------------------


@pytest.mark.unit
class TestEnsureSparseField:
    def test_creates_field_when_missing(self) -> None:
        client = MagicMock()
        collection_info = MagicMock()
        collection_info.config.params.sparse_vectors = None
        client.get_collection.return_value = collection_info

        ensure_sparse_field(client, "test_collection")

        client.update_collection.assert_called_once()
        call_kwargs = client.update_collection.call_args
        assert call_kwargs.kwargs["collection_name"] == "test_collection"

    def test_skips_when_field_exists(self) -> None:
        client = MagicMock()
        collection_info = MagicMock()
        collection_info.config.params.sparse_vectors = {"bm25": MagicMock()}
        client.get_collection.return_value = collection_info

        ensure_sparse_field(client, "test_collection")

        client.update_collection.assert_not_called()

    def test_creates_field_when_other_sparse_exists(self) -> None:
        client = MagicMock()
        collection_info = MagicMock()
        collection_info.config.params.sparse_vectors = {"other": MagicMock()}
        client.get_collection.return_value = collection_info

        ensure_sparse_field(client, "test_collection", field_name="bm25")

        client.update_collection.assert_called_once()

    def test_custom_field_name(self) -> None:
        client = MagicMock()
        collection_info = MagicMock()
        collection_info.config.params.sparse_vectors = {"custom_bm25": MagicMock()}
        client.get_collection.return_value = collection_info

        ensure_sparse_field(client, "test_collection", field_name="custom_bm25")

        client.update_collection.assert_not_called()


# -- upsert_sparse_vector -------------------------------------------------


@pytest.mark.unit
class TestUpsertSparseVector:
    def test_updates_point_with_sparse_vector(self) -> None:
        client = MagicMock()
        vec = SparseVector(indices=(10, 20, 30), values=(1.0, 2.0, 3.0))

        upsert_sparse_vector(client, "test_collection", "mem-001", vec)

        client.update_vectors.assert_called_once()

    def test_empty_vector_skipped(self) -> None:
        client = MagicMock()
        vec = SparseVector(indices=(), values=())

        upsert_sparse_vector(client, "test_collection", "mem-001", vec)

        client.update_vectors.assert_not_called()

    def test_custom_field_name(self) -> None:
        client = MagicMock()
        vec = SparseVector(indices=(10,), values=(1.0,))

        upsert_sparse_vector(
            client, "test_collection", "mem-001", vec, field_name="custom"
        )

        client.update_vectors.assert_called_once()


# -- search_sparse ---------------------------------------------------------


@pytest.mark.unit
class TestSearchSparse:
    def test_queries_sparse_field(self) -> None:
        client = MagicMock()
        result = MagicMock()
        result.points = [_make_scored_point()]
        client.query_points.return_value = result
        query_vec = SparseVector(indices=(10,), values=(1.0,))

        points = search_sparse(
            client,
            "test_collection",
            query_vec,
            user_id_filter="agent-1",
            limit=10,
        )

        client.query_points.assert_called_once()
        assert len(points) == 1
        # Verify the sparse vector was forwarded
        call_kwargs = client.query_points.call_args.kwargs
        query_arg = call_kwargs["query"]
        assert list(query_arg.indices) == list(query_vec.indices)
        assert list(query_arg.values) == list(query_vec.values)

    def test_applies_user_id_filter(self) -> None:
        client = MagicMock()
        result = MagicMock()
        result.points = []
        client.query_points.return_value = result
        query_vec = SparseVector(indices=(10,), values=(1.0,))

        search_sparse(
            client,
            "test_collection",
            query_vec,
            user_id_filter="agent-1",
            limit=5,
        )

        call_kwargs = client.query_points.call_args.kwargs
        assert call_kwargs["limit"] == 5
        assert call_kwargs["using"] == "bm25"
        # Verify user_id filter enforces agent isolation
        filter_obj = call_kwargs["query_filter"]
        assert filter_obj is not None
        assert len(filter_obj.must) == 1
        condition = filter_obj.must[0]
        assert condition.key == "user_id"
        assert condition.match.value == "agent-1"

    def test_empty_vector_returns_empty(self) -> None:
        client = MagicMock()
        query_vec = SparseVector(indices=(), values=())

        points = search_sparse(
            client,
            "test_collection",
            query_vec,
            user_id_filter="agent-1",
            limit=10,
        )

        client.query_points.assert_not_called()
        assert points == []


# -- scored_points_to_entries ----------------------------------------------


@pytest.mark.unit
class TestScoredPointsToEntries:
    def test_maps_single_point(self) -> None:
        point = _make_scored_point(
            point_id="mem-001",
            score=0.85,
            memory="test content",
        )

        entries = scored_points_to_entries([point], NotBlankStr("agent-1"))

        assert len(entries) == 1
        assert entries[0].id == "mem-001"
        assert entries[0].content == "test content"
        assert entries[0].relevance_score is not None
        assert entries[0].relevance_score <= 1.0

    def test_maps_multiple_points(self) -> None:
        points = [
            _make_scored_point(point_id="mem-001", score=0.9),
            _make_scored_point(point_id="mem-002", score=0.7),
        ]

        entries = scored_points_to_entries(points, NotBlankStr("agent-1"))

        assert len(entries) == 2

    def test_empty_points_returns_empty(self) -> None:
        entries = scored_points_to_entries([], NotBlankStr("agent-1"))
        assert entries == ()

    def test_skips_malformed_points(self) -> None:
        good = _make_scored_point(point_id="mem-001")
        # payload=None triggers an exception in _point_to_entry, so the
        # point is skipped rather than mapped to an entry.
        bad = ScoredPoint(id="mem-002", version=0, score=0.5, payload=None)

        entries = scored_points_to_entries([good, bad], NotBlankStr("agent-1"))

        # Should skip the bad point and return only the good one
        assert len(entries) == 1
        assert entries[0].id == "mem-001"


# -- Adapter integration ---------------------------------------------------


@pytest.mark.unit
class TestAdapterSparseIntegration:
    def test_config_sparse_enabled(self) -> None:
        config = _sparse_config()
        assert config.sparse_search_enabled is True

    def test_config_sparse_disabled_by_default(self) -> None:
        config = Mem0BackendConfig(
            data_dir="/tmp/test-memory",  # noqa: S108
            embedder=_test_embedder(),
        )
        assert config.sparse_search_enabled is False

    def test_supports_sparse_search_when_enabled(self) -> None:
        config = _sparse_config()
        backend = Mem0MemoryBackend(mem0_config=config)
        # Not connected yet, so sparse not available
        assert backend.supports_sparse_search is False

    def test_supports_sparse_search_when_disabled(self) -> None:
        config = Mem0BackendConfig(
            data_dir="/tmp/test-memory",  # noqa: S108
            embedder=_test_embedder(),
        )
        backend = Mem0MemoryBackend(mem0_config=config)
        assert backend.supports_sparse_search is False

    async def test_store_calls_sparse_upsert_when_enabled(self) -> None:
        config = _sparse_config()
        backend = Mem0MemoryBackend(mem0_config=config)
        mock_client = MagicMock()
        mock_client.add.return_value = {
            "results": [{"id": "mem-001", "memory": "x", "event": "ADD"}],
        }
        backend._client = mock_client
        backend._connected = True
        mock_qdrant = MagicMock()
        backend._qdrant_client = mock_qdrant

        from synthorg.memory.models import MemoryStoreRequest

        request = MemoryStoreRequest(
            category=MemoryCategory.EPISODIC,
            content="test sparse content",
        )

        with patch(
            "synthorg.memory.backends.mem0.sparse_search.upsert_sparse_vector",
        ) as mock_upsert:
            await backend.store(NotBlankStr("agent-1"), request)
            mock_upsert.assert_called_once()

    async def test_store_sparse_failure_does_not_fail_store(self) -> None:
        config = _sparse_config()
        backend = Mem0MemoryBackend(mem0_config=config)
        mock_client = MagicMock()
        mock_client.add.return_value = {
            "results": [{"id": "mem-001", "memory": "x", "event": "ADD"}],
        }
        backend._client = mock_client
        backend._connected = True
        mock_qdrant = MagicMock()
        backend._qdrant_client = mock_qdrant

        from synthorg.memory.models import MemoryStoreRequest

        request = MemoryStoreRequest(
            category=MemoryCategory.EPISODIC,
            content="test sparse content",
        )

        with patch(
            "synthorg.memory.backends.mem0.sparse_search.upsert_sparse_vector",
            side_effect=RuntimeError("sparse failed"),
        ):
            # Should not raise -- sparse failure is non-fatal
            memory_id = await backend.store(NotBlankStr("agent-1"), request)
            assert memory_id == "mem-001"

    async def test_retrieve_sparse_returns_entries(self) -> None:
        config = _sparse_config()
        backend = Mem0MemoryBackend(mem0_config=config)
        mock_client = MagicMock()
        backend._client = mock_client
        backend._connected = True
        mock_qdrant = MagicMock()
        backend._qdrant_client = mock_qdrant

        result = MagicMock()
        result.points = [_make_scored_point()]
        mock_qdrant.query_points.return_value = result

        query = MemoryQuery(text="test query", limit=10)
        entries = await backend.retrieve_sparse(
            NotBlankStr("agent-1"),
            query,
        )

        assert len(entries) > 0

    async def test_retrieve_sparse_when_disabled_returns_empty(self) -> None:
        config = Mem0BackendConfig(
            data_dir="/tmp/test-memory",  # noqa: S108
            embedder=_test_embedder(),
        )
        backend = Mem0MemoryBackend(mem0_config=config)
        backend._connected = True
        backend._client = MagicMock()

        query = MemoryQuery(text="test query", limit=10)
        entries = await backend.retrieve_sparse(
            NotBlankStr("agent-1"),
            query,
        )

        assert entries == ()
