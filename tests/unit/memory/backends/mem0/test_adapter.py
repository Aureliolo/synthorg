"""Tests for the Mem0 memory backend adapter."""

import sys
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from ai_company.core.enums import MemoryCategory
from ai_company.memory.backends.mem0.adapter import (
    _SHARED_NAMESPACE,
    Mem0MemoryBackend,
)
from ai_company.memory.backends.mem0.config import (
    Mem0BackendConfig,
    Mem0EmbedderConfig,
)
from ai_company.memory.backends.mem0.mappers import _PUBLISHER_KEY
from ai_company.memory.errors import (
    MemoryConnectionError,
    MemoryRetrievalError,
    MemoryStoreError,
)
from ai_company.memory.models import (
    MemoryQuery,
    MemoryStoreRequest,
)

pytestmark = pytest.mark.timeout(30)


def _test_embedder() -> Mem0EmbedderConfig:
    """Vendor-agnostic embedder config for tests."""
    return Mem0EmbedderConfig(
        provider="test-provider",
        model="test-embedding-001",
    )


@pytest.fixture
def mem0_config() -> Mem0BackendConfig:
    """Default Mem0 config for tests."""
    return Mem0BackendConfig(
        data_dir="/tmp/test-memory",  # noqa: S108
        embedder=_test_embedder(),
    )


@pytest.fixture
def mock_client() -> MagicMock:
    """Mock Mem0 Memory client."""
    return MagicMock()


@pytest.fixture
def backend(
    mem0_config: Mem0BackendConfig,
    mock_client: MagicMock,
) -> Mem0MemoryBackend:
    """Connected backend with mocked Mem0 client."""
    b = Mem0MemoryBackend(mem0_config=mem0_config, max_memories_per_agent=100)
    b._client = mock_client
    b._connected = True
    return b


def _mem0_add_result(memory_id: str = "mem-001") -> dict[str, Any]:
    """Build a typical Mem0 add() return value."""
    return {
        "results": [
            {
                "id": memory_id,
                "memory": "test content",
                "event": "ADD",
            },
        ],
    }


