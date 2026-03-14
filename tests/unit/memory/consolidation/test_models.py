"""Tests for consolidation domain models."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from synthorg.core.enums import MemoryCategory
from synthorg.memory.consolidation.models import (
    ArchivalEntry,
    ConsolidationResult,
    RetentionRule,
)
from synthorg.memory.models import MemoryMetadata

pytestmark = pytest.mark.timeout(30)

_NOW = datetime.now(UTC)


@pytest.mark.unit
class TestConsolidationResult:
    """ConsolidationResult creation and validation."""

    def test_minimal(self) -> None:
        result = ConsolidationResult()
        assert result.consolidated_count == 0
        assert result.removed_ids == ()
        assert result.summary_id is None
        assert result.archived_count == 0

    def test_full(self) -> None:
        result = ConsolidationResult(
            removed_ids=("m1", "m2", "m3"),
            summary_id="summary-1",
            archived_count=2,
        )
        assert result.consolidated_count == 3
        assert len(result.removed_ids) == 3

    def test_consolidated_count_is_computed(self) -> None:
        """consolidated_count always equals len(removed_ids)."""
        result = ConsolidationResult(removed_ids=("a", "b"))
        assert result.consolidated_count == 2

    def test_frozen(self) -> None:
        result = ConsolidationResult()
        with pytest.raises(ValidationError):
            result.removed_ids = ("x",)  # type: ignore[misc]


@pytest.mark.unit
class TestArchivalEntry:
    """ArchivalEntry creation and validation."""

    def test_valid_entry(self) -> None:
        entry = ArchivalEntry(
            original_id="mem-1",
            agent_id="agent-1",
            content="Some memory",
            category=MemoryCategory.EPISODIC,
            created_at=_NOW,
            archived_at=_NOW,
        )
        assert entry.original_id == "mem-1"
        assert entry.category == MemoryCategory.EPISODIC

    def test_with_metadata(self) -> None:
        entry = ArchivalEntry(
            original_id="mem-1",
            agent_id="agent-1",
            content="Memory with meta",
            category=MemoryCategory.SEMANTIC,
            metadata=MemoryMetadata(source="task-123", confidence=0.9),
            created_at=_NOW,
            archived_at=_NOW,
        )
        assert entry.metadata.source == "task-123"

    def test_frozen(self) -> None:
        entry = ArchivalEntry(
            original_id="mem-1",
            agent_id="agent-1",
            content="test",
            category=MemoryCategory.WORKING,
            created_at=_NOW,
            archived_at=_NOW,
        )
        with pytest.raises(ValidationError):
            entry.content = "changed"  # type: ignore[misc]

    def test_archival_entry_archived_before_created_rejected(self) -> None:
        created = _NOW
        archived = _NOW - timedelta(hours=1)
        with pytest.raises(
            ValidationError,
            match=r"archived_at.*must be >= created_at",
        ):
            ArchivalEntry(
                original_id="mem-1",
                agent_id="agent-1",
                content="test",
                category=MemoryCategory.EPISODIC,
                created_at=created,
                archived_at=archived,
            )


@pytest.mark.unit
class TestRetentionRule:
    """RetentionRule creation and validation."""

    def test_valid_rule(self) -> None:
        rule = RetentionRule(
            category=MemoryCategory.WORKING,
            retention_days=30,
        )
        assert rule.category == MemoryCategory.WORKING
        assert rule.retention_days == 30

    def test_zero_days_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RetentionRule(
                category=MemoryCategory.WORKING,
                retention_days=0,
            )

    def test_frozen(self) -> None:
        rule = RetentionRule(
            category=MemoryCategory.WORKING,
            retention_days=30,
        )
        with pytest.raises(ValidationError):
            rule.retention_days = 60  # type: ignore[misc]
