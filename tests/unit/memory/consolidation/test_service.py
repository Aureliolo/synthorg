"""Tests for MemoryConsolidationService."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from ai_company.core.enums import MemoryCategory
from ai_company.memory.consolidation.config import (
    ArchivalConfig,
    ConsolidationConfig,
    RetentionConfig,
)
from ai_company.memory.consolidation.models import ConsolidationResult
from ai_company.memory.consolidation.service import MemoryConsolidationService
from ai_company.memory.models import MemoryEntry, MemoryMetadata

pytestmark = pytest.mark.timeout(30)

_NOW = datetime.now(UTC)
_AGENT_ID = "test-agent"


def _make_entry(entry_id: str) -> MemoryEntry:
    return MemoryEntry(
        id=entry_id,
        agent_id=_AGENT_ID,
        category=MemoryCategory.EPISODIC,
        content=f"Content {entry_id}",
        metadata=MemoryMetadata(),
        created_at=_NOW - timedelta(hours=1),
    )


def _make_backend_mock(
    entries: tuple[MemoryEntry, ...] = (),
    count: int = 0,
) -> AsyncMock:
    backend = AsyncMock()
    backend.retrieve = AsyncMock(return_value=entries)
    backend.delete = AsyncMock(return_value=True)
    backend.count = AsyncMock(return_value=count)
    return backend


@pytest.mark.unit
class TestRunConsolidation:
    """run_consolidation behaviour."""

    async def test_no_strategy_returns_empty(self) -> None:
        backend = _make_backend_mock()
        config = ConsolidationConfig()
        service = MemoryConsolidationService(
            backend=backend,
            config=config,
            strategy=None,
        )
        result = await service.run_consolidation(_AGENT_ID)
        assert result.consolidated_count == 0

    async def test_run_consolidation_skipped_when_no_strategy(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        backend = _make_backend_mock()
        config = ConsolidationConfig()
        service = MemoryConsolidationService(
            backend=backend,
            config=config,
            strategy=None,
        )
        result = await service.run_consolidation(_AGENT_ID)
        assert result.consolidated_count == 0
        assert result.removed_ids == ()
        assert result.summary_id is None
        captured = capsys.readouterr()
        assert "consolidation.run.skipped" in captured.out

    async def test_with_strategy(self) -> None:
        entries = (_make_entry("m1"), _make_entry("m2"))
        backend = _make_backend_mock(entries=entries)

        strategy = AsyncMock()
        strategy.consolidate = AsyncMock(
            return_value=ConsolidationResult(
                consolidated_count=1,
                removed_ids=("m1",),
                summary_id="summary-1",
            ),
        )

        config = ConsolidationConfig()
        service = MemoryConsolidationService(
            backend=backend,
            config=config,
            strategy=strategy,
        )
        result = await service.run_consolidation(_AGENT_ID)
        assert result.consolidated_count == 1
        assert result.summary_id == "summary-1"

    async def test_run_consolidation_exception_propagates(self) -> None:
        entries = (_make_entry("m1"),)
        backend = _make_backend_mock(entries=entries)

        strategy = AsyncMock()
        strategy.consolidate = AsyncMock(
            side_effect=RuntimeError("strategy failure"),
        )

        config = ConsolidationConfig()
        service = MemoryConsolidationService(
            backend=backend,
            config=config,
            strategy=strategy,
        )
        with pytest.raises(RuntimeError, match="strategy failure"):
            await service.run_consolidation(_AGENT_ID)

    async def test_with_archival(self) -> None:
        entries = (_make_entry("m1"), _make_entry("m2"))
        backend = _make_backend_mock(entries=entries)

        strategy = AsyncMock()
        strategy.consolidate = AsyncMock(
            return_value=ConsolidationResult(
                consolidated_count=1,
                removed_ids=("m1",),
            ),
        )

        archival = AsyncMock()
        archival.archive = AsyncMock(return_value="arch-1")

        config = ConsolidationConfig(
            archival=ArchivalConfig(enabled=True),
        )
        service = MemoryConsolidationService(
            backend=backend,
            config=config,
            strategy=strategy,
            archival_store=archival,
        )
        result = await service.run_consolidation(_AGENT_ID)
        assert result.archived_count == 1
        archival.archive.assert_called_once()


@pytest.mark.unit
class TestEnforceMaxMemories:
    """enforce_max_memories behaviour."""

    async def test_under_limit(self) -> None:
        backend = _make_backend_mock(count=5)
        config = ConsolidationConfig(max_memories_per_agent=100)
        service = MemoryConsolidationService(
            backend=backend,
            config=config,
        )
        deleted = await service.enforce_max_memories(_AGENT_ID)
        assert deleted == 0

    async def test_over_limit_deletes_excess(self) -> None:
        entries = tuple(_make_entry(f"m{i}") for i in range(3))
        backend = _make_backend_mock(entries=entries, count=13)
        config = ConsolidationConfig(max_memories_per_agent=10)
        service = MemoryConsolidationService(
            backend=backend,
            config=config,
        )
        deleted = await service.enforce_max_memories(_AGENT_ID)
        assert deleted == 3

    async def test_enforce_max_memories_delete_returns_false(self) -> None:
        entries = tuple(_make_entry(f"m{i}") for i in range(3))
        backend = _make_backend_mock(entries=entries, count=13)
        backend.delete = AsyncMock(side_effect=[True, False, True])

        config = ConsolidationConfig(max_memories_per_agent=10)
        service = MemoryConsolidationService(
            backend=backend,
            config=config,
        )
        deleted = await service.enforce_max_memories(_AGENT_ID)
        assert deleted == 2


@pytest.mark.unit
class TestCleanupRetention:
    """cleanup_retention delegates to RetentionEnforcer."""

    async def test_delegates_to_retention(self) -> None:
        backend = AsyncMock()
        backend.retrieve = AsyncMock(return_value=())
        config = ConsolidationConfig(
            retention=RetentionConfig(),
        )
        service = MemoryConsolidationService(
            backend=backend,
            config=config,
        )
        deleted = await service.cleanup_retention(_AGENT_ID)
        assert deleted == 0


@pytest.mark.unit
class TestRunMaintenance:
    """run_maintenance orchestrates all steps."""

    async def test_full_maintenance(self) -> None:
        backend = _make_backend_mock(count=5)
        strategy = AsyncMock()
        strategy.consolidate = AsyncMock(
            return_value=ConsolidationResult(consolidated_count=0),
        )

        config = ConsolidationConfig(max_memories_per_agent=100)
        service = MemoryConsolidationService(
            backend=backend,
            config=config,
            strategy=strategy,
        )
        result = await service.run_maintenance(_AGENT_ID)
        assert result.consolidated_count == 0
        strategy.consolidate.assert_called_once()