def _mem0_search_result(
    items: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a typical Mem0 search() return value."""
    if items is None:
        items = [
            {
                "id": "mem-001",
                "memory": "found content",
                "score": 0.85,
                "created_at": "2026-03-12T10:00:00+00:00",
                "metadata": {
                    "_synthorg_category": "episodic",
                    "_synthorg_confidence": 0.9,
                },
            },
        ]
    return {"results": items}


def _mem0_get_result(memory_id: str = "mem-001") -> dict[str, Any]:
    """Build a typical Mem0 get() return value."""
    return {
        "id": memory_id,
        "memory": "stored content",
        "created_at": "2026-03-12T10:00:00+00:00",
        "updated_at": None,
        "metadata": {
            "_synthorg_category": "episodic",
            "_synthorg_confidence": 1.0,
        },
    }


def _make_store_request(
    *,
    category: MemoryCategory = MemoryCategory.EPISODIC,
    content: str = "test content",
) -> MemoryStoreRequest:
    """Helper to build a store request."""
    return MemoryStoreRequest(category=category, content=content)


# ── Properties ────────────────────────────────────────────────────


@pytest.mark.unit
class TestProperties:
    def test_backend_name(self, backend: Mem0MemoryBackend) -> None:
        assert backend.backend_name == "mem0"

    def test_is_connected_true(self, backend: Mem0MemoryBackend) -> None:
        assert backend.is_connected is True

    def test_is_connected_false(self, mem0_config: Mem0BackendConfig) -> None:
        b = Mem0MemoryBackend(mem0_config=mem0_config)
        assert b.is_connected is False


# ── Capabilities ──────────────────────────────────────────────────


@pytest.mark.unit
class TestCapabilities:
    def test_supported_categories(self, backend: Mem0MemoryBackend) -> None:
        assert backend.supported_categories == frozenset(MemoryCategory)

    def test_supports_graph_false(self, backend: Mem0MemoryBackend) -> None:
        assert backend.supports_graph is False

    def test_supports_temporal_true(self, backend: Mem0MemoryBackend) -> None:
        assert backend.supports_temporal is True

    def test_supports_vector_search_true(
        self,
        backend: Mem0MemoryBackend,
    ) -> None:
        assert backend.supports_vector_search is True

    def test_supports_shared_access_true(
        self,
        backend: Mem0MemoryBackend,
    ) -> None:
        assert backend.supports_shared_access is True

    def test_max_memories_per_agent(
        self,
        backend: Mem0MemoryBackend,
    ) -> None:
        assert backend.max_memories_per_agent == 100


# ── Protocol Conformance ─────────────────────────────────────────


@pytest.mark.unit
class TestProtocolConformance:
    """Verify Mem0MemoryBackend conforms to protocol interfaces."""

    def test_has_memory_backend_methods(
        self,
        backend: Mem0MemoryBackend,
    ) -> None:
        assert hasattr(backend, "connect")
        assert hasattr(backend, "disconnect")
        assert hasattr(backend, "health_check")
        assert hasattr(backend, "store")
        assert hasattr(backend, "retrieve")
        assert hasattr(backend, "get")
        assert hasattr(backend, "delete")
        assert hasattr(backend, "count")

    def test_has_capabilities_properties(
        self,
        backend: Mem0MemoryBackend,
    ) -> None:
        assert hasattr(backend, "supported_categories")
        assert hasattr(backend, "supports_graph")
        assert hasattr(backend, "supports_temporal")
        assert hasattr(backend, "supports_vector_search")
        assert hasattr(backend, "supports_shared_access")
        assert hasattr(backend, "max_memories_per_agent")

    def test_has_shared_knowledge_methods(
        self,
        backend: Mem0MemoryBackend,
    ) -> None:
        assert hasattr(backend, "publish")
        assert hasattr(backend, "search_shared")
        assert hasattr(backend, "retract")


# ── Lifecycle ─────────────────────────────────────────────────────


@pytest.mark.unit
class TestLifecycle:
    async def test_connect_success(
        self,
        mem0_config: Mem0BackendConfig,
    ) -> None:
        b = Mem0MemoryBackend(mem0_config=mem0_config)
        mock_memory = MagicMock()
        with patch(
            "ai_company.memory.backends.mem0.adapter.asyncio.to_thread",
            return_value=mock_memory,
        ):
            await b.connect()

        assert b.is_connected is True

    async def test_connect_failure_raises(
        self,
        mem0_config: Mem0BackendConfig,
    ) -> None:
        b = Mem0MemoryBackend(mem0_config=mem0_config)
        with (
            patch(
                "ai_company.memory.backends.mem0.adapter.asyncio.to_thread",
                side_effect=RuntimeError("connection failed"),
            ),
            pytest.raises(MemoryConnectionError, match="Failed to connect"),
        ):
            await b.connect()
        assert b.is_connected is False

    async def test_disconnect(self, backend: Mem0MemoryBackend) -> None:
        await backend.disconnect()
        assert backend.is_connected is False
        assert backend._client is None

    async def test_disconnect_when_not_connected(
        self,
        mem0_config: Mem0BackendConfig,
    ) -> None:
        b = Mem0MemoryBackend(mem0_config=mem0_config)
        await b.disconnect()  # Should not raise
        assert b.is_connected is False

    async def test_health_check_connected(
        self,
        backend: Mem0MemoryBackend,
        mock_client: MagicMock,
    ) -> None:
        mock_client.get_all.return_value = {"results": []}
        assert await backend.health_check() is True

    async def test_health_check_disconnected(
        self,
        mem0_config: Mem0BackendConfig,
    ) -> None:
        b = Mem0MemoryBackend(mem0_config=mem0_config)
        assert await b.health_check() is False

    async def test_health_check_probe_failure(
        self,
        backend: Mem0MemoryBackend,
        mock_client: MagicMock,
    ) -> None:
        mock_client.get_all.side_effect = RuntimeError("backend down")
        assert await backend.health_check() is False

    async def test_connect_import_error_raises(
        self,
        mem0_config: Mem0BackendConfig,
    ) -> None:
        """ImportError when mem0 package is not installed."""
        b = Mem0MemoryBackend(mem0_config=mem0_config)
        with (
            patch.dict(sys.modules, {"mem0": None}),
            pytest.raises(MemoryConnectionError, match="not installed"),
        ):
            await b.connect()

    async def test_health_check_reraises_memory_error(
        self,
        backend: Mem0MemoryBackend,
        mock_client: MagicMock,
    ) -> None:
        """MemoryError propagates through health_check."""
        mock_client.get_all.side_effect = MemoryError("out of memory")
        with pytest.raises(MemoryError):
            await backend.health_check()


# ── Connection guard ──────────────────────────────────────────────


@pytest.mark.unit
class TestConnectionGuard:
    async def test_store_raises_when_disconnected(
        self,
        mem0_config: Mem0BackendConfig,
    ) -> None:
        b = Mem0MemoryBackend(mem0_config=mem0_config)
        with pytest.raises(MemoryConnectionError, match="Not connected"):
            await b.store("test-agent-001", _make_store_request())

    async def test_retrieve_raises_when_disconnected(
        self,
        mem0_config: Mem0BackendConfig,
    ) -> None:
        b = Mem0MemoryBackend(mem0_config=mem0_config)
        with pytest.raises(MemoryConnectionError, match="Not connected"):
            await b.retrieve("test-agent-001", MemoryQuery(text="test"))

    async def test_get_raises_when_disconnected(
        self,
        mem0_config: Mem0BackendConfig,
    ) -> None:
        b = Mem0MemoryBackend(mem0_config=mem0_config)
        with pytest.raises(MemoryConnectionError, match="Not connected"):
            await b.get("test-agent-001", "mem-001")

    async def test_delete_raises_when_disconnected(
        self,
        mem0_config: Mem0BackendConfig,
    ) -> None:
        b = Mem0MemoryBackend(mem0_config=mem0_config)
        with pytest.raises(MemoryConnectionError, match="Not connected"):
            await b.delete("test-agent-001", "mem-001")

    async def test_count_raises_when_disconnected(
        self,
        mem0_config: Mem0BackendConfig,
    ) -> None:
        b = Mem0MemoryBackend(mem0_config=mem0_config)
        with pytest.raises(MemoryConnectionError, match="Not connected"):
            await b.count("test-agent-001")

    async def test_publish_raises_when_disconnected(
        self,
        mem0_config: Mem0BackendConfig,
    ) -> None:
        b = Mem0MemoryBackend(mem0_config=mem0_config)
        with pytest.raises(MemoryConnectionError, match="Not connected"):
            await b.publish("test-agent-001", _make_store_request())

    async def test_search_shared_raises_when_disconnected(
        self,
        mem0_config: Mem0BackendConfig,
    ) -> None:
        b = Mem0MemoryBackend(mem0_config=mem0_config)
        with pytest.raises(MemoryConnectionError, match="Not connected"):
            await b.search_shared(MemoryQuery(text="test"))

    async def test_retract_raises_when_disconnected(
        self,
        mem0_config: Mem0BackendConfig,
    ) -> None:
        b = Mem0MemoryBackend(mem0_config=mem0_config)
        with pytest.raises(MemoryConnectionError, match="Not connected"):
            await b.retract("test-agent-001", "mem-001")


# ── Store ─────────────────────────────────────────────────────────


@pytest.mark.unit
class TestStore:
    async def test_store_success(
        self,
        backend: Mem0MemoryBackend,
        mock_client: MagicMock,
    ) -> None:
        mock_client.add.return_value = _mem0_add_result("new-mem-id")

        memory_id = await backend.store(
            "test-agent-001",
            _make_store_request(),
        )

        assert memory_id == "new-mem-id"
        mock_client.add.assert_called_once()
        call_kwargs = mock_client.add.call_args[1]
        assert call_kwargs["user_id"] == "test-agent-001"
        assert call_kwargs["infer"] is False

    async def test_store_empty_results_raises(
        self,
        backend: Mem0MemoryBackend,
        mock_client: MagicMock,
    ) -> None:
        mock_client.add.return_value = {"results": []}

        with pytest.raises(MemoryStoreError, match="no results"):
            await backend.store("test-agent-001", _make_store_request())

    async def test_store_missing_id_raises(
        self,
        backend: Mem0MemoryBackend,
        mock_client: MagicMock,
    ) -> None:
        mock_client.add.return_value = {
            "results": [{"memory": "no id", "event": "ADD"}],
        }

        with pytest.raises(MemoryStoreError, match="missing or blank 'id'"):
            await backend.store("test-agent-001", _make_store_request())

    async def test_store_exception_wraps(
        self,
        backend: Mem0MemoryBackend,
        mock_client: MagicMock,
    ) -> None:
        mock_client.add.side_effect = RuntimeError("disk full")

        with pytest.raises(MemoryStoreError, match="Failed to store") as exc_info:
            await backend.store("test-agent-001", _make_store_request())

        assert exc_info.value.__cause__ is not None

    async def test_store_reraises_memory_error(
        self,
        backend: Mem0MemoryBackend,
        mock_client: MagicMock,
    ) -> None:
        """MemoryError is re-raised without wrapping."""
        mock_client.add.side_effect = MemoryError("out of memory")
        with pytest.raises(MemoryError):
            await backend.store("test-agent-001", _make_store_request())


# ── Retrieve ──────────────────────────────────────────────────────


@pytest.mark.unit
class TestRetrieve:
    async def test_retrieve_with_text(
        self,
        backend: Mem0MemoryBackend,
        mock_client: MagicMock,
    ) -> None:
        mock_client.search.return_value = _mem0_search_result()

        query = MemoryQuery(text="find relevant", limit=5)
        entries = await backend.retrieve("test-agent-001", query)

        assert len(entries) == 1
        assert entries[0].content == "found content"
        assert entries[0].relevance_score == 0.85
        mock_client.search.assert_called_once()

    async def test_retrieve_without_text(
        self,
        backend: Mem0MemoryBackend,
        mock_client: MagicMock,
    ) -> None:
        mock_client.get_all.return_value = _mem0_search_result(
            [
                {
                    "id": "mem-001",
                    "memory": "all content",
                    "created_at": "2026-03-12T10:00:00+00:00",
                    "metadata": {},
                },
            ],
        )

        query = MemoryQuery(text=None, limit=10)
        entries = await backend.retrieve("test-agent-001", query)

        assert len(entries) == 1
        mock_client.get_all.assert_called_once()

    async def test_retrieve_applies_post_filters(
        self,
        backend: Mem0MemoryBackend,
        mock_client: MagicMock,
    ) -> None:
        mock_client.search.return_value = _mem0_search_result(
            [
                {
                    "id": "m1",
                    "memory": "episodic",
                    "score": 0.9,
                    "created_at": "2026-03-12T10:00:00+00:00",
                    "metadata": {"_synthorg_category": "episodic"},
                },
                {
                    "id": "m2",
                    "memory": "semantic",
                    "score": 0.8,
                    "created_at": "2026-03-12T10:00:00+00:00",
                    "metadata": {"_synthorg_category": "semantic"},
                },
            ],
        )

        query = MemoryQuery(
            text="test",
            categories=frozenset({MemoryCategory.EPISODIC}),
        )
        entries = await backend.retrieve("test-agent-001", query)

        assert len(entries) == 1
        assert entries[0].category == MemoryCategory.EPISODIC

    async def test_retrieve_exception_wraps(
        self,
        backend: Mem0MemoryBackend,
        mock_client: MagicMock,
    ) -> None:
        mock_client.search.side_effect = RuntimeError("search failed")

        with pytest.raises(MemoryRetrievalError, match="Failed to retrieve"):
            await backend.retrieve(
                "test-agent-001",
                MemoryQuery(text="test"),
            )

    async def test_retrieve_reraises_memory_error(
        self,
        backend: Mem0MemoryBackend,
        mock_client: MagicMock,
    ) -> None:
        """MemoryError is re-raised without wrapping."""
        mock_client.search.side_effect = MemoryError("out of memory")
        with pytest.raises(MemoryError):
            await backend.retrieve(
                "test-agent-001",
                MemoryQuery(text="test"),
            )


# ── Get ───────────────────────────────────────────────────────────


@pytest.mark.unit
class TestGet:
    async def test_get_existing(
        self,
        backend: Mem0MemoryBackend,
        mock_client: MagicMock,
    ) -> None:
        mock_client.get.return_value = _mem0_get_result("mem-001")

        entry = await backend.get("test-agent-001", "mem-001")

        assert entry is not None
        assert entry.id == "mem-001"
        assert entry.agent_id == "test-agent-001"

    async def test_get_not_found(
        self,
        backend: Mem0MemoryBackend,
        mock_client: MagicMock,
    ) -> None:
        mock_client.get.return_value = None

        entry = await backend.get("test-agent-001", "nonexistent")

        assert entry is None

    async def test_get_exception_wraps(
        self,
        backend: Mem0MemoryBackend,
        mock_client: MagicMock,
    ) -> None:
        mock_client.get.side_effect = RuntimeError("backend error")

        with pytest.raises(MemoryRetrievalError, match="Failed to get"):
            await backend.get("test-agent-001", "mem-001")


# ── Delete ────────────────────────────────────────────────────────


@pytest.mark.unit
class TestDelete:
    async def test_delete_existing(
        self,
        backend: Mem0MemoryBackend,
        mock_client: MagicMock,
    ) -> None:
        mock_client.get.return_value = _mem0_get_result("mem-001")
        mock_client.delete.return_value = None

        result = await backend.delete("test-agent-001", "mem-001")

        assert result is True
        mock_client.delete.assert_called_once_with("mem-001")

    async def test_delete_not_found(
        self,
        backend: Mem0MemoryBackend,
        mock_client: MagicMock,
    ) -> None:
        mock_client.get.return_value = None

        result = await backend.delete("test-agent-001", "nonexistent")

        assert result is False
        mock_client.delete.assert_not_called()

    async def test_delete_exception_wraps(
        self,
        backend: Mem0MemoryBackend,
        mock_client: MagicMock,
    ) -> None:
        mock_client.get.side_effect = RuntimeError("backend error")

        with pytest.raises(MemoryStoreError, match="Failed to delete"):
            await backend.delete("test-agent-001", "mem-001")

    async def test_delete_get_ok_but_delete_fails(
        self,
        backend: Mem0MemoryBackend,
        mock_client: MagicMock,
    ) -> None:
        mock_client.get.return_value = _mem0_get_result("mem-001")
        mock_client.delete.side_effect = RuntimeError("delete failed")

        with pytest.raises(MemoryStoreError, match="Failed to delete"):
            await backend.delete("test-agent-001", "mem-001")


# ── Count ─────────────────────────────────────────────────────────


@pytest.mark.unit
class TestCount:
    async def test_count_all(
        self,
        backend: Mem0MemoryBackend,
        mock_client: MagicMock,
    ) -> None:
        mock_client.get_all.return_value = {
            "results": [
                {"id": "m1", "memory": "a", "metadata": {}},
                {"id": "m2", "memory": "b", "metadata": {}},
            ],
        }

        count = await backend.count("test-agent-001")
        assert count == 2

    async def test_count_by_category(
        self,
        backend: Mem0MemoryBackend,
        mock_client: MagicMock,
    ) -> None:
        mock_client.get_all.return_value = {
            "results": [
                {
                    "id": "m1",
                    "memory": "a",
                    "metadata": {"_synthorg_category": "episodic"},
                },
                {
                    "id": "m2",
                    "memory": "b",
                    "metadata": {"_synthorg_category": "semantic"},
                },
                {
                    "id": "m3",
                    "memory": "c",
                    "metadata": {"_synthorg_category": "episodic"},
                },
            ],
        }

        count = await backend.count(
            "test-agent-001",
            category=MemoryCategory.EPISODIC,
        )
        assert count == 2

    async def test_count_with_invalid_category_in_data(
        self,
        backend: Mem0MemoryBackend,
        mock_client: MagicMock,
    ) -> None:
        """Invalid category in stored data defaults to WORKING."""
        mock_client.get_all.return_value = {
            "results": [
                {
                    "id": "m1",
                    "memory": "a",
                    "metadata": {"_synthorg_category": "bogus_category"},
                },
                {
                    "id": "m2",
                    "memory": "b",
                    "metadata": {"_synthorg_category": "episodic"},
                },
            ],
        }

        count = await backend.count(
            "test-agent-001",
            category=MemoryCategory.WORKING,
        )
        # "bogus_category" defaults to WORKING
        assert count == 1

    async def test_count_exception_wraps(
        self,
        backend: Mem0MemoryBackend,
        mock_client: MagicMock,
    ) -> None:
        mock_client.get_all.side_effect = RuntimeError("fail")

        with pytest.raises(MemoryRetrievalError, match="Failed to count"):
            await backend.count("test-agent-001")


# ── Shared Knowledge Store ────────────────────────────────────────


@pytest.mark.unit
class TestPublish:
    async def test_publish_success(
        self,
        backend: Mem0MemoryBackend,
        mock_client: MagicMock,
    ) -> None:
        mock_client.add.return_value = _mem0_add_result("shared-mem-001")

        memory_id = await backend.publish(
            "test-agent-001",
            _make_store_request(),
        )

        assert memory_id == "shared-mem-001"
        call_kwargs = mock_client.add.call_args[1]
        assert call_kwargs["user_id"] == _SHARED_NAMESPACE
        assert _PUBLISHER_KEY in call_kwargs["metadata"]
        assert call_kwargs["metadata"][_PUBLISHER_KEY] == "test-agent-001"

    async def test_publish_empty_results_raises(
        self,
        backend: Mem0MemoryBackend,
        mock_client: MagicMock,
    ) -> None:
        mock_client.add.return_value = {"results": []}

        with pytest.raises(MemoryStoreError, match="no results"):
            await backend.publish("test-agent-001", _make_store_request())

    async def test_publish_exception_wraps(
        self,
        backend: Mem0MemoryBackend,
        mock_client: MagicMock,
    ) -> None:
        mock_client.add.side_effect = RuntimeError("network error")

        with pytest.raises(MemoryStoreError, match="Failed to publish"):
            await backend.publish("test-agent-001", _make_store_request())

    async def test_publish_missing_id_raises(
        self,
        backend: Mem0MemoryBackend,
        mock_client: MagicMock,
    ) -> None:
        """Publish result missing 'id' raises MemoryStoreError."""
        mock_client.add.return_value = {
            "results": [{"memory": "no id", "event": "ADD"}],
        }

        with pytest.raises(MemoryStoreError, match="missing or blank 'id'"):
            await backend.publish("test-agent-001", _make_store_request())


@pytest.mark.unit
class TestSearchShared:
    async def test_search_shared_with_text(
        self,
        backend: Mem0MemoryBackend,
        mock_client: MagicMock,
    ) -> None:
        mock_client.search.return_value = _mem0_search_result(
            [
                {
                    "id": "shared-1",
                    "memory": "shared fact",
                    "score": 0.9,
                    "created_at": "2026-03-12T10:00:00+00:00",
                    "metadata": {
                        "_synthorg_category": "semantic",
                        _PUBLISHER_KEY: "test-agent-002",
                    },
                },
            ],
        )

        query = MemoryQuery(text="find shared", limit=5)
        entries = await backend.search_shared(query)

        assert len(entries) == 1
        assert entries[0].agent_id == "test-agent-002"
        mock_client.search.assert_called_once()
        call_kwargs = mock_client.search.call_args[1]
        assert call_kwargs["user_id"] == _SHARED_NAMESPACE

    async def test_search_shared_without_text(
        self,
        backend: Mem0MemoryBackend,
        mock_client: MagicMock,
    ) -> None:
        mock_client.get_all.return_value = _mem0_search_result(
            [
                {
                    "id": "shared-1",
                    "memory": "shared fact",
                    "created_at": "2026-03-12T10:00:00+00:00",
                    "metadata": {
                        _PUBLISHER_KEY: "test-agent-002",
                    },
                },
            ],
        )

        query = MemoryQuery(text=None)
        entries = await backend.search_shared(query)

        assert len(entries) == 1
        mock_client.get_all.assert_called_once()

    async def test_search_shared_exclude_agent(
        self,
        backend: Mem0MemoryBackend,
        mock_client: MagicMock,
    ) -> None:
        mock_client.search.return_value = _mem0_search_result(
            [
                {
                    "id": "s1",
                    "memory": "from agent 1",
                    "score": 0.9,
                    "created_at": "2026-03-12T10:00:00+00:00",
                    "metadata": {_PUBLISHER_KEY: "test-agent-001"},
                },
                {
                    "id": "s2",
                    "memory": "from agent 2",
                    "score": 0.8,
                    "created_at": "2026-03-12T10:00:00+00:00",
                    "metadata": {_PUBLISHER_KEY: "test-agent-002"},
                },
            ],
        )

        query = MemoryQuery(text="test")
        entries = await backend.search_shared(
            query,
            exclude_agent="test-agent-001",
        )

        assert len(entries) == 1
        assert entries[0].agent_id == "test-agent-002"

    async def test_search_shared_exception_wraps(
        self,
        backend: Mem0MemoryBackend,
        mock_client: MagicMock,
    ) -> None:
        mock_client.search.side_effect = RuntimeError("search error")

        with pytest.raises(MemoryRetrievalError, match="Failed to search"):
            await backend.search_shared(MemoryQuery(text="test"))


@pytest.mark.unit
class TestRetract:
    async def test_retract_success(
        self,
        backend: Mem0MemoryBackend,
        mock_client: MagicMock,
    ) -> None:
        mock_client.get.return_value = {
            "id": "shared-001",
            "memory": "shared content",
            "created_at": "2026-03-12T10:00:00+00:00",
            "metadata": {_PUBLISHER_KEY: "test-agent-001"},
        }
        mock_client.delete.return_value = None

        result = await backend.retract("test-agent-001", "shared-001")

        assert result is True
        mock_client.delete.assert_called_once_with("shared-001")

    async def test_retract_not_found(
        self,
        backend: Mem0MemoryBackend,
        mock_client: MagicMock,
    ) -> None:
        mock_client.get.return_value = None

        result = await backend.retract("test-agent-001", "nonexistent")

        assert result is False

    async def test_retract_ownership_mismatch(
        self,
        backend: Mem0MemoryBackend,
        mock_client: MagicMock,
    ) -> None:
        mock_client.get.return_value = {
            "id": "shared-001",
            "memory": "content",
            "created_at": "2026-03-12T10:00:00+00:00",
            "metadata": {_PUBLISHER_KEY: "test-agent-002"},
        }

        with pytest.raises(MemoryStoreError, match="cannot retract"):
            await backend.retract("test-agent-001", "shared-001")

    async def test_retract_no_publisher_raises(
        self,
        backend: Mem0MemoryBackend,
        mock_client: MagicMock,
    ) -> None:
        mock_client.get.return_value = {
            "id": "not-shared-001",
            "memory": "private content",
            "created_at": "2026-03-12T10:00:00+00:00",
            "metadata": {},
        }

        with pytest.raises(MemoryStoreError, match="not a shared memory"):
            await backend.retract("test-agent-001", "not-shared-001")

    async def test_retract_exception_wraps(
        self,
        backend: Mem0MemoryBackend,
        mock_client: MagicMock,
    ) -> None:
        mock_client.get.side_effect = RuntimeError("backend error")

        with pytest.raises(MemoryStoreError, match="Failed to retract"):
            await backend.retract("test-agent-001", "shared-001")

    async def test_retract_reraises_memory_error(
        self,
        backend: Mem0MemoryBackend,
        mock_client: MagicMock,
    ) -> None:
        """MemoryError is re-raised without wrapping."""
        mock_client.get.side_effect = MemoryError("out of memory")
        with pytest.raises(MemoryError):
            await backend.retract("test-agent-001", "shared-001")

    async def test_retract_delete_failure_wraps(
        self,
        backend: Mem0MemoryBackend,
        mock_client: MagicMock,
    ) -> None:
        """Exception during delete phase wraps in MemoryStoreError."""
        mock_client.get.return_value = {
            "id": "shared-001",
            "memory": "content",
            "created_at": "2026-03-12T10:00:00+00:00",
            "metadata": {_PUBLISHER_KEY: "test-agent-001"},
        }
        mock_client.delete.side_effect = RuntimeError("delete failed")

        with pytest.raises(MemoryStoreError, match="Failed to retract"):
            await backend.retract("test-agent-001", "shared-001")


@pytest.mark.unit
class TestAdditionalEdgeCases:
    """Edge cases for improved coverage."""

    async def test_store_blank_id_from_add_raises(
        self,
        backend: Mem0MemoryBackend,
        mock_client: MagicMock,
    ) -> None:
        """Store result with blank ID raises MemoryStoreError."""
        mock_client.add.return_value = {
            "results": [{"id": "", "event": "ADD"}],
        }
        with pytest.raises(MemoryStoreError, match="missing or blank 'id'"):
            await backend.store("test-agent-001", _make_store_request())

    async def test_store_whitespace_id_from_add_raises(
        self,
        backend: Mem0MemoryBackend,
        mock_client: MagicMock,
    ) -> None:
        """Store result with whitespace-only ID raises MemoryStoreError."""
        mock_client.add.return_value = {
            "results": [{"id": "   ", "event": "ADD"}],
        }
        with pytest.raises(MemoryStoreError, match="missing or blank 'id'"):
            await backend.store("test-agent-001", _make_store_request())

    async def test_get_reraises_memory_error(
        self,
        backend: Mem0MemoryBackend,
        mock_client: MagicMock,
    ) -> None:
        """MemoryError is re-raised without wrapping in get()."""
        mock_client.get.side_effect = MemoryError("out of memory")
        with pytest.raises(MemoryError):
            await backend.get("test-agent-001", "mem-001")

    async def test_delete_reraises_memory_error(
        self,
        backend: Mem0MemoryBackend,
        mock_client: MagicMock,
    ) -> None:
        """MemoryError is re-raised without wrapping in delete()."""
        mock_client.get.side_effect = MemoryError("out of memory")
        with pytest.raises(MemoryError):
            await backend.delete("test-agent-001", "mem-001")

    async def test_count_reraises_memory_error(
        self,
        backend: Mem0MemoryBackend,
        mock_client: MagicMock,
    ) -> None:
        """MemoryError is re-raised without wrapping in count()."""
        mock_client.get_all.side_effect = MemoryError("out of memory")
        with pytest.raises(MemoryError):
            await backend.count("test-agent-001")

    async def test_publish_reraises_memory_error(
        self,
        backend: Mem0MemoryBackend,
        mock_client: MagicMock,
    ) -> None:
        """MemoryError is re-raised without wrapping in publish()."""
        mock_client.add.side_effect = MemoryError("out of memory")
        with pytest.raises(MemoryError):
            await backend.publish("test-agent-001", _make_store_request())

    async def test_search_shared_reraises_memory_error(
        self,
        backend: Mem0MemoryBackend,
        mock_client: MagicMock,
    ) -> None:
        """MemoryError is re-raised without wrapping in search_shared()."""
        mock_client.search.side_effect = MemoryError("out of memory")
        with pytest.raises(MemoryError):
            await backend.search_shared(MemoryQuery(text="test"))

    async def test_store_non_list_results_raises(
        self,
        backend: Mem0MemoryBackend,
        mock_client: MagicMock,
    ) -> None:
        """Store result with non-list 'results' raises MemoryStoreError."""
        mock_client.add.return_value = {"results": "not-a-list"}
        with pytest.raises(MemoryStoreError, match="no results"):
            await backend.store("test-agent-001", _make_store_request())

    async def test_retrieve_invalid_entry_raises(
        self,
        backend: Mem0MemoryBackend,
        mock_client: MagicMock,
    ) -> None:
        """Invalid entry in search results wraps as MemoryRetrievalError."""
        mock_client.search.return_value = {
            "results": [
                {"id": "", "memory": "blank id", "metadata": {}},
            ],
        }
        with pytest.raises(MemoryRetrievalError, match="missing or blank"):
            await backend.retrieve(
                "test-agent-001",
                MemoryQuery(text="test"),
            )

    async def test_search_shared_no_publisher_uses_namespace(
        self,
        backend: Mem0MemoryBackend,
        mock_client: MagicMock,
    ) -> None:
        """Entries without publisher metadata use the shared namespace."""
        mock_client.search.return_value = _mem0_search_result(
            [
                {
                    "id": "shared-1",
                    "memory": "orphan fact",
                    "score": 0.9,
                    "created_at": "2026-03-12T10:00:00+00:00",
                    "metadata": {"_synthorg_category": "semantic"},
                },
            ],
        )

        entries = await backend.search_shared(MemoryQuery(text="test"))
        assert len(entries) == 1
        assert entries[0].agent_id == _SHARED_NAMESPACE

    async def test_count_empty_results(
        self,
        backend: Mem0MemoryBackend,
        mock_client: MagicMock,
    ) -> None:
        """Count returns 0 for empty results."""
        mock_client.get_all.return_value = {"results": []}
        count = await backend.count("test-agent-001")
        assert count == 0
