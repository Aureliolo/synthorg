# module-kind: code
"""Normalized OpenHands event stream.

The adapter consumes a normalized event rather than the SDK's concrete
event classes, so the loop logic is independent of the SDK version and
fully testable with a fake. The real SDK runtime maps OpenHands
``ActionEvent`` / ``ObservationEvent`` / ``MessageEvent`` onto these.
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.completion_enums import FinishReason


class OpenHandsEventKind(StrEnum):
    """Kind of a normalized OpenHands event.

    ``MESSAGE`` / ``ACTION`` each correspond to one LLM completion (a turn);
    ``OBSERVATION`` is a tool result (updates artifact tracking, not a turn);
    ``FINISHED`` / ``ERROR`` terminate the run.
    """

    MESSAGE = "message"
    ACTION = "action"
    OBSERVATION = "observation"
    FINISHED = "finished"
    ERROR = "error"


class OpenHandsEvent(BaseModel):
    """One normalized event from an OpenHands conversation."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    kind: OpenHandsEventKind = Field(description="Event kind")
    text: str = Field(default="", description="Assistant text or error message")
    tool_name: str | None = Field(
        default=None, description="Tool invoked (ACTION events)"
    )
    input_tokens: int = Field(
        default=0, ge=0, description="Prompt tokens for this turn"
    )
    output_tokens: int = Field(
        default=0, ge=0, description="Completion tokens for this turn"
    )
    cost: float = Field(default=0.0, ge=0.0, description="Cost for this turn")

    @property
    def finish_reason(self) -> FinishReason:
        """Map the event kind onto a completion finish reason.

        Returns:
            ``TOOL_USE`` for an action, else ``STOP``.
        """
        return (
            FinishReason.TOOL_USE
            if self.kind is OpenHandsEventKind.ACTION
            else FinishReason.STOP
        )
