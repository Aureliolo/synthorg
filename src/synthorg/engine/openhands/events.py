# module-kind: code
"""Normalized OpenHands event stream.

The adapter consumes a normalized event rather than the SDK's concrete
event classes, so the loop logic is independent of the SDK version and
fully testable with a fake. The real SDK runtime maps OpenHands
``ActionEvent`` / ``ObservationEvent`` / ``MessageEvent`` onto these.
"""

from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from synthorg.core.completion_enums import FinishReason
from synthorg.core.types import NotBlankStr


class OpenHandsEventKind(StrEnum):
    """Kind of a normalized OpenHands event.

    ``MESSAGE`` / ``ACTION`` each correspond to one LLM completion (a turn);
    ``OBSERVATION`` is a tool result (not a turn); ``FINISHED`` / ``ERROR``
    terminate the run.
    """

    MESSAGE = "message"
    ACTION = "action"
    OBSERVATION = "observation"
    FINISHED = "finished"
    ERROR = "error"


# Kinds that correspond to an LLM completion (a turn), so may carry token /
# cost figures; other kinds must not.
_TURN_KINDS = frozenset({OpenHandsEventKind.MESSAGE, OpenHandsEventKind.ACTION})


class OpenHandsEvent(BaseModel):
    """One normalized event from an OpenHands conversation."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    kind: OpenHandsEventKind = Field(description="Event kind")
    text: str = Field(default="", description="Assistant text or error message")
    tool_name: NotBlankStr | None = Field(
        default=None, description="Tool invoked (ACTION events)"
    )
    input_tokens: int = Field(
        default=0, ge=0, description="Prompt tokens for this turn"
    )
    output_tokens: int = Field(
        default=0, ge=0, description="Completion tokens for this turn"
    )
    cost: float = Field(default=0.0, ge=0.0, description="Cost for this turn")

    @computed_field
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

    @model_validator(mode="after")
    def _validate_kind_invariants(self) -> Self:
        """Enforce that per-kind fields are only set on the kinds that own them.

        ``tool_name`` belongs to an ACTION; token / cost figures belong to a
        turn (MESSAGE / ACTION). A stray value on another kind is a mapping
        bug, caught here rather than skewing turn accounting downstream.

        Returns:
            The validated event.

        Raises:
            ValueError: If a field is set on a kind that does not own it.
        """
        if self.tool_name is not None and self.kind is not OpenHandsEventKind.ACTION:
            msg = f"tool_name is only valid on an ACTION event, not {self.kind}"
            raise ValueError(msg)
        if (
            self.input_tokens or self.output_tokens or self.cost
        ) and self.kind not in _TURN_KINDS:
            msg = f"token / cost figures are only valid on a turn, not {self.kind}"
            raise ValueError(msg)
        return self
