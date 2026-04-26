"""Shared builders + constants for the perf-benchmark suite.

Lives outside ``conftest.py`` because conftest auto-injects fixtures
into the test environment but isn't an importable module path. Test
modules import builders + constants from here directly.

All builders are deterministic. Vendor-agnostic naming per
CLAUDE.md §Testing.
"""

from datetime import UTC, datetime, timedelta
from typing import Final

from synthorg.budget.cost_record import CostRecord
from synthorg.core.enums import MemoryCategory
from synthorg.memory.models import MemoryEntry, MemoryMetadata
from synthorg.memory.retrieval_config import MemoryRetrievalConfig

# Reference instant: pinned so no fixture depends on wall-clock time.
NOW: Final[datetime] = datetime(2026, 1, 1, tzinfo=UTC)

RETRIEVAL_CONFIG: Final[MemoryRetrievalConfig] = MemoryRetrievalConfig(
    relevance_weight=0.7,
    recency_weight=0.3,
    recency_decay_rate=0.01,
    personal_boost=0.1,
    min_relevance=0.3,
    max_memories=20,
)


def make_memory_entry(
    idx: int,
    *,
    age_hours: float = 0.0,
    relevance: float = 0.8,
    content: str | None = None,
) -> MemoryEntry:
    """Build a :class:`MemoryEntry` with deterministic fields."""
    return MemoryEntry(
        id=f"mem-{idx:04d}",
        agent_id="agent-bench",
        category=MemoryCategory.EPISODIC,
        content=content or f"Memory entry number {idx} with some benchmark content",
        metadata=MemoryMetadata(
            source="benchmark",
            confidence=0.9,
            tags=("bench",),
        ),
        created_at=NOW - timedelta(hours=age_hours),
        relevance_score=relevance,
    )


def make_cost_record(  # noqa: PLR0913
    idx: int,
    *,
    agent_id: str = "agent-1",
    cost: float = 0.05,
    input_tokens: int = 500,
    output_tokens: int = 200,
    hours_ago: float = 0.0,
) -> CostRecord:
    """Build a :class:`CostRecord` with deterministic fields.

    Provider/model names are vendor-agnostic per CLAUDE.md §Testing.
    """
    return CostRecord(
        agent_id=agent_id,
        task_id=f"task-{idx:04d}",
        provider="test-provider",
        model="test-small-001",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost=cost,
        currency="USD",
        timestamp=NOW - timedelta(hours=hours_ago),
    )
