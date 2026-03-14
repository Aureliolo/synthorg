"""Workspace isolation domain models."""

from datetime import datetime  # noqa: TC003
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from synthorg.core.enums import ConflictEscalation, ConflictType  # noqa: TC001
from synthorg.core.types import NotBlankStr  # noqa: TC001


class WorkspaceRequest(BaseModel):
    """Request to create an isolated workspace for an agent task.

    Attributes:
        task_id: Identifier of the task requiring isolation.
        agent_id: Identifier of the agent that will work in the workspace.
        base_branch: Git branch to branch from.
        file_scope: Optional file path hints for the workspace.
    """

    model_config = ConfigDict(frozen=True)

    task_id: NotBlankStr = Field(description="Task requiring isolation")
    agent_id: NotBlankStr = Field(description="Agent working in workspace")
    base_branch: NotBlankStr = Field(
        default="main",
        description="Git branch to branch from",
    )
    file_scope: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Optional file path hints",
    )


class Workspace(BaseModel):
    """An active isolated workspace backed by a git worktree.

    Attributes:
        workspace_id: Unique identifier for this workspace.
        task_id: Task this workspace serves.
        agent_id: Agent operating in this workspace.
        branch_name: Git branch created for this workspace.
        worktree_path: Filesystem path to the worktree directory.
        base_branch: Branch this workspace was created from.
        created_at: Timestamp of workspace creation.
    """

    model_config = ConfigDict(frozen=True)

    workspace_id: NotBlankStr = Field(description="Unique workspace ID")
    task_id: NotBlankStr = Field(description="Task this workspace serves")
    agent_id: NotBlankStr = Field(
        description="Agent operating in workspace",
    )
    branch_name: NotBlankStr = Field(
        description="Git branch for this workspace",
    )
    worktree_path: NotBlankStr = Field(
        description="Filesystem path to worktree",
    )
    base_branch: NotBlankStr = Field(
        description="Branch workspace was created from",
    )
    created_at: datetime = Field(description="Workspace creation timestamp")


class MergeConflict(BaseModel):
    """A single merge conflict detected during workspace merge.

    Attributes:
        file_path: Path of the conflicting file.
        conflict_type: Type of conflict (e.g. textual, semantic).
        ours_content: Content from the base branch side.
        theirs_content: Content from the workspace branch side.
    """

    model_config = ConfigDict(frozen=True)

    file_path: NotBlankStr = Field(description="Conflicting file path")
    conflict_type: ConflictType = Field(
        description="Type of conflict detected during merge",
    )
    ours_content: str = Field(
        default="",
        description="Base branch content",
    )
    theirs_content: str = Field(
        default="",
        description="Workspace branch content",
    )


class MergeResult(BaseModel):
    """Result of merging a single workspace branch back.

    Attributes:
        workspace_id: Workspace that was merged.
        branch_name: Branch that was merged.
        success: Whether the merge completed without conflicts.
        conflicts: Any conflicts encountered during merge.
        escalation: Escalation strategy applied, if any.
        merged_commit_sha: SHA of the merge commit, if successful.
        duration_seconds: Time taken for the merge operation.
    """

    model_config = ConfigDict(frozen=True)

    workspace_id: NotBlankStr = Field(description="Merged workspace ID")
    branch_name: NotBlankStr = Field(description="Merged branch name")
    success: bool = Field(description="Whether merge succeeded")
    conflicts: tuple[MergeConflict, ...] = Field(
        default=(),
        description="Conflicts encountered",
    )
    escalation: ConflictEscalation | None = Field(
        default=None,
        description="Escalation strategy applied",
    )
    merged_commit_sha: NotBlankStr | None = Field(
        default=None,
        description="Merge commit SHA if successful",
    )
    duration_seconds: float = Field(
        ge=0.0,
        description="Merge duration in seconds",
    )

    @model_validator(mode="after")
    def _validate_success_consistency(self) -> Self:
        """Ensure success, conflicts, and merged_commit_sha are consistent."""
        if self.success and self.conflicts:
            msg = "Successful merge cannot have conflicts"
            raise ValueError(msg)
        if self.success and self.merged_commit_sha is None:
            msg = "Successful merge must have a commit SHA"
            raise ValueError(msg)
        if not self.success and self.merged_commit_sha is not None:
            msg = "Failed merge cannot have a commit SHA"
            raise ValueError(msg)
        return self


class WorkspaceGroupResult(BaseModel):
    """Aggregated result of merging a group of workspaces.

    Attributes:
        group_id: Identifier for this merge group.
        merge_results: Individual merge results for each workspace.
        duration_seconds: Total time for the group merge operation.
    """

    model_config = ConfigDict(frozen=True)

    group_id: NotBlankStr = Field(description="Merge group identifier")
    merge_results: tuple[MergeResult, ...] = Field(
        default=(),
        description="Individual merge results",
    )
    duration_seconds: float = Field(
        ge=0.0,
        description="Total merge duration in seconds",
    )

    @computed_field(  # type: ignore[prop-decorator]
        description="Whether all workspaces merged successfully",
    )
    @property
    def all_merged(self) -> bool:
        """Return True only if every workspace merged without conflict."""
        if not self.merge_results:
            return False
        return all(r.success for r in self.merge_results)

    @computed_field(  # type: ignore[prop-decorator]
        description="Total number of conflicts across all merges",
    )
    @property
    def total_conflicts(self) -> int:
        """Sum of conflicts from all merge results."""
        return sum(len(r.conflicts) for r in self.merge_results)
