# module-kind: declarative
"""Result models for direct chat-driven MCP actions.

``AgentEngine.run_chat_action`` runs a short, tool-capable completion
loop with no Task lifecycle and returns a :class:`ChatActionResult`:
the tools it executed plus the agent's final message, or -- when a
sensitive action escalated -- the ``approval_id`` of the parked
decision. The same model is returned by the taskless resume path
(:meth:`AgentEngine.resume_parked_chat_action`).
"""

from pydantic import BaseModel, ConfigDict, Field, computed_field

from synthorg.core.types import NotBlankStr
from synthorg.engine.loop_protocol import TerminationReason


class ExecutedToolCall(BaseModel):
    """One tool the chat action invoked, with its fenced result."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    tool_name: NotBlankStr = Field(description="Name of the invoked tool")
    is_error: bool = Field(description="Whether the tool reported an error")
    result: str = Field(
        description="The tool result content (already untrusted-fenced)",
    )


class ChatActionResult(BaseModel):
    """Outcome of a direct chat-driven MCP action.

    Either a completed run (``final_message`` plus the executed
    ``tool_calls``) or a parked run gated to the approval queue
    (``approval_id`` set, ``termination_reason`` ``PARKED``). The
    ``tool_calls`` of a parked result include the escalated call whose
    fenced "approval required" notice is already in the conversation.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    termination_reason: TerminationReason = Field(
        description="Why the chat-action loop stopped",
    )
    final_message: str | None = Field(
        default=None,
        description="The agent's final assistant message, if any",
    )
    tool_calls: tuple[ExecutedToolCall, ...] = Field(
        default=(),
        description="Tools the action executed this run, in order",
    )
    approval_id: str | None = Field(
        default=None,
        description="Approval id of the parked decision (PARKED only)",
    )

    @computed_field(  # type: ignore[prop-decorator]
        description="Whether the action parked for human approval",
    )
    @property
    def parked(self) -> bool:
        """True when the action escalated and is awaiting a decision."""
        return self.termination_reason == TerminationReason.PARKED
