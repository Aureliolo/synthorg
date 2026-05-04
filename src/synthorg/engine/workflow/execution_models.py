"""Workflow execution models.

A ``WorkflowExecution`` is a runtime instance of a
``WorkflowDefinition``.  It tracks per-node execution state
and maps TASK nodes to concrete ``Task`` instances created
via the ``TaskEngine``.
"""

from collections.abc import Mapping  # noqa: TC003
from copy import deepcopy
from datetime import UTC, datetime
from types import MappingProxyType
from typing import ClassVar, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from synthorg.core.enums import (
    WorkflowExecutionStatus,
    WorkflowNodeExecutionStatus,
    WorkflowNodeType,
)
from synthorg.core.types import NotBlankStr  # noqa: TC001


class ExecutionFrame(BaseModel):
    """A scoped variable frame for subworkflow call/return execution.

    The activation-time graph walker pushes a new frame whenever it
    resolves a ``SUBWORKFLOW`` node and pops it when the child graph
    completes.  Each frame owns a private ``variables`` mapping;
    children cannot read parent variables outside declared inputs
    because the walker receives a new frame-scoped context map.

    Attributes:
        workflow_id: ID of the workflow whose graph is being walked.
        workflow_version: Semver version string of the workflow.
        variables: Frame-local variable map (resolved inputs, defaults,
            and walk-time computed values).
        parent_frame: Immediate caller frame, ``None`` for the root.
        depth: Nesting depth (root frame is ``0``).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    workflow_id: NotBlankStr = Field(description="Workflow definition ID")
    workflow_version: NotBlankStr = Field(description="Semver version")
    variables: Mapping[str, object] = Field(
        default_factory=lambda: MappingProxyType({}),
        description="Frame-local variables",
    )

    @field_validator("variables", mode="before")
    @classmethod
    def _freeze_variables(
        cls, value: Mapping[str, object]
    ) -> MappingProxyType[str, object]:
        return MappingProxyType(deepcopy(dict(value)))

    parent_frame: ExecutionFrame | None = Field(
        default=None,
        description="Caller frame (None for root)",
    )
    depth: int = Field(default=0, ge=0, description="Nesting depth")

    @model_validator(mode="after")
    def _validate_depth_matches_parent(self) -> Self:
        """Ensure ``depth == parent.depth + 1`` (root frame has depth 0)."""
        if self.parent_frame is None:
            if self.depth != 0:
                msg = f"Root frame must have depth=0, got {self.depth}"
                raise ValueError(msg)
        elif self.depth != self.parent_frame.depth + 1:
            msg = (
                f"Frame depth {self.depth} does not match "
                f"parent depth + 1 ({self.parent_frame.depth + 1})"
            )
            raise ValueError(msg)
        return self


ExecutionFrame.model_rebuild()


class WorkflowNodeExecution(BaseModel):
    """Per-node execution state within a workflow execution.

    Attributes:
        node_id: ID of the node in the source ``WorkflowDefinition``.
        node_type: Type of the node (task, conditional, etc.).
        status: Current processing status.
        task_id: Concrete task ID when a TASK node has been
            instantiated (``None`` for control nodes and pending nodes).
        skipped_reason: Explanation when the node was skipped
            (e.g. conditional branch not taken).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    node_id: NotBlankStr = Field(description="Source node ID")
    node_type: WorkflowNodeType = Field(description="Node type")
    status: WorkflowNodeExecutionStatus = Field(
        default=WorkflowNodeExecutionStatus.PENDING,
        description="Processing status",
    )
    task_id: NotBlankStr | None = Field(
        default=None,
        description="Concrete task ID (TASK nodes only)",
    )
    skipped_reason: NotBlankStr | None = Field(
        default=None,
        description="Reason when status is SKIPPED",
    )

    _TASK_LINKED_STATUSES: ClassVar[frozenset[WorkflowNodeExecutionStatus]] = frozenset(
        {
            WorkflowNodeExecutionStatus.TASK_CREATED,
            WorkflowNodeExecutionStatus.TASK_COMPLETED,
            WorkflowNodeExecutionStatus.TASK_FAILED,
        }
    )

    @model_validator(mode="after")
    def _validate_status_fields(self) -> Self:
        """Enforce cross-field invariants between status and optional fields."""
        if self.status in self._TASK_LINKED_STATUSES:
            if self.node_type is not WorkflowNodeType.TASK:
                msg = (
                    f"{self.status.value!r} status is only valid for"
                    f" TASK nodes, not {self.node_type.value!r}"
                )
                raise ValueError(msg)
            if self.task_id is None:
                msg = f"task_id is required when status is {self.status.value!r}"
                raise ValueError(msg)
        elif self.task_id is not None:
            msg = "task_id must be None when status is not a task-linked status"
            raise ValueError(msg)

        if self.status is WorkflowNodeExecutionStatus.SKIPPED:
            if self.skipped_reason is None:
                msg = "skipped_reason is required when status is SKIPPED"
                raise ValueError(msg)
        elif self.skipped_reason is not None:
            msg = "skipped_reason must be None when status is not SKIPPED"
            raise ValueError(msg)
        return self


