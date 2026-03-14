"""Tests for workspace isolation configuration models."""

import pytest
from pydantic import ValidationError

from synthorg.core.enums import ConflictEscalation, MergeOrder
from synthorg.engine.workspace.config import (
    PlannerWorktreesConfig,
    WorkspaceIsolationConfig,
)

# ---------------------------------------------------------------------------
# PlannerWorktreesConfig
# ---------------------------------------------------------------------------


class TestPlannerWorktreesConfig:
    """Tests for PlannerWorktreesConfig model."""

    @pytest.mark.unit
    def test_defaults(self) -> None:
        """Default values are applied correctly."""
        cfg = PlannerWorktreesConfig()
        assert cfg.max_concurrent_worktrees == 8
        assert cfg.merge_order == MergeOrder.COMPLETION
        assert cfg.conflict_escalation == ConflictEscalation.HUMAN
        assert cfg.worktree_base_dir is None
        assert cfg.cleanup_on_merge is True

    @pytest.mark.unit
    def test_custom_values(self) -> None:
        """Custom values are accepted."""
        cfg = PlannerWorktreesConfig(
            max_concurrent_worktrees=4,
            merge_order=MergeOrder.PRIORITY,
            conflict_escalation=ConflictEscalation.REVIEW_AGENT,
            worktree_base_dir="worktrees",
            cleanup_on_merge=False,
        )
        assert cfg.max_concurrent_worktrees == 4
        assert cfg.merge_order == MergeOrder.PRIORITY
        assert cfg.conflict_escalation == ConflictEscalation.REVIEW_AGENT
        assert cfg.worktree_base_dir == "worktrees"
        assert cfg.cleanup_on_merge is False

    @pytest.mark.unit
    def test_frozen(self) -> None:
        """Config is immutable."""
        cfg = PlannerWorktreesConfig()
        with pytest.raises(ValidationError, match="frozen"):
            cfg.max_concurrent_worktrees = 16  # type: ignore[misc]

    @pytest.mark.unit
    def test_max_concurrent_lower_bound(self) -> None:
        """max_concurrent_worktrees must be >= 1."""
        with pytest.raises(ValidationError):
            PlannerWorktreesConfig(max_concurrent_worktrees=0)

    @pytest.mark.unit
    def test_max_concurrent_upper_bound(self) -> None:
        """max_concurrent_worktrees must be <= 32."""
        with pytest.raises(ValidationError):
            PlannerWorktreesConfig(max_concurrent_worktrees=33)

    @pytest.mark.unit
    def test_max_concurrent_boundary_values(self) -> None:
        """Boundary values 1 and 32 are accepted."""
        low = PlannerWorktreesConfig(max_concurrent_worktrees=1)
        assert low.max_concurrent_worktrees == 1
        high = PlannerWorktreesConfig(max_concurrent_worktrees=32)
        assert high.max_concurrent_worktrees == 32

    @pytest.mark.unit
    def test_extra_fields_rejected(self) -> None:
        """Unknown fields are rejected by extra='forbid'."""
        with pytest.raises(ValidationError, match="extra"):
            PlannerWorktreesConfig(unknown_field="value")  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# WorkspaceIsolationConfig
# ---------------------------------------------------------------------------


class TestWorkspaceIsolationConfig:
    """Tests for WorkspaceIsolationConfig model."""

    @pytest.mark.unit
    def test_defaults(self) -> None:
        """Default values are applied correctly."""
        cfg = WorkspaceIsolationConfig()
        assert cfg.strategy == "planner_worktrees"
        assert isinstance(cfg.planner_worktrees, PlannerWorktreesConfig)
        assert cfg.planner_worktrees.max_concurrent_worktrees == 8

    @pytest.mark.unit
    def test_custom_strategy(self) -> None:
        """Custom strategy name is accepted."""
        cfg = WorkspaceIsolationConfig(strategy="custom_isolation")
        assert cfg.strategy == "custom_isolation"

    @pytest.mark.unit
    def test_nested_config(self) -> None:
        """Nested planner config is propagated."""
        cfg = WorkspaceIsolationConfig(
            planner_worktrees=PlannerWorktreesConfig(
                max_concurrent_worktrees=4,
            ),
        )
        assert cfg.planner_worktrees.max_concurrent_worktrees == 4

    @pytest.mark.unit
    def test_frozen(self) -> None:
        """Config is immutable."""
        cfg = WorkspaceIsolationConfig()
        with pytest.raises(ValidationError, match="frozen"):
            cfg.strategy = "other"  # type: ignore[misc]

    @pytest.mark.unit
    def test_blank_strategy_rejected(self) -> None:
        """Empty strategy is rejected by NotBlankStr."""
        with pytest.raises(ValidationError):
            WorkspaceIsolationConfig(strategy="")

    @pytest.mark.unit
    def test_extra_fields_rejected(self) -> None:
        """Unknown fields are rejected by extra='forbid'."""
        with pytest.raises(ValidationError, match="extra"):
            WorkspaceIsolationConfig(unknown_field="value")  # type: ignore[call-arg]
