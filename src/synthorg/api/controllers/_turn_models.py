# module-kind: code
"""Request/response DTOs for the unified conversational turn surface.

Extracted from ``_turn_dispatch`` so the dispatch service (the classify +
route logic) stays within its size tier. These are the wire models the
``/meta/chat/turn`` controller and its stream sibling exchange; the dispatch
functions construct :class:`TurnResult` and read :class:`TurnRequest`.
"""

from typing import Final, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.types import NotBlankStr
from synthorg.meta.charter.models import InterviewTurnResult
from synthorg.meta.chief_of_staff._multi_voice import ChimeIn
from synthorg.meta.chief_of_staff.actor import ConversationalActResult
from synthorg.meta.chief_of_staff.group_models import GroupConverseResult
from synthorg.meta.chief_of_staff.intent_router import (
    IntentRoutingReason,
    TurnIntent,
)
from synthorg.meta.chief_of_staff.models import ChatResponse, ProposeResult
from synthorg.meta.chief_of_staff.operator_console import ConsoleTurnResult

_MESSAGE_MAX_LENGTH: Final[int] = 2000


class TurnRequest(BaseModel):
    """Request body for one unified conversational turn."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    message: NotBlankStr = Field(
        max_length=_MESSAGE_MAX_LENGTH,
        description="The operator's message for this turn.",
    )
    conversation_id: NotBlankStr | None = Field(
        default=None,
        description="Existing conversation to continue; None starts a new one.",
    )
    intent_override: TurnIntent | None = Field(
        default=None,
        description=(
            "Force a capability instead of classifying (e.g. to continue a"
            " typed conversation). None auto-routes."
        ),
    )
    named_targets: tuple[NotBlankStr, ...] = Field(
        default=(),
        description=(
            "Roles/names the classifier read from the message, carried through a"
            " deferred stream so a re-issued ACT/GROUP turn keeps its targets"
            " instead of degrading to EXPLAIN. Only honoured with an override."
        ),
    )
    project: NotBlankStr | None = Field(
        default=None,
        description="Project the turn is scoped to, for propose/charter turns.",
    )


class TurnResult(BaseModel):
    """Outcome of one unified turn: the resolved intent plus its payload.

    Exactly one capability payload is set, matching :attr:`intent` (a degraded
    or explain turn carries :attr:`answer`).

    Attributes:
        intent: The capability the turn dispatched to.
        intent_reason: Why this intent was chosen or degraded to.
        intent_confidence: Classifier confidence (0-1) when a classification
            ran; ``None`` for an override / no-classifier turn.
        conversation_id: The conversation this turn belongs to; ``None`` for
            the stateless explain path.
        answer: The explain answer (set iff ``intent`` is EXPLAIN).
        propose: The clarify-or-propose outcome (set iff PROPOSE).
        group: The group-round outcome (set iff GROUP_CONVENE).
        act: The direct-acting outcome (set iff ACT).
        charter: The charter-interview outcome (set iff CHARTER).
        configure: The operator-console outcome (set iff CONFIGURE).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    intent: TurnIntent
    intent_reason: IntentRoutingReason
    intent_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    conversation_id: NotBlankStr | None = None
    answer: ChatResponse | None = None
    propose: ProposeResult | None = None
    group: GroupConverseResult | None = None
    act: ConversationalActResult | None = None
    charter: InterviewTurnResult | None = None
    configure: ConsoleTurnResult | None = None
    chime_ins: tuple[ChimeIn, ...] = Field(
        default=(),
        description=(
            "Specialists who added a short attributed perspective to an "
            "explain answer; empty for every other intent."
        ),
    )

    @model_validator(mode="after")
    def _validate_single_payload(self) -> Self:
        """Enforce exactly-one payload set, matching ``intent``.

        Returns:
            ``Self`` instance.

        Raises:
            ValueError: When the set payload does not match ``intent``, or a
                turn carries zero or several payloads.
        """
        payloads = {
            TurnIntent.EXPLAIN: self.answer,
            TurnIntent.PROPOSE: self.propose,
            TurnIntent.GROUP_CONVENE: self.group,
            TurnIntent.ACT: self.act,
            TurnIntent.CHARTER: self.charter,
            TurnIntent.CONFIGURE: self.configure,
        }
        present = [
            intent for intent, payload in payloads.items() if payload is not None
        ]
        if present != [self.intent]:
            msg = (
                f"exactly the {self.intent.value!r} payload must be set; "
                f"got {[i.value for i in present]}"
            )
            raise ValueError(msg)
        return self


__all__ = ["TurnRequest", "TurnResult"]
