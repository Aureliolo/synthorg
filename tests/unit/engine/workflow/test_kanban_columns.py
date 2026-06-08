"""Tests for Kanban board columns, transitions, WIP limits, and config."""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from synthorg.core.task_enums import TaskStatus
from synthorg.engine.workflow.kanban_board import (
    KanbanConfig,
    KanbanWipLimit,
    WipCheckResult,
    check_wip_limit,
)
from synthorg.engine.workflow.kanban_columns import (
    COLUMN_TO_STATUSES,
    STATUS_TO_COLUMN,
    VALID_COLUMN_TRANSITIONS,
    KanbanColumn,
    resolve_task_transitions,
    validate_column_transition,
)

# ── Column enum ────────────────────────────────────────────────


class TestKanbanColumnEnum:
    """KanbanColumn enum has exactly five members."""

    @pytest.mark.unit
    def test_column_count(self) -> None:
        assert len(KanbanColumn) == 5

    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("member", "value"),
        [
            (KanbanColumn.BACKLOG, "backlog"),
            (KanbanColumn.READY, "ready"),
            (KanbanColumn.IN_PROGRESS, "in_progress"),
            (KanbanColumn.REVIEW, "review"),
            (KanbanColumn.DONE, "done"),
        ],
    )
    def test_column_values(self, member: KanbanColumn, value: str) -> None:
        assert member.value == value


# ── Column <-> TaskStatus bridge ───────────────────────────────


class TestColumnStatusBridge:
    """Mappings between KanbanColumn and TaskStatus are consistent."""

    @pytest.mark.unit
    def test_every_column_has_status_mapping(self) -> None:
        for col in KanbanColumn:
            assert col in COLUMN_TO_STATUSES, (
                f"Missing COLUMN_TO_STATUSES entry for {col.value}"
            )

    @pytest.mark.unit
    def test_every_status_has_column_mapping(self) -> None:
        for status in TaskStatus:
            assert status in STATUS_TO_COLUMN, (
                f"Missing STATUS_TO_COLUMN entry for {status.value}"
            )

    @pytest.mark.unit
    def test_on_board_statuses_round_trip(self) -> None:
        """Every status that maps to a column also appears in that
        column's status set."""
        for status, column in STATUS_TO_COLUMN.items():
            if column is not None:
                assert status in COLUMN_TO_STATUSES[column]

    @pytest.mark.unit
    def test_off_board_statuses(self) -> None:
        off_board = {
            TaskStatus.BLOCKED,
            TaskStatus.FAILED,
            TaskStatus.INTERRUPTED,
            TaskStatus.SUSPENDED,
            TaskStatus.CANCELLED,
        }
        for status in off_board:
            assert STATUS_TO_COLUMN[status] is None

    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("column", "expected_status"),
        [
            (KanbanColumn.BACKLOG, TaskStatus.CREATED),
            (KanbanColumn.READY, TaskStatus.ASSIGNED),
            (KanbanColumn.IN_PROGRESS, TaskStatus.IN_PROGRESS),
            (KanbanColumn.REVIEW, TaskStatus.IN_REVIEW),
            (KanbanColumn.DONE, TaskStatus.COMPLETED),
        ],
    )
    def test_specific_column_status_mappings(
        self,
        column: KanbanColumn,
        expected_status: TaskStatus,
    ) -> None:
        assert expected_status in COLUMN_TO_STATUSES[column]
        assert STATUS_TO_COLUMN[expected_status] == column


# ── Column transitions ─────────────────────────────────────────


