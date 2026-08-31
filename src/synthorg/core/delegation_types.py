"""The delegation-request model engine classification routes through.

A core domain type (it depends only on :mod:`core.task` and
:mod:`core.types`), so it lives in ``core`` rather than
``communication``. Keeping it here lets the engine classification
loaders reference a delegation request without importing the
``communication`` package hub, which would otherwise close an
``engine`` <-> ``communication`` cold-import cycle.
"""

from collections.abc import Mapping
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.task import Task
from synthorg.core.types import NotBlankStr


class DelegationRequest(BaseModel):
    """Request to delegate a task down the hierarchy.

    Attributes:
        delegator_id: Agent ID of the delegator.
        delegatee_id: Agent ID of the target agent.
        task: The task to delegate.
        refinement: Additional context from the delegator.
        constraints: Extra constraints for the delegatee.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

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
    entity_versions: Mapping[str, int] | None = Field(
        default=None,
        description="Delegator's known entity version manifest",
    )

    @model_validator(mode="after")
    def _validate_self_delegation(self) -> Self:
        """Reject delegation to self.

        Returns:
            The validated request.

        Raises:
            ValueError: If ``delegator_id`` equals ``delegatee_id``.
        """
        if self.delegator_id == self.delegatee_id:
            msg = "delegator_id and delegatee_id must differ"
            raise ValueError(msg)
        return self
