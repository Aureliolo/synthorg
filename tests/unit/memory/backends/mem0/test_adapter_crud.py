"""Tests for Mem0 adapter -- store, retrieve, get, delete, count."""

import builtins
from unittest.mock import MagicMock

import pytest

from synthorg.core.enums import MemoryCategory
from synthorg.memory.backends.mem0.adapter import Mem0MemoryBackend
from synthorg.memory.backends.mem0.mappers import (
    SHARED_NAMESPACE,
    validate_mem0_result,
)
from synthorg.memory.errors import (
    MemoryRetrievalError,
    MemoryStoreError,
)
from synthorg.memory.models import MemoryQuery

from .conftest import (
    make_store_request,
    mem0_add_result,
    mem0_get_result,
    mem0_search_result,
)

# ── Store ─────────────────────────────────────────────────────────


@pytest.mark.unit
class TestStore:
    async def test_store_success(
        self,
        backend: Mem0MemoryBackend,
        mock_client: MagicMock,
    ) -> None:
        mock_client.add.return_value = mem0_add_result("new-mem-id")

        memory_id = await backend.store(
            "test-agent-001",
            make_store_request(),
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
            await backend.store("test-agent-001", make_store_request())

    async def test_store_missing_id_raises(
        self,
        backend: Mem0MemoryBackend,
        mock_client: MagicMock,
    ) -> None:
        mock_client.add.return_value = {
            "results": [{"memory": "no id", "event": "ADD"}],
        }

        with pytest.raises(MemoryStoreError, match="missing or blank 'id'"):
            await backend.store("test-agent-001", make_store_request())

    async def test_store_exception_wraps(
        self,
        backend: Mem0MemoryBackend,
        mock_client: MagicMock,
    ) -> None:
        mock_client.add.side_effect = RuntimeError("disk full")

        with pytest.raises(MemoryStoreError, match="Failed to store") as exc_info:
            await backend.store("test-agent-001", make_store_request())

        assert exc_info.value.__cause__ is not None

    async def test_store_reraises_memory_error(
        self,
        backend: Mem0MemoryBackend,
        mock_client: MagicMock,
    ) -> None:
        """builtins.MemoryError is re-raised without wrapping."""
        mock_client.add.side_effect = builtins.MemoryError("out of memory")
        with pytest.raises(builtins.MemoryError):
            await backend.store("test-agent-001", make_store_request())

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
            await backend.store("test-agent-001", make_store_request())

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
            await backend.store("test-agent-001", make_store_request())

    async def test_store_non_list_results_raises(
        self,
        backend: Mem0MemoryBackend,
        mock_client: MagicMock,
    ) -> None:
        """Store result with non-list 'results' raises MemoryStoreError."""
        mock_client.add.return_value = {"results": "not-a-list"}
        with pytest.raises(MemoryStoreError, match="no results"):
            await backend.store("test-agent-001", make_store_request())

    async def test_store_rejects_shared_namespace_agent_id(
        self,
        backend: Mem0MemoryBackend,
        mock_client: MagicMock,
    ) -> None:
        """Storing with the shared namespace agent ID is rejected."""
        with pytest.raises(MemoryStoreError, match="reserved shared namespace"):
            await backend.store(SHARED_NAMESPACE, make_store_request())
        mock_client.add.assert_not_called()

    async def test_store_reraises_recursion_error(
        self,
        backend: Mem0MemoryBackend,
        mock_client: MagicMock,
    ) -> None:
        """RecursionError is re-raised without wrapping."""
        mock_client.add.side_effect = RecursionError("infinite loop")
        with pytest.raises(RecursionError):
            await backend.store("test-agent-001", make_store_request())


# ── Retrieve ──────────────────────────────────────────────────────


@pytest.mark.unit
class TestRetrieve:
    async def test_retrieve_with_text(
        self,
        backend: Mem0MemoryBackend,
        mock_client: MagicMock,
    ) -> None:
        mock_client.search.return_value = mem0_search_result()

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
        mock_client.get_all.return_value = mem0_search_result(
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
        mock_client.search.return_value = mem0_search_result(
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
        """builtins.MemoryError is re-raised without wrapping."""
        mock_client.search.side_effect = builtins.MemoryError("out of memory")
        with pytest.raises(builtins.MemoryError):
            await backend.retrieve(
                "test-agent-001",
                MemoryQuery(text="test"),
            )

    async def test_retrieve_reraises_recursion_error(
        self,
        backend: Mem0MemoryBackend,
        mock_client: MagicMock,
    ) -> None:
        """RecursionError is re-raised without wrapping."""
        mock_client.search.side_effect = RecursionError("infinite loop")
        with pytest.raises(RecursionError):
            await backend.retrieve(
                "test-agent-001",
                MemoryQuery(text="test"),
            )

    async def test_retrieve_rejects_shared_namespace_agent_id(
        self,
        backend: Mem0MemoryBackend,
    ) -> None:
        """retrieve() rejects the shared namespace with MemoryRetrievalError."""
        with pytest.raises(MemoryRetrievalError, match="reserved shared namespace"):
            await backend.retrieve(
                SHARED_NAMESPACE,
                MemoryQuery(text="test"),
            )

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


# ── Get ───────────────────────────────────────────────────────────


@pytest.mark.unit
class TestGet:
    async def test_get_existing(
        self,
        backend: Mem0MemoryBackend,
        mock_client: MagicMock,
    ) -> None:
        mock_client.get.return_value = mem0_get_result(
            "mem-001",
            user_id="test-agent-001",
        )

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

    async def test_get_reraises_memory_error(
        self,
        backend: Mem0MemoryBackend,
        mock_client: MagicMock,
    ) -> None:
        """builtins.MemoryError is re-raised without wrapping in get()."""
        mock_client.get.side_effect = builtins.MemoryError("out of memory")
        with pytest.raises(builtins.MemoryError):
            await backend.get("test-agent-001", "mem-001")

    async def test_get_reraises_recursion_error(
        self,
        backend: Mem0MemoryBackend,
        mock_client: MagicMock,
    ) -> None:
        """RecursionError is re-raised without wrapping in get()."""
        mock_client.get.side_effect = RecursionError("infinite loop")
        with pytest.raises(RecursionError):
            await backend.get("test-agent-001", "mem-001")

    async def test_get_rejects_shared_namespace_agent_id(
        self,
        backend: Mem0MemoryBackend,
    ) -> None:
        """get() rejects the shared namespace with MemoryRetrievalError."""
        with pytest.raises(MemoryRetrievalError, match="reserved shared namespace"):
            await backend.get(SHARED_NAMESPACE, "mem-001")

    async def test_get_ownership_mismatch_returns_none(
        self,
        backend: Mem0MemoryBackend,
        mock_client: MagicMock,
    ) -> None:
        """get() returns None when user_id doesn't match agent_id."""
        mock_client.get.return_value = mem0_get_result(
            "mem-001",
            user_id="other-agent",
        )

        entry = await backend.get("test-agent-001", "mem-001")
        assert entry is None

    async def test_get_orphan_returns_none(
        self,
        backend: Mem0MemoryBackend,
        mock_client: MagicMock,
    ) -> None:
        """get() returns None when memory has no user_id (orphan)."""
        mock_client.get.return_value = mem0_get_result("mem-001")

        entry = await backend.get("test-agent-001", "mem-001")
        assert entry is None


# ── Delete ────────────────────────────────────────────────────────


@pytest.mark.unit
class TestDelete:
    async def test_delete_existing(
        self,
        backend: Mem0MemoryBackend,
        mock_client: MagicMock,
    ) -> None:
        mock_client.get.return_value = mem0_get_result(
            "mem-001",
            user_id="test-agent-001",
        )
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
        mock_client.get.return_value = mem0_get_result(
            "mem-001",
            user_id="test-agent-001",
        )
        mock_client.delete.side_effect = RuntimeError("delete failed")

        with pytest.raises(MemoryStoreError, match="Failed to delete"):
            await backend.delete("test-agent-001", "mem-001")

    async def test_delete_reraises_memory_error(
        self,
        backend: Mem0MemoryBackend,
        mock_client: MagicMock,
    ) -> None:
        """builtins.MemoryError is re-raised without wrapping in delete()."""
        mock_client.get.side_effect = builtins.MemoryError("out of memory")
        with pytest.raises(builtins.MemoryError):
            await backend.delete("test-agent-001", "mem-001")

    async def test_delete_reraises_recursion_error(
        self,
        backend: Mem0MemoryBackend,
        mock_client: MagicMock,
    ) -> None:
        """RecursionError is re-raised without wrapping in delete()."""
        mock_client.get.side_effect = RecursionError("infinite loop")
        with pytest.raises(RecursionError):
            await backend.delete("test-agent-001", "mem-001")

    async def test_delete_shared_namespace_entry_raises(
        self,
        backend: Mem0MemoryBackend,
        mock_client: MagicMock,
    ) -> None:
        """delete() rejects entries belonging to the shared namespace."""
        mock_client.get.return_value = mem0_get_result(
            "mem-001",
            user_id=SHARED_NAMESPACE,
        )

        with pytest.raises(MemoryStoreError, match="shared namespace"):
            await backend.delete("test-agent-001", "mem-001")

    async def test_delete_ownership_mismatch_raises(
        self,
        backend: Mem0MemoryBackend,
        mock_client: MagicMock,
    ) -> None:
        """delete() rejects when user_id doesn't match agent_id."""
        mock_client.get.return_value = mem0_get_result(
            "mem-001",
            user_id="other-agent",
        )

        with pytest.raises(MemoryStoreError, match="cannot delete"):
            await backend.delete("test-agent-001", "mem-001")

    async def test_delete_orphan_raises(
        self,
        backend: Mem0MemoryBackend,
        mock_client: MagicMock,
    ) -> None:
        """delete() raises when memory has no user_id (orphan)."""
        mock_client.get.return_value = mem0_get_result("mem-001")

        with pytest.raises(MemoryStoreError, match="unverifiable"):
            await backend.delete("test-agent-001", "mem-001")
        mock_client.delete.assert_not_called()

    async def test_delete_rejects_shared_namespace_agent_id(
        self,
        backend: Mem0MemoryBackend,
    ) -> None:
        """delete() rejects the shared namespace as agent_id."""
        with pytest.raises(MemoryStoreError, match="reserved shared namespace"):
            await backend.delete(SHARED_NAMESPACE, "mem-001")


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

    async def test_count_rejects_shared_namespace_agent_id(
        self,
        backend: Mem0MemoryBackend,
    ) -> None:
        """count() rejects the shared namespace with MemoryRetrievalError."""
        with pytest.raises(MemoryRetrievalError, match="reserved shared namespace"):
            await backend.count(SHARED_NAMESPACE)

    async def test_count_exception_wraps(
        self,
        backend: Mem0MemoryBackend,
        mock_client: MagicMock,
    ) -> None:
        mock_client.get_all.side_effect = RuntimeError("fail")

        with pytest.raises(MemoryRetrievalError, match="Failed to count"):
            await backend.count("test-agent-001")

    async def test_count_empty_results(
        self,
        backend: Mem0MemoryBackend,
        mock_client: MagicMock,
    ) -> None:
        """Count returns 0 for empty results."""
        mock_client.get_all.return_value = {"results": []}
        count = await backend.count("test-agent-001")
        assert count == 0

    async def test_count_truncation_warning(
        self,
        backend: Mem0MemoryBackend,
        mock_client: MagicMock,
    ) -> None:
        """count() returns the count even when truncated at max_memories_per_agent."""
        # backend fixture has max_memories_per_agent=100
        items = [
            {"id": f"m{i}", "memory": f"content-{i}", "metadata": {}}
            for i in range(100)
        ]
        mock_client.get_all.return_value = {"results": items}

        count = await backend.count("test-agent-001")
        # Truncation should still return a valid count
        assert count == 100

    async def test_count_reraises_memory_error(
        self,
        backend: Mem0MemoryBackend,
        mock_client: MagicMock,
    ) -> None:
        """builtins.MemoryError is re-raised without wrapping in count()."""
        mock_client.get_all.side_effect = builtins.MemoryError("out of memory")
        with pytest.raises(builtins.MemoryError):
            await backend.count("test-agent-001")

    async def test_count_reraises_recursion_error(
        self,
        backend: Mem0MemoryBackend,
        mock_client: MagicMock,
    ) -> None:
        """RecursionError is re-raised without wrapping in count()."""
        mock_client.get_all.side_effect = RecursionError("infinite loop")
        with pytest.raises(RecursionError):
            await backend.count("test-agent-001")


# ── validate_mem0_result ────────────────────────────────────────


@pytest.mark.unit
class TestValidateMem0Result:
    def test_non_dict_raises(self) -> None:
        """Non-dict response raises MemoryRetrievalError."""
        with pytest.raises(MemoryRetrievalError, match="Unexpected Mem0 response type"):
            validate_mem0_result("not-a-dict", context="test")

    def test_missing_results_key_raises(self) -> None:
        """Dict without 'results' key raises MemoryRetrievalError."""
        with pytest.raises(MemoryRetrievalError, match="missing 'results' key"):
            validate_mem0_result({"data": []}, context="test")

    def test_non_list_results_raises(self) -> None:
        """Non-list 'results' value raises MemoryRetrievalError."""
        with pytest.raises(MemoryRetrievalError, match="Unexpected Mem0 results type"):
            validate_mem0_result({"results": "not-a-list"}, context="test")

    def test_valid_response(self) -> None:
        """Valid response returns the results list."""
        items = [{"id": "m1"}]
        result = validate_mem0_result({"results": items}, context="test")
        assert result == items

    def test_empty_results(self) -> None:
        """Empty results list is valid."""
        result = validate_mem0_result({"results": []}, context="test")
        assert result == []

    def test_none_raises(self) -> None:
        """None response raises MemoryRetrievalError."""
        with pytest.raises(MemoryRetrievalError, match="Unexpected Mem0 response type"):
            validate_mem0_result(None, context="test")