class TestColumnTransitions:
    """validate_column_transition enforces the transition map."""

    @pytest.mark.unit
    def test_every_column_has_transition_entry(self) -> None:
        for col in KanbanColumn:
            assert col in VALID_COLUMN_TRANSITIONS

    @pytest.mark.unit
    def test_done_is_terminal(self) -> None:
        assert VALID_COLUMN_TRANSITIONS[KanbanColumn.DONE] == frozenset()

    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("from_col", "to_col"),
        [
            (KanbanColumn.BACKLOG, KanbanColumn.READY),
            (KanbanColumn.BACKLOG, KanbanColumn.DONE),
            (KanbanColumn.READY, KanbanColumn.IN_PROGRESS),
            (KanbanColumn.READY, KanbanColumn.BACKLOG),
            (KanbanColumn.IN_PROGRESS, KanbanColumn.REVIEW),
            (KanbanColumn.IN_PROGRESS, KanbanColumn.BACKLOG),
            (KanbanColumn.IN_PROGRESS, KanbanColumn.READY),
            (KanbanColumn.REVIEW, KanbanColumn.DONE),
            (KanbanColumn.REVIEW, KanbanColumn.IN_PROGRESS),
        ],
    )
    def test_valid_transitions(
        self,
        from_col: KanbanColumn,
        to_col: KanbanColumn,
    ) -> None:
        validate_column_transition(from_col, to_col)

    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("from_col", "to_col"),
        [
            (KanbanColumn.DONE, KanbanColumn.BACKLOG),
            (KanbanColumn.DONE, KanbanColumn.REVIEW),
            (KanbanColumn.BACKLOG, KanbanColumn.IN_PROGRESS),
            (KanbanColumn.BACKLOG, KanbanColumn.REVIEW),
            (KanbanColumn.READY, KanbanColumn.REVIEW),
            (KanbanColumn.READY, KanbanColumn.DONE),
            (KanbanColumn.REVIEW, KanbanColumn.BACKLOG),
            (KanbanColumn.REVIEW, KanbanColumn.READY),
        ],
    )
    def test_invalid_transitions_raise(
        self,
        from_col: KanbanColumn,
        to_col: KanbanColumn,
    ) -> None:
        with pytest.raises(ValueError, match="Invalid Kanban column"):
            validate_column_transition(from_col, to_col)

    @pytest.mark.unit
    def test_self_transition_not_allowed(self) -> None:
        for col in KanbanColumn:
            if col in VALID_COLUMN_TRANSITIONS[col]:
                continue  # pragma: no cover
            with pytest.raises(ValueError, match="Invalid Kanban column"):
                validate_column_transition(col, col)


# ── resolve_task_transitions ───────────────────────────────────


class TestResolveTaskTransitions:
    """resolve_task_transitions returns correct status paths."""

    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("from_col", "to_col", "expected_first"),
        [
            (
                KanbanColumn.BACKLOG,
                KanbanColumn.READY,
                TaskStatus.ASSIGNED,
            ),
            (
                KanbanColumn.READY,
                KanbanColumn.IN_PROGRESS,
                TaskStatus.IN_PROGRESS,
            ),
            (
                KanbanColumn.IN_PROGRESS,
                KanbanColumn.REVIEW,
                TaskStatus.IN_REVIEW,
            ),
            (
                KanbanColumn.REVIEW,
                KanbanColumn.DONE,
                TaskStatus.COMPLETED,
            ),
        ],
    )
    def test_forward_single_step(
        self,
        from_col: KanbanColumn,
        to_col: KanbanColumn,
        expected_first: TaskStatus,
    ) -> None:
        path = resolve_task_transitions(from_col, to_col)
        assert path[0] == expected_first

    @pytest.mark.unit
    def test_backlog_to_done_is_multi_step(self) -> None:
        path = resolve_task_transitions(KanbanColumn.BACKLOG, KanbanColumn.DONE)
        assert len(path) > 1
        assert path[-1] == TaskStatus.COMPLETED

    @pytest.mark.unit
    def test_review_to_in_progress_rework(self) -> None:
        path = resolve_task_transitions(KanbanColumn.REVIEW, KanbanColumn.IN_PROGRESS)
        assert path == (TaskStatus.IN_PROGRESS,)

    @pytest.mark.unit
    def test_all_valid_transitions_have_paths(self) -> None:
        """Every valid column transition has a defined status path."""
        for from_col, targets in VALID_COLUMN_TRANSITIONS.items():
            for to_col in targets:
                path = resolve_task_transitions(from_col, to_col)
                assert len(path) >= 1, (
                    f"Empty path for {from_col.value} -> {to_col.value}"
                )

    @pytest.mark.unit
    def test_undefined_path_raises(self) -> None:
        with pytest.raises(ValueError, match="No task status path"):
            resolve_task_transitions(KanbanColumn.DONE, KanbanColumn.BACKLOG)


# ── WIP limit enforcement ──────────────────────────────────────


