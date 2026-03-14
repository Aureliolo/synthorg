"""Tests for Mem0 adapter — shared knowledge store (publish, search, retract)."""

import builtins
from unittest.mock import MagicMock

import pytest

from synthorg.memory.backends.mem0.adapter import Mem0MemoryBackend
from synthorg.memory.backends.mem0.mappers import PUBLISHER_KEY, SHARED_NAMESPACE
from synthorg.memory.errors import (
    MemoryRetrievalError,
    MemoryStoreError,
)
from synthorg.memory.models import MemoryQuery

from .conftest import (
    make_store_request,
    mem0_add_result,
    mem0_search_result,
)

pytestmark = pytest.mark.timeout(30)


# ── Publish ──────────────────────────────────────────────────────


@pytest.mark.unit
class TestPublish:
    async def test_publish_success(
        self,
        backend: Mem0MemoryBackend,
        mock_client: MagicMock,
    ) -> None:
        mock_client.add.return_value = mem0_add_result("shared-mem-001")

        memory_id = await backend.publish(
            "test-agent-001",
            make_store_request(),
        )

        assert memory_id == "shared-mem-001"
        call_kwargs = mock_client.add.call_args[1]
        assert call_kwargs["user_id"] == SHARED_NAMESPACE
        assert PUBLISHER_KEY in call_kwargs["metadata"]
        assert call_kwargs["metadata"][PUBLISHER_KEY] == "test-agent-001"

    async def test_publish_empty_results_raises(
        self,
        backend: Mem0MemoryBackend,
        mock_client: MagicMock,
    ) -> None:
        mock_client.add.return_value = {"results": []}

        with pytest.raises(MemoryStoreError, match="no results"):
            await backend.publish("test-agent-001", make_store_request())

    async def test_publish_exception_wraps(
        self,
        backend: Mem0MemoryBackend,
        mock_client: MagicMock,
    ) -> None:
        mock_client.add.side_effect = RuntimeError("network error")

        with pytest.raises(MemoryStoreError, match="Failed to publish"):
            await backend.publish("test-agent-001", make_store_request())

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
            await backend.publish("test-agent-001", make_store_request())

    @pytest.mark.parametrize(
        "exc_type",
        [builtins.MemoryError, RecursionError],
        ids=["MemoryError", "RecursionError"],
    )
    async def test_publish_reraises_system_error(
        self,
        backend: Mem0MemoryBackend,
        mock_client: MagicMock,
        exc_type: type[BaseException],
    ) -> None:
        """System errors are re-raised without wrapping."""
        mock_client.add.side_effect = exc_type("system failure")
        with pytest.raises(exc_type):
            await backend.publish("test-agent-001", make_store_request())

    async def test_publish_rejects_shared_namespace_agent_id(
        self,
        backend: Mem0MemoryBackend,
    ) -> None:
        """publish() rejects the shared namespace as agent_id."""
        with pytest.raises(MemoryStoreError, match="reserved shared namespace"):
            await backend.publish(SHARED_NAMESPACE, make_store_request())


# ── SearchShared ─────────────────────────────────────────────────


