"""Integration tests for Mem0 backend with retrieval pipeline.

Tests the adapter plugged into the retrieval pipeline (ranking +
context injection) using a mocked Mem0 client — validates the full
store -> retrieve -> rank -> format flow.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from synthorg.core.enums import MemoryCategory
from synthorg.memory.backends.mem0.adapter import Mem0MemoryBackend
from synthorg.memory.backends.mem0.config import (
    Mem0BackendConfig,
    Mem0EmbedderConfig,
)
from synthorg.memory.backends.mem0.mappers import PUBLISHER_KEY, SHARED_NAMESPACE
from synthorg.memory.models import MemoryQuery, MemoryStoreRequest
from synthorg.memory.retrieval_config import MemoryRetrievalConfig
from synthorg.memory.retriever import ContextInjectionStrategy

pytestmark = pytest.mark.timeout(30)


def _test_embedder() -> Mem0EmbedderConfig:
    """Vendor-agnostic embedder config for tests."""
    return Mem0EmbedderConfig(
        provider="test-provider",
        model="test-embedding-001",
    )


@pytest.fixture
def mock_client() -> MagicMock:
    """Mock Mem0 Memory client."""
    return MagicMock()


@pytest.fixture
def backend(mock_client: MagicMock) -> Mem0MemoryBackend:
    """Connected Mem0 backend with mocked client."""
    config = Mem0BackendConfig(
        data_dir="/tmp/test-integration",  # noqa: S108
        embedder=_test_embedder(),
    )
    b = Mem0MemoryBackend(mem0_config=config, max_memories_per_agent=100)
    b._client = mock_client
    b._connected = True
    return b


@pytest.mark.integration
class TestMem0RetrievalPipeline:
    """Test adapter integrated with the retrieval pipeline."""

    async def test_store_then_retrieve(
        self,
        backend: Mem0MemoryBackend,
        mock_client: MagicMock,
    ) -> None:
        """Store a memory, then retrieve it via semantic search."""
        mock_client.add.return_value = {
            "results": [
                {
                    "id": "mem-int-001",
                    "memory": "project uses Litestar",
                    "event": "ADD",
                },
            ],
        }
        mock_client.search.return_value = {
            "results": [
                {
                    "id": "mem-int-001",
                    "memory": "project uses Litestar",
                    "score": 0.92,
                    "created_at": datetime.now(UTC).isoformat(),
                    "metadata": {
                        "_synthorg_category": "semantic",
                        "_synthorg_confidence": 1.0,
                    },
                },
            ],
        }

        # Store
        memory_id = await backend.store(
            "test-agent-001",
            MemoryStoreRequest(
                category=MemoryCategory.SEMANTIC,
                content="project uses Litestar",
            ),
        )
        assert memory_id == "mem-int-001"

        # Retrieve
        entries = await backend.retrieve(
            "test-agent-001",
            MemoryQuery(text="what framework", limit=5),
        )
        assert len(entries) == 1
        assert entries[0].content == "project uses Litestar"
        assert entries[0].relevance_score == 0.92

    async def test_pipeline_prepare_messages(
        self,
        backend: Mem0MemoryBackend,
        mock_client: MagicMock,
    ) -> None:
        """Full pipeline: retrieve -> rank -> format via ContextInjectionStrategy."""
        now = datetime.now(UTC)
        mock_client.search.return_value = {
            "results": [
                {
                    "id": "m1",
                    "memory": "agent prefers concise responses",
                    "score": 0.88,
                    "created_at": (now - timedelta(hours=1)).isoformat(),
                    "metadata": {
                        "_synthorg_category": "procedural",
                        "_synthorg_confidence": 0.9,
                    },
                },
                {
                    "id": "m2",
                    "memory": "last task was code review",
                    "score": 0.75,
                    "created_at": (now - timedelta(hours=24)).isoformat(),
                    "metadata": {
                        "_synthorg_category": "episodic",
                        "_synthorg_confidence": 0.8,
                    },
                },
            ],
        }

        config = MemoryRetrievalConfig(
            relevance_weight=0.7,
            recency_weight=0.3,
            min_relevance=0.1,
            max_memories=10,
        )
        strategy = ContextInjectionStrategy(
            backend=backend,
            config=config,
        )

        messages = await strategy.prepare_messages(
            agent_id="test-agent-001",
            query_text="what should I remember",
            token_budget=500,
        )

        # Should produce at least one message with memory context
        assert len(messages) >= 1
        # Content should include both memories (they pass min_relevance)
        combined = " ".join(m.content for m in messages if m.content)
        assert "concise responses" in combined
        assert "code review" in combined

    async def test_shared_knowledge_flow(
        self,
        backend: Mem0MemoryBackend,
        mock_client: MagicMock,
    ) -> None:
        """Publish -> search_shared -> retract flow."""
        # Publish
        mock_client.add.return_value = {
            "results": [
                {
                    "id": "shared-001",
                    "memory": "company policy",
                    "event": "ADD",
                },
            ],
        }

        shared_id = await backend.publish(
            "test-agent-001",
            MemoryStoreRequest(
                category=MemoryCategory.SEMANTIC,
                content="company policy: always test code",
            ),
        )
        assert shared_id == "shared-001"

        # Search shared
        mock_client.search.return_value = {
            "results": [
                {
                    "id": "shared-001",
                    "memory": "company policy: always test code",
                    "score": 0.95,
                    "created_at": datetime.now(UTC).isoformat(),
                    "metadata": {
                        "_synthorg_category": "semantic",
                        PUBLISHER_KEY: "test-agent-001",
                    },
                },
            ],
        }

        entries = await backend.search_shared(
            MemoryQuery(text="company policy"),
        )
        assert len(entries) == 1
        assert entries[0].agent_id == "test-agent-001"

        # Retract
        mock_client.get.return_value = {
            "id": "shared-001",
            "memory": "company policy: always test code",
            "created_at": datetime.now(UTC).isoformat(),
            "user_id": SHARED_NAMESPACE,
            "metadata": {PUBLISHER_KEY: "test-agent-001"},
        }
        mock_client.delete.return_value = None

        retracted = await backend.retract("test-agent-001", "shared-001")
        assert retracted is True

    async def test_shared_search_excludes_agent(
        self,
        backend: Mem0MemoryBackend,
        mock_client: MagicMock,
    ) -> None:
        """Search shared knowledge excluding the requesting agent."""
        mock_client.search.return_value = {
            "results": [
                {
                    "id": "s1",
                    "memory": "from agent 1",
                    "score": 0.9,
                    "created_at": datetime.now(UTC).isoformat(),
                    "metadata": {PUBLISHER_KEY: "test-agent-001"},
                },
                {
                    "id": "s2",
                    "memory": "from agent 2",
                    "score": 0.85,
                    "created_at": datetime.now(UTC).isoformat(),
                    "metadata": {PUBLISHER_KEY: "test-agent-002"},
                },
            ],
        }

        entries = await backend.search_shared(
            MemoryQuery(text="knowledge"),
            exclude_agent="test-agent-001",
        )
        assert len(entries) == 1
        assert entries[0].agent_id == "test-agent-002"

    async def test_count_after_store(
        self,
        backend: Mem0MemoryBackend,
        mock_client: MagicMock,
    ) -> None:
        """Count memories after storing several entries."""
        mock_client.get_all.return_value = {
            "results": [
                {
                    "id": "m1",
                    "memory": "first",
                    "metadata": {"_synthorg_category": "episodic"},
                },
                {
                    "id": "m2",
                    "memory": "second",
                    "metadata": {"_synthorg_category": "semantic"},
                },
                {
                    "id": "m3",
                    "memory": "third",
                    "metadata": {"_synthorg_category": "episodic"},
                },
            ],
        }

        total = await backend.count("test-agent-001")
        assert total == 3

        episodic_count = await backend.count(
            "test-agent-001",
            category=MemoryCategory.EPISODIC,
        )
        assert episodic_count == 2