class TestWipLimitEnforcement:
    """check_wip_limit respects configured limits."""

    @pytest.mark.unit
    def test_under_limit_allowed(self, strict_kanban_config: KanbanConfig) -> None:
        result = check_wip_limit(
            strict_kanban_config,
            KanbanColumn.IN_PROGRESS,
            {KanbanColumn.IN_PROGRESS: 0},
        )
        assert result.allowed is True
        assert result.limit == 2

    @pytest.mark.unit
    def test_at_limit_minus_one_allowed(
        self, strict_kanban_config: KanbanConfig
    ) -> None:
        result = check_wip_limit(
            strict_kanban_config,
            KanbanColumn.IN_PROGRESS,
            {KanbanColumn.IN_PROGRESS: 1},
        )
        assert result.allowed is True

    @pytest.mark.unit
    def test_at_limit_rejected_in_strict_mode(
        self, strict_kanban_config: KanbanConfig
    ) -> None:
        result = check_wip_limit(
            strict_kanban_config,
            KanbanColumn.IN_PROGRESS,
            {KanbanColumn.IN_PROGRESS: 2},
        )
        assert result.allowed is False
        assert result.current_count == 2
        assert result.limit == 2

    @pytest.mark.unit
    def test_over_limit_allowed_in_advisory_mode(
        self, advisory_kanban_config: KanbanConfig
    ) -> None:
        result = check_wip_limit(
            advisory_kanban_config,
            KanbanColumn.IN_PROGRESS,
            {KanbanColumn.IN_PROGRESS: 5},
        )
        assert result.allowed is True

    @pytest.mark.unit
    def test_no_limit_configured_always_allowed(
        self, strict_kanban_config: KanbanConfig
    ) -> None:
        result = check_wip_limit(
            strict_kanban_config,
            KanbanColumn.BACKLOG,
            {KanbanColumn.BACKLOG: 100},
        )
        assert result.allowed is True
        assert result.limit is None

    @pytest.mark.unit
    def test_missing_count_defaults_to_zero(
        self, strict_kanban_config: KanbanConfig
    ) -> None:
        result = check_wip_limit(
            strict_kanban_config,
            KanbanColumn.IN_PROGRESS,
            {},
        )
        assert result.allowed is True
        assert result.current_count == 0

    @pytest.mark.unit
    def test_result_model_fields(self) -> None:
        result = WipCheckResult(
            allowed=True,
            column=KanbanColumn.REVIEW,
            current_count=1,
            limit=3,
        )
        assert result.allowed is True
        assert result.column == KanbanColumn.REVIEW
        assert result.current_count == 1
        assert result.limit == 3


# ── KanbanConfig validation ────────────────────────────────────


class TestKanbanConfigValidation:
    """KanbanConfig validators catch bad configurations."""

    @pytest.mark.unit
    def test_default_config_is_valid(self) -> None:
        config = KanbanConfig()
        assert len(config.wip_limits) == 2

    @pytest.mark.unit
    def test_duplicate_columns_rejected(self) -> None:
        with pytest.raises(ValueError, match="Duplicate WIP limit"):
            KanbanConfig(
                wip_limits=(
                    KanbanWipLimit(column=KanbanColumn.IN_PROGRESS, limit=3),
                    KanbanWipLimit(column=KanbanColumn.IN_PROGRESS, limit=5),
                ),
            )

    @pytest.mark.unit
    def test_done_column_limit_rejected(self) -> None:
        with pytest.raises(ValueError, match="DONE column are not allowed"):
            KanbanConfig(
                wip_limits=(KanbanWipLimit(column=KanbanColumn.DONE, limit=10),),
            )

    @pytest.mark.unit
    def test_empty_wip_limits_allowed(self) -> None:
        config = KanbanConfig(wip_limits=())
        assert config.wip_limits == ()

    @pytest.mark.unit
    def test_wip_limit_bounds(self) -> None:
        with pytest.raises(ValueError, match="greater than or equal"):
            KanbanWipLimit(column=KanbanColumn.REVIEW, limit=0)
        with pytest.raises(ValueError, match="less than or equal"):
            KanbanWipLimit(column=KanbanColumn.REVIEW, limit=101)


# ── Property-based tests ───────────────────────────────────────


class TestKanbanProperties:
    """Hypothesis-based consistency checks."""

    @pytest.mark.unit
    @given(
        column=st.sampled_from(list(KanbanColumn)),
    )
    def test_status_to_column_round_trips(self, column: KanbanColumn) -> None:
        """Every on-board status in a column maps back to that column."""
        for status in COLUMN_TO_STATUSES[column]:
            assert STATUS_TO_COLUMN[status] == column

    @pytest.mark.unit
    @given(
        status=st.sampled_from(list(TaskStatus)),
    )
    def test_every_status_is_mapped(self, status: TaskStatus) -> None:
        """Every TaskStatus appears in STATUS_TO_COLUMN."""
        assert status in STATUS_TO_COLUMN
