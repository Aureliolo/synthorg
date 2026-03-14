"""Shared fixtures for Mem0 adapter tests."""

from typing import Any
from unittest.mock import MagicMock

import pytest

from synthorg.core.enums import MemoryCategory
from synthorg.memory.backends.mem0.adapter import Mem0MemoryBackend
from synthorg.memory.backends.mem0.config import (
    Mem0BackendConfig,
    Mem0EmbedderConfig,
)
from synthorg.memory.models import MemoryStoreRequest


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


def mem0_add_result(memory_id: str = "mem-001") -> dict[str, Any]:
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


def mem0_search_result(
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


def mem0_get_result(
    memory_id: str = "mem-001",
    *,
    user_id: str | None = None,
) -> dict[str, Any]:
    """Build a typical Mem0 get() return value.

    Args:
        memory_id: Memory identifier.
        user_id: Optional owner ``user_id`` for ownership tests.
    """
    result: dict[str, Any] = {
        "id": memory_id,
        "memory": "stored content",
        "created_at": "2026-03-12T10:00:00+00:00",
        "updated_at": None,
        "metadata": {
            "_synthorg_category": "episodic",
            "_synthorg_confidence": 1.0,
        },
    }
    if user_id is not None:
        result["user_id"] = user_id
    return result


def make_store_request(
    *,
    category: MemoryCategory = MemoryCategory.EPISODIC,
    content: str = "test content",
) -> MemoryStoreRequest:
    """Helper to build a store request."""
    return MemoryStoreRequest(category=category, content=content)
