"""Delegation request, result, and audit trail models."""

from typing import Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from synthorg.core.task import Task  # noqa: TC001
from synthorg.core.types import NotBlankStr  # noqa: TC001


class DelegationRequest(BaseModel):
    """Request to delegate a task down the hierarchy.

    Attributes:
        delegator_id: Agent ID of the delegator.
        delegatee_id: Agent ID of the target agent.
        task: The task to delegate.
        refinement: Additional context from the delegator.
        constraints: Extra constraints for the delegatee.
    """

    model_config = ConfigDict(frozen=True)

    delegator_id: NotBlankStr = Field(
        description="Agent ID of the delegator",
    )
    delegatee_id: NotBlankStr = Field(
        description="Agent ID of the target agent",
    )
    task: Task = Field(description="Task to delegate")
    refinement: str = Field(
        default="",
        description="Additional context from the delegator",
    )
    constraints: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Extra constraints for the delegatee",
    )

    @model_validator(mode="after")
    def _validate_self_delegation(self) -> Self:
        """Reject delegation to self."""
        if self.delegator_id == self.delegatee_id:
            msg = "delegator_id and delegatee_id must differ"
            raise ValueError(msg)
        return self


class DelegationResult(BaseModel):
    """Outcome of a delegation attempt.

    Attributes:
        success: Whether the delegation succeeded.
        delegated_task: The sub-task created, if successful.
        rejection_reason: Reason for rejection, if unsuccessful.
        blocked_by: Mechanism name that blocked, if applicable.
    """

    model_config = ConfigDict(frozen=True)

    success: bool = Field(description="Whether delegation succeeded")
    delegated_task: Task | None = Field(
        default=None,
        description="Sub-task created on success",
    )
    rejection_reason: str | None = Field(
        default=None,
        description="Reason for rejection",
    )
    blocked_by: NotBlankStr | None = Field(
        default=None,
        description="Mechanism name that blocked delegation",
    )

    @model_validator(mode="after")
    def _validate_success_consistency(self) -> Self:
        """Enforce success/failure field correlation."""
        if self.success:
            if self.delegated_task is None:
                msg = "delegated_task is required when success is True"
                raise ValueError(msg)
            if self.rejection_reason is not None:
                msg = "rejection_reason must be None when success is True"
                raise ValueError(msg)
            if self.blocked_by is not None:
                msg = "blocked_by must be None when success is True"
                raise ValueError(msg)
        elif self.delegated_task is not None:
            msg = "delegated_task must be None when success is False"
            raise ValueError(msg)
        if not self.success and (
            self.rejection_reason is None or not self.rejection_reason.strip()
        ):
            msg = "rejection_reason is required when success is False"
            raise ValueError(msg)
        return self


class DelegationRecord(BaseModel):
    """Audit trail entry for a completed delegation.

    Attributes:
        delegation_id: Unique delegation identifier.
        delegator_id: Agent ID of the delegator.
        delegatee_id: Agent ID of the delegatee.
        original_task_id: ID of the original task.
        delegated_task_id: ID of the created sub-task.
        timestamp: When the delegation occurred.
        refinement: Context provided by the delegator.
    """

    model_config = ConfigDict(frozen=True)

    delegation_id: NotBlankStr = Field(
        description="Unique delegation identifier",
    )
    delegator_id: NotBlankStr = Field(
        description="Delegator agent ID",
    )
    delegatee_id: NotBlankStr = Field(
        description="Delegatee agent ID",
    )
    original_task_id: NotBlankStr = Field(
        description="Original task ID",
    )
    delegated_task_id: NotBlankStr = Field(
        description="Created sub-task ID",
    )
    timestamp: AwareDatetime = Field(
        description="When delegation occurred",
    )
    refinement: str = Field(
        default="",
        description="Context provided by delegator",
    )
