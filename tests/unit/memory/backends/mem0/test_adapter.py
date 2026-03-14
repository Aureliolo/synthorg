"""Tests for Mem0 adapter — properties, capabilities, protocol, lifecycle."""

import builtins
import sys
from unittest.mock import MagicMock, patch

import pytest

from synthorg.core.enums import MemoryCategory
from synthorg.memory.backends.mem0.adapter import Mem0MemoryBackend
from synthorg.memory.backends.mem0.config import Mem0BackendConfig
from synthorg.memory.capabilities import MemoryCapabilities
from synthorg.memory.errors import MemoryConnectionError
from synthorg.memory.models import MemoryQuery
from synthorg.memory.protocol import MemoryBackend
from synthorg.memory.shared import SharedKnowledgeStore

from .conftest import make_store_request

pytestmark = pytest.mark.timeout(30)


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

    def test_supports_temporal_true(
        self,
        backend: Mem0MemoryBackend,
    ) -> None:
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

    def test_isinstance_memory_backend(
        self,
        backend: Mem0MemoryBackend,
    ) -> None:
        assert isinstance(backend, MemoryBackend)

    def test_isinstance_memory_capabilities(
        self,
        backend: Mem0MemoryBackend,
    ) -> None:
        assert isinstance(backend, MemoryCapabilities)

    def test_isinstance_shared_knowledge_store(
        self,
        backend: Mem0MemoryBackend,
    ) -> None:
        assert isinstance(backend, SharedKnowledgeStore)


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
            "synthorg.memory.backends.mem0.adapter.asyncio.to_thread",
            return_value=mock_memory,
        ):
            await b.connect()

        assert b.is_connected is True

    async def test_connect_idempotent_when_already_connected(
        self,
        backend: Mem0MemoryBackend,
    ) -> None:
        """connect() is a no-op when already connected."""
        assert backend.is_connected is True
        # Calling connect() again should not re-create client
        original_client = backend._client
        with patch(
            "synthorg.memory.backends.mem0.adapter.asyncio.to_thread",
        ) as mock_to_thread:
            await backend.connect()
        mock_to_thread.assert_not_called()
        assert backend._client is original_client

    async def test_connect_failure_raises(
        self,
        mem0_config: Mem0BackendConfig,
    ) -> None:
        b = Mem0MemoryBackend(mem0_config=mem0_config)
        with (
            patch(
                "synthorg.memory.backends.mem0.adapter.asyncio.to_thread",
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

    async def test_disconnect_does_not_call_reset(
        self,
        backend: Mem0MemoryBackend,
        mock_client: MagicMock,
    ) -> None:
        """disconnect() must never call reset() — that wipes all data."""
        await backend.disconnect()
        mock_client.reset.assert_not_called()

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
        """builtins.MemoryError propagates through health_check."""
        mock_client.get_all.side_effect = builtins.MemoryError("out of memory")
        with pytest.raises(builtins.MemoryError):
            await backend.health_check()

    async def test_connect_memory_error_propagates(
        self,
        mem0_config: Mem0BackendConfig,
    ) -> None:
        """builtins.MemoryError from connect is not wrapped."""
        b = Mem0MemoryBackend(mem0_config=mem0_config)
        with (
            patch(
                "synthorg.memory.backends.mem0.adapter.asyncio.to_thread",
                side_effect=builtins.MemoryError("out of memory"),
            ),
            pytest.raises(builtins.MemoryError),
        ):
            await b.connect()

    async def test_connect_recursion_error_propagates(
        self,
        mem0_config: Mem0BackendConfig,
    ) -> None:
        """RecursionError from connect is not wrapped."""
        b = Mem0MemoryBackend(mem0_config=mem0_config)
        with (
            patch(
                "synthorg.memory.backends.mem0.adapter.asyncio.to_thread",
                side_effect=RecursionError("infinite loop"),
            ),
            pytest.raises(RecursionError),
        ):
            await b.connect()


# ── Connection guard ──────────────────────────────────────────────


@pytest.mark.unit
class TestConnectionGuard:
    async def test_store_raises_when_disconnected(
        self,
        mem0_config: Mem0BackendConfig,
    ) -> None:
        b = Mem0MemoryBackend(mem0_config=mem0_config)
        with pytest.raises(MemoryConnectionError, match="Not connected"):
            await b.store("test-agent-001", make_store_request())

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
            await b.publish("test-agent-001", make_store_request())

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
