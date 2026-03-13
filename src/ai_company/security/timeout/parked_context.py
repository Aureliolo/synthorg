"""Parked context model for suspended agent executions.

When an agent's execution is parked (awaiting human approval), the
full ``AgentContext`` is serialized and stored as a ``ParkedContext``
so it can be resumed when the approval decision arrives.
"""

import copy
import json
from types import MappingProxyType
from typing import Self
from uuid import uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from ai_company.core.types import NotBlankStr  # noqa: TC001


class ParkedContext(BaseModel):
    """Serialized snapshot of a parked agent execution.

    Attributes:
        id: Unique identifier for this parked context.
        execution_id: The execution run ID from ``AgentContext``.
        agent_id: Agent whose execution was parked.
        task_id: Task the agent was working on.
        approval_id: Approval item that caused the park.
        parked_at: Timestamp when the context was parked.
        context_json: JSON-serialized ``AgentContext``.
        metadata: Additional metadata (e.g. tool name, action type).
    """

    model_config = ConfigDict(frozen=True)

    id: NotBlankStr = Field(
        default_factory=lambda: str(uuid4()),
        description="Unique parked context identifier",
    )
    execution_id: NotBlankStr = Field(description="Execution run identifier")
    agent_id: NotBlankStr = Field(description="Agent identifier")
    task_id: NotBlankStr | None = Field(
        default=None, description="Task identifier (None for taskless agents)"
    )
    approval_id: NotBlankStr = Field(description="Approval item identifier")
    parked_at: AwareDatetime = Field(description="When the context was parked")
    context_json: str = Field(description="JSON-serialized AgentContext")
    metadata: dict[str, str] = Field(
        default_factory=dict,
        description="Additional metadata",
    )

    @model_validator(mode="after")
    def _validate_and_protect(self) -> Self:
        """Validate context_json and deep-copy metadata."""
        try:
            json.loads(self.context_json)
        except (json.JSONDecodeError, TypeError) as exc:
            msg = f"context_json must be valid JSON: {exc}"
            raise ValueError(msg) from exc
        object.__setattr__(self, "metadata", copy.deepcopy(self.metadata))
        return self

    @property
    def metadata_view(self) -> MappingProxyType[str, str]:
        """Read-only view of metadata."""
        return MappingProxyType(self.metadata)
