"""Tests for consolidation domain models."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from synthorg.core.memory_enums import MemoryCategory
from synthorg.memory.consolidation.models import (
    ArchivalEntry,
    ArchivalIndexEntry,
    ArchivalMode,
    ArchivalModeAssignment,
    ConsolidationResult,
    RetentionRule,
)
from synthorg.memory.models import MemoryMetadata

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
            summary_ids=("summary-1",),
            archived_count=2,
        )
        assert result.consolidated_count == 3
        assert len(result.removed_ids) == 3
        assert result.summary_id == "summary-1"
        assert result.summary_ids == ("summary-1",)

    def test_multi_summary(self) -> None:
        """``summary_id`` resolves to the last summary when multiple are produced."""
        result = ConsolidationResult(
            removed_ids=("m1", "m2", "m3"),
            summary_ids=("sum-a", "sum-b", "sum-c"),
        )
        assert result.summary_id == "sum-c"
        assert result.summary_ids == ("sum-a", "sum-b", "sum-c")

    def test_summary_ids_reject_duplicates(self) -> None:
        with pytest.raises(ValidationError, match="summary_ids contains duplicates"):
            ConsolidationResult(
                removed_ids=("m1",),
                summary_ids=("dup", "dup"),
            )

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
            archival_mode=ArchivalMode.EXTRACTIVE,
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
            archival_mode=ArchivalMode.EXTRACTIVE,
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
            archival_mode=ArchivalMode.EXTRACTIVE,
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
                archival_mode=ArchivalMode.EXTRACTIVE,
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


@pytest.mark.unit
class TestArchivalMode:
    """ArchivalMode enum values and StrEnum behaviour."""

    def test_values(self) -> None:
        assert ArchivalMode.ABSTRACTIVE.value == "abstractive"
        assert ArchivalMode.EXTRACTIVE.value == "extractive"

    def test_is_str(self) -> None:
        assert isinstance(ArchivalMode.ABSTRACTIVE, str)

    def test_all_members(self) -> None:
        assert set(ArchivalMode) == {
            ArchivalMode.ABSTRACTIVE,
            ArchivalMode.EXTRACTIVE,
        }


@pytest.mark.unit
class TestArchivalModeAssignment:
    """ArchivalModeAssignment creation and validation."""

    def test_valid(self) -> None:
        assignment = ArchivalModeAssignment(
            original_id="mem-1",
            mode=ArchivalMode.EXTRACTIVE,
        )
        assert assignment.original_id == "mem-1"
        assert assignment.mode == ArchivalMode.EXTRACTIVE

    def test_frozen(self) -> None:
        assignment = ArchivalModeAssignment(
            original_id="mem-1",
            mode=ArchivalMode.ABSTRACTIVE,
        )
        with pytest.raises(ValidationError):
            assignment.mode = ArchivalMode.EXTRACTIVE  # type: ignore[misc]

    def test_blank_id_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ArchivalModeAssignment(
                original_id="   ",
                mode=ArchivalMode.ABSTRACTIVE,
            )


@pytest.mark.unit
class TestArchivalIndexEntry:
    """ArchivalIndexEntry creation and validation."""

    def test_valid(self) -> None:
        entry = ArchivalIndexEntry(
            original_id="mem-1",
            archival_id="arch-1",
            mode=ArchivalMode.EXTRACTIVE,
        )
        assert entry.original_id == "mem-1"
        assert entry.archival_id == "arch-1"
        assert entry.mode == ArchivalMode.EXTRACTIVE

    def test_frozen(self) -> None:
        entry = ArchivalIndexEntry(
            original_id="mem-1",
            archival_id="arch-1",
            mode=ArchivalMode.ABSTRACTIVE,
        )
        with pytest.raises(ValidationError):
            entry.archival_id = "arch-2"  # type: ignore[misc]


@pytest.mark.unit
class TestConsolidationResultDualMode:
    """ConsolidationResult new dual-mode fields."""

    def test_backward_compatible_defaults(self) -> None:
        """Existing code creating ConsolidationResult() still works."""
        result = ConsolidationResult()
        assert result.mode_assignments == ()
        assert result.archival_index == ()

    def test_with_mode_assignments(self) -> None:
        assignments = (
            ArchivalModeAssignment(
                original_id="m1",
                mode=ArchivalMode.ABSTRACTIVE,
            ),
            ArchivalModeAssignment(
                original_id="m2",
                mode=ArchivalMode.EXTRACTIVE,
            ),
        )
        result = ConsolidationResult(
            removed_ids=("m1", "m2"),
            mode_assignments=assignments,
        )
        assert len(result.mode_assignments) == 2
        assert result.mode_assignments[0].mode == ArchivalMode.ABSTRACTIVE

    def test_duplicate_removed_ids_rejected(self) -> None:
        with pytest.raises(
            ValidationError,
            match="removed_ids contains duplicates",
        ):
            ConsolidationResult(
                removed_ids=("m1", "m1"),
            )

    def test_archival_index_exceeds_count_rejected(self) -> None:
        with pytest.raises(
            ValidationError,
            match="must not exceed archived_count",
        ):
            ConsolidationResult(
                removed_ids=("m1",),
                archived_count=0,
                archival_index=(
                    ArchivalIndexEntry(
                        original_id="m1",
                        archival_id="a1",
                        mode=ArchivalMode.EXTRACTIVE,
                    ),
                ),
            )

    def test_archived_count_exceeds_consolidated_rejected(self) -> None:
        with pytest.raises(
            ValidationError,
            match="must not exceed consolidated_count",
        ):
            ConsolidationResult(
                removed_ids=("m1",),
                archived_count=5,
            )

    def test_archival_index_id_not_in_removed_rejected(self) -> None:
        with pytest.raises(
            ValidationError,
            match="not in removed_ids",
        ):
            ConsolidationResult(
                removed_ids=("m1",),
                archived_count=1,
                archival_index=(
                    ArchivalIndexEntry(
                        original_id="m99",
                        archival_id="a1",
                        mode=ArchivalMode.ABSTRACTIVE,
                    ),
                ),
            )

    def test_mode_assignments_exceeds_removed_rejected(self) -> None:
        with pytest.raises(
            ValidationError,
            match="must not exceed removed_ids length",
        ):
            ConsolidationResult(
                removed_ids=("m1",),
                mode_assignments=(
                    ArchivalModeAssignment(
                        original_id="m1",
                        mode=ArchivalMode.ABSTRACTIVE,
                    ),
                    ArchivalModeAssignment(
                        original_id="m2",
                        mode=ArchivalMode.EXTRACTIVE,
                    ),
                ),
            )

    def test_mode_assignments_duplicate_ids_rejected(self) -> None:
        with pytest.raises(
            ValidationError,
            match="mode_assignments contains duplicate",
        ):
            ConsolidationResult(
                removed_ids=("m1", "m2"),
                mode_assignments=(
                    ArchivalModeAssignment(
                        original_id="m1",
                        mode=ArchivalMode.ABSTRACTIVE,
                    ),
                    ArchivalModeAssignment(
                        original_id="m1",
                        mode=ArchivalMode.EXTRACTIVE,
                    ),
                ),
            )

    def test_mode_assignments_unknown_id_rejected(self) -> None:
        with pytest.raises(
            ValidationError,
            match="not in removed_ids",
        ):
            ConsolidationResult(
                removed_ids=("m1",),
                mode_assignments=(
                    ArchivalModeAssignment(
                        original_id="m99",
                        mode=ArchivalMode.ABSTRACTIVE,
                    ),
                ),
            )

    def test_with_archival_index(self) -> None:
        index = (
            ArchivalIndexEntry(
                original_id="m1",
                archival_id="arch-1",
                mode=ArchivalMode.EXTRACTIVE,
            ),
        )
        result = ConsolidationResult(
            removed_ids=("m1",),
            archived_count=1,
            archival_index=index,
        )
        assert len(result.archival_index) == 1
        assert result.archival_index[0].archival_id == "arch-1"


@pytest.mark.unit
class TestArchivalEntryDualMode:
    """ArchivalEntry new archival_mode field."""

    def test_archival_mode_required(self) -> None:
        """Omitting archival_mode raises ValidationError."""
        with pytest.raises(ValidationError):
            ArchivalEntry(  # type: ignore[call-arg]
                original_id="mem-1",
                agent_id="agent-1",
                content="test",
                category=MemoryCategory.EPISODIC,
                created_at=_NOW,
                archived_at=_NOW,
            )

    def test_with_archival_mode(self) -> None:
        entry = ArchivalEntry(
            original_id="mem-1",
            agent_id="agent-1",
            content="def foo(): pass",
            category=MemoryCategory.PROCEDURAL,
            created_at=_NOW,
            archived_at=_NOW,
            archival_mode=ArchivalMode.EXTRACTIVE,
        )
        assert entry.archival_mode == ArchivalMode.EXTRACTIVE
