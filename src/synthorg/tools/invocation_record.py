"""Tool invocation record for activity tracking.

Immutable record of a single tool invocation, used by the activity
timeline to surface ``tool_used`` events.
"""

from typing import Self
from uuid import uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from synthorg.core.types import NotBlankStr


class ToolInvocationRecord(BaseModel):
    """Immutable record of a single tool invocation.

    Once created, a ``ToolInvocationRecord`` cannot be modified
    (frozen model).  This enforces the append-only pattern.

    Attributes:
        id: Unique record identifier.
        agent_id: Agent who invoked the tool.
        task_id: Task context (None if invoked outside a task).
        tool_name: Name of the tool invoked.
        is_success: Whether the invocation succeeded.
        timestamp: When the invocation occurred.
        error_message: Error message if the invocation failed.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    id: NotBlankStr = Field(
        default_factory=lambda: NotBlankStr(str(uuid4())),
        description="Unique record identifier",
    )
    agent_id: NotBlankStr = Field(description="Agent who invoked the tool")
    task_id: NotBlankStr | None = Field(
        default=None,
        description="Task context (None if invoked outside a task)",
    )
    tool_name: NotBlankStr = Field(description="Name of the tool invoked")
    is_success: bool = Field(description="Whether the invocation succeeded")
    timestamp: AwareDatetime = Field(
        description="When the invocation occurred",
    )
    error_message: str | None = Field(
        default=None,
        max_length=2048,
        description="Error message if the invocation failed",
    )

    @model_validator(mode="after")
    def _validate_success_consistency(self) -> Self:
        """Reject error_message when the invocation succeeded."""
        if self.is_success and self.error_message is not None:
            msg = "error_message must be None when is_success is True"
            raise ValueError(msg)
        return self