@pytest.mark.unit
class TestSearchShared:
    async def test_search_shared_with_text(
        self,
        backend: Mem0MemoryBackend,
        mock_client: MagicMock,
    ) -> None:
        mock_client.search.return_value = mem0_search_result(
            [
                {
                    "id": "shared-1",
                    "memory": "shared fact",
                    "score": 0.9,
                    "created_at": "2026-03-12T10:00:00+00:00",
                    "metadata": {
                        "_synthorg_category": "semantic",
                        PUBLISHER_KEY: "test-agent-002",
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
        assert call_kwargs["user_id"] == SHARED_NAMESPACE

    async def test_search_shared_without_text(
        self,
        backend: Mem0MemoryBackend,
        mock_client: MagicMock,
    ) -> None:
        mock_client.get_all.return_value = mem0_search_result(
            [
                {
                    "id": "shared-1",
                    "memory": "shared fact",
                    "created_at": "2026-03-12T10:00:00+00:00",
                    "metadata": {
                        PUBLISHER_KEY: "test-agent-002",
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
        mock_client.search.return_value = mem0_search_result(
            [
                {
                    "id": "s1",
                    "memory": "from agent 1",
                    "score": 0.9,
                    "created_at": "2026-03-12T10:00:00+00:00",
                    "metadata": {PUBLISHER_KEY: "test-agent-001"},
                },
                {
                    "id": "s2",
                    "memory": "from agent 2",
                    "score": 0.8,
                    "created_at": "2026-03-12T10:00:00+00:00",
                    "metadata": {PUBLISHER_KEY: "test-agent-002"},
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

    @pytest.mark.parametrize(
        "exc_type",
        [builtins.MemoryError, RecursionError],
        ids=["MemoryError", "RecursionError"],
    )
    async def test_search_shared_reraises_system_error(
        self,
        backend: Mem0MemoryBackend,
        mock_client: MagicMock,
        exc_type: type[BaseException],
    ) -> None:
        """System errors are re-raised without wrapping."""
        mock_client.search.side_effect = exc_type("system failure")
        with pytest.raises(exc_type):
            await backend.search_shared(MemoryQuery(text="test"))

    async def test_search_shared_with_category_post_filter(
        self,
        backend: Mem0MemoryBackend,
        mock_client: MagicMock,
    ) -> None:
        """search_shared applies post-filters (e.g. category filter)."""
        mock_client.search.return_value = mem0_search_result(
            [
                {
                    "id": "s1",
                    "memory": "episodic fact",
                    "score": 0.9,
                    "created_at": "2026-03-12T10:00:00+00:00",
                    "metadata": {
                        "_synthorg_category": "episodic",
                        PUBLISHER_KEY: "test-agent-001",
                    },
                },
                {
                    "id": "s2",
                    "memory": "semantic fact",
                    "score": 0.8,
                    "created_at": "2026-03-12T10:00:00+00:00",
                    "metadata": {
                        "_synthorg_category": "semantic",
                        PUBLISHER_KEY: "test-agent-001",
                    },
                },
            ],
        )

        from synthorg.core.enums import MemoryCategory

        query = MemoryQuery(
            text="test",
            categories=frozenset({MemoryCategory.SEMANTIC}),
        )
        entries = await backend.search_shared(query)
        assert len(entries) == 1
        assert entries[0].category == MemoryCategory.SEMANTIC

    async def test_search_shared_rejects_shared_namespace_exclude(
        self,
        backend: Mem0MemoryBackend,
    ) -> None:
        """search_shared() rejects exclude_agent == shared namespace."""
        with pytest.raises(
            MemoryRetrievalError,
            match="reserved shared namespace",
        ):
            await backend.search_shared(
                MemoryQuery(text="test"),
                exclude_agent=SHARED_NAMESPACE,
            )

    async def test_search_shared_no_publisher_uses_namespace(
        self,
        backend: Mem0MemoryBackend,
        mock_client: MagicMock,
    ) -> None:
        """Entries without publisher metadata use the shared namespace."""
        mock_client.search.return_value = mem0_search_result(
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
        assert entries[0].agent_id == SHARED_NAMESPACE


# ── Retract ──────────────────────────────────────────────────────


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
            "user_id": SHARED_NAMESPACE,
            "metadata": {PUBLISHER_KEY: "test-agent-001"},
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
            "user_id": SHARED_NAMESPACE,
            "metadata": {PUBLISHER_KEY: "test-agent-002"},
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
            "user_id": SHARED_NAMESPACE,
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

    @pytest.mark.parametrize(
        "exc_type",
        [builtins.MemoryError, RecursionError],
        ids=["MemoryError", "RecursionError"],
    )
    async def test_retract_reraises_system_error(
        self,
        backend: Mem0MemoryBackend,
        mock_client: MagicMock,
        exc_type: type[BaseException],
    ) -> None:
        """System errors are re-raised without wrapping."""
        mock_client.get.side_effect = exc_type("system failure")
        with pytest.raises(exc_type):
            await backend.retract("test-agent-001", "shared-001")

    async def test_retract_rejects_shared_namespace_agent_id(
        self,
        backend: Mem0MemoryBackend,
    ) -> None:
        """retract() rejects the shared namespace as agent_id."""
        with pytest.raises(MemoryStoreError, match="reserved shared namespace"):
            await backend.retract(SHARED_NAMESPACE, "shared-001")

    async def test_retract_not_shared_namespace_raises(
        self,
        backend: Mem0MemoryBackend,
        mock_client: MagicMock,
    ) -> None:
        """retract() rejects memories not in the shared namespace."""
        mock_client.get.return_value = {
            "id": "private-001",
            "memory": "private content",
            "created_at": "2026-03-12T10:00:00+00:00",
            "user_id": "test-agent-001",
            "metadata": {PUBLISHER_KEY: "test-agent-001"},
        }

        with pytest.raises(MemoryStoreError, match="not in the shared namespace"):
            await backend.retract("test-agent-001", "private-001")

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
            "user_id": SHARED_NAMESPACE,
            "metadata": {PUBLISHER_KEY: "test-agent-001"},
        }
        mock_client.delete.side_effect = RuntimeError("delete failed")

        with pytest.raises(MemoryStoreError, match="Failed to retract"):
            await backend.retract("test-agent-001", "shared-001")
