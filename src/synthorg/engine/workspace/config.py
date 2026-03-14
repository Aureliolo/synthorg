"""Workspace isolation configuration models."""

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.enums import ConflictEscalation, MergeOrder
from synthorg.core.types import NotBlankStr  # noqa: TC001


class PlannerWorktreesConfig(BaseModel):
    """Configuration for the planner-worktrees isolation strategy.

    Attributes:
        max_concurrent_worktrees: Maximum number of active worktrees.
        merge_order: Order in which branches are merged back.
        conflict_escalation: Strategy for handling merge conflicts.
        worktree_base_dir: Base directory for worktree creation.
        cleanup_on_merge: Whether to remove worktree after merge.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_concurrent_worktrees: int = Field(
        default=8,
        ge=1,
        le=32,
        description="Maximum number of active worktrees",
    )
    merge_order: MergeOrder = Field(
        default=MergeOrder.COMPLETION,
        description="Order in which branches are merged back",
    )
    conflict_escalation: ConflictEscalation = Field(
        default=ConflictEscalation.HUMAN,
        description="Strategy for handling merge conflicts",
    )
    worktree_base_dir: NotBlankStr | None = Field(
        default=None,
        description="Base directory for worktree creation",
    )
    cleanup_on_merge: bool = Field(
        default=True,
        description="Whether to remove worktree after merge",
    )


class WorkspaceIsolationConfig(BaseModel):
    """Top-level workspace isolation configuration.

    Attributes:
        strategy: Name of the isolation strategy to use.
        planner_worktrees: Config for planner-worktrees strategy.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    strategy: NotBlankStr = Field(
        default="planner_worktrees",
        description="Name of the isolation strategy",
    )
    planner_worktrees: PlannerWorktreesConfig = Field(
        default_factory=PlannerWorktreesConfig,
        description="Config for planner-worktrees strategy",
    )