class WorkflowExecution(BaseModel):
    """Runtime instance of an activated workflow definition.

    Created when a user activates a ``WorkflowDefinition``.
    Tracks the overall execution lifecycle and per-node
    processing state.

    Attributes:
        id: Unique execution identifier (``"wfexec-{uuid12}"``).
        definition_id: Source ``WorkflowDefinition`` ID.
        definition_revision: Snapshot of the definition's optimistic
            concurrency counter at activation time.
        status: Overall execution lifecycle status.
        node_executions: Per-node execution state.
        activated_by: Identity of the user who triggered activation.
        project: Project ID for all created tasks.
        created_at: Creation timestamp (UTC).
        updated_at: Last update timestamp (UTC).
        completed_at: Completion timestamp (``None`` until terminal).
        error: Error message when status is ``FAILED``.
        version: Optimistic concurrency version counter.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    id: NotBlankStr = Field(description="Unique execution ID")
    definition_id: NotBlankStr = Field(description="Source definition ID")
    definition_revision: int = Field(
        ge=1,
        description="Definition revision (optimistic concurrency) at activation time",
    )
    status: WorkflowExecutionStatus = Field(
        default=WorkflowExecutionStatus.PENDING,
        description="Overall execution status",
    )
    node_executions: tuple[WorkflowNodeExecution, ...] = Field(
        default=(),
        description="Per-node execution state",
    )
    activated_by: NotBlankStr = Field(
        description="User who triggered activation",
    )
    project: NotBlankStr = Field(
        description="Project ID for created tasks",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Creation timestamp (UTC)",
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Last update timestamp (UTC)",
    )
    completed_at: datetime | None = Field(
        default=None,
        description="Completion timestamp",
    )
    error: NotBlankStr | None = Field(
        default=None,
        description="Error message when FAILED",
    )
    version: int = Field(
        default=1,
        ge=1,
        description="Optimistic concurrency version",
    )

    @model_validator(mode="after")
    def _validate_status_fields(self) -> Self:
        """Enforce cross-field invariants between status and optional fields."""
        terminal = {
            WorkflowExecutionStatus.COMPLETED,
            WorkflowExecutionStatus.FAILED,
            WorkflowExecutionStatus.CANCELLED,
        }
        if self.status in terminal:
            if self.completed_at is None:
                msg = "completed_at is required when status is terminal"
                raise ValueError(msg)
        elif self.completed_at is not None:
            msg = "completed_at must be None when status is not terminal"
            raise ValueError(msg)

        if self.status is WorkflowExecutionStatus.FAILED:
            if self.error is None:
                msg = "error is required when status is FAILED"
                raise ValueError(msg)
        elif self.error is not None:
            msg = "error must be None when status is not FAILED"
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_unique_node_ids(self) -> Self:
        """Ensure no duplicate node_id values in node_executions."""
        if not self.node_executions:
            return self
        node_ids = [ne.node_id for ne in self.node_executions]
        if len(node_ids) != len(set(node_ids)):
            dupes = sorted(v for v in set(node_ids) if node_ids.count(v) > 1)
            msg = f"Duplicate node_execution node_ids: {dupes}"
            raise ValueError(msg)
        return self
