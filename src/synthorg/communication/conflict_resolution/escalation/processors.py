"""Decision processor strategy for the escalation queue."""

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal, assert_never
from uuid import uuid4

from synthorg.communication.conflict_resolution.escalation.models import (
    EscalationDecision,
    RejectDecision,
    WinnerDecision,
)
from synthorg.communication.conflict_resolution.models import (
    Conflict,
    ConflictResolution,
    ConflictResolutionOutcome,
    DissentRecord,
)
from synthorg.communication.enums import ConflictResolutionStrategy
from synthorg.observability import get_logger

if TYPE_CHECKING:
    from synthorg.core.types import NotBlankStr
from synthorg.observability.events.conflict import (
    CONFLICT_ESCALATION_RESOLVED,
    CONFLICT_VALIDATION_ERROR,
)

logger = get_logger(__name__)

DecisionMode = Literal["winner", "hybrid"]

_NO_WINNER_OUTCOMES = frozenset(
    {
        ConflictResolutionOutcome.ESCALATED_TO_HUMAN,
        ConflictResolutionOutcome.REJECTED_BY_HUMAN,
    },
)


def _build_dissent_records_from_resolution(
    conflict: Conflict,
    resolution: ConflictResolution,
) -> tuple[DissentRecord, ...]:
    """Emit one dissent record per non-winning position.

    For outcomes without a winner (escalated / rejected), every
    position is recorded so auditors keep the full stance history.
    """
    if resolution.outcome in _NO_WINNER_OUTCOMES:
        targets = conflict.positions
    else:
        targets = tuple(
            p for p in conflict.positions if p.agent_id != resolution.winning_agent_id
        )
    return tuple(
        DissentRecord(
            id=f"dissent-{uuid4().hex[:12]}",
            conflict=conflict,
            resolution=resolution,
            dissenting_agent_id=pos.agent_id,
            dissenting_position=pos.position,
            strategy_used=ConflictResolutionStrategy.HUMAN,
            timestamp=datetime.now(UTC),
            metadata=(("escalation_reason", "human_review_required"),),
        )
        for pos in targets
    )


class HumanDecisionProcessor:
    """Build a :class:`ConflictResolution` from an operator decision.

    The ``mode`` discriminator selects which decision shapes are
    accepted:

    - ``mode="winner"`` (safest surface) accepts only
      :class:`WinnerDecision`. A :class:`RejectDecision` raises a
      precise ``ValueError`` so the REST layer can surface a
      ``422 Unprocessable Entity`` instead of a 500.
    - ``mode="hybrid"`` additionally accepts
      :class:`RejectDecision`, producing a ``REJECTED_BY_HUMAN``
      outcome with no winner so the caller can fall back to a
      different strategy (retry, alternative resolution, manual
      intervention).

    Args:
        mode: Strategy discriminator.  Defaults to ``"winner"``.
    """

    __slots__ = ("_mode",)

    _mode: DecisionMode

    def __init__(self, mode: DecisionMode = "winner") -> None:
        if mode not in ("winner", "hybrid"):
            msg = f"mode must be 'winner' or 'hybrid', got {mode!r}"
            logger.warning(
                CONFLICT_VALIDATION_ERROR,
                note="invalid_decision_mode",
                attempted_mode=mode,
                error=msg,
            )
            raise ValueError(msg)
        self._mode = mode

    @property
    def mode(self) -> DecisionMode:
        """The strategy discriminator (``"winner"`` or ``"hybrid"``)."""
        return self._mode

    def process(
        self,
        conflict: Conflict,
        decision: EscalationDecision,
        *,
        decided_by: NotBlankStr,
    ) -> ConflictResolution:
        """Build a resolution matching the decision and the configured mode.

        Raises:
            ValueError: ``decision`` is not a :class:`WinnerDecision`
                and the processor is in ``"winner"`` mode, or
                ``decision`` is a :class:`WinnerDecision` whose
                ``winning_agent_id`` does not match any participant.
        """
        if isinstance(decision, WinnerDecision):
            return self._build_winner_resolution(
                conflict,
                decision,
                decided_by=decided_by,
            )
        # An unknown subclass of ``EscalationDecision`` (e.g. a future
        # variant added alongside ``WinnerDecision`` / ``RejectDecision``)
        # must not silently fall through to the reject path -- that would
        # misclassify the new decision as ``REJECTED_BY_HUMAN``. Detect
        # ``RejectDecision`` explicitly and raise on anything else.
        if isinstance(decision, RejectDecision):
            if self._mode == "winner":
                # Raised as ValueError (rather than TypeError) because the
                # caller is the REST layer validating payload shapes; the
                # escalations controller translates this into a 422
                # ValidationError.
                msg = (
                    "HumanDecisionProcessor in 'winner' mode only accepts "
                    "'winner' decisions. Configure decision_strategy='hybrid' "
                    "to allow 'reject' decisions."
                )
                logger.warning(
                    CONFLICT_ESCALATION_RESOLVED,
                    conflict_id=conflict.id,
                    decided_by=decided_by,
                    decision_type=getattr(decision, "type", type(decision).__name__),
                    strategy=ConflictResolutionStrategy.HUMAN.value,
                    note="winner_mode_rejected_reject_decision",
                )
                raise ValueError(msg)
            return self._build_reject_resolution(
                conflict,
                decision,
                decided_by=decided_by,
            )
        # ``EscalationDecision`` is a union of the two variants above;
        # ``assert_never`` proves exhaustiveness to the type checker and
        # raises ``AssertionError`` at runtime if a future variant is
        # added without updating this branch.
        assert_never(decision)

    def build_dissent_records(
        self,
        conflict: Conflict,
        resolution: ConflictResolution,
    ) -> tuple[DissentRecord, ...]:
        """Build dissent records covering all non-winning positions."""
        return _build_dissent_records_from_resolution(conflict, resolution)

    @staticmethod
    def _build_winner_resolution(
        conflict: Conflict,
        decision: WinnerDecision,
        *,
        decided_by: NotBlankStr,
    ) -> ConflictResolution:
        winning_position = next(
            (
                p.position
                for p in conflict.positions
                if p.agent_id == decision.winning_agent_id
            ),
            None,
        )
        if winning_position is None:
            msg = (
                f"winning_agent_id {decision.winning_agent_id!r} "
                "does not match any position in the conflict"
            )
            logger.warning(
                CONFLICT_ESCALATION_RESOLVED,
                conflict_id=conflict.id,
                decided_by=decided_by,
                winning_agent_id=decision.winning_agent_id,
                strategy=ConflictResolutionStrategy.HUMAN.value,
                note="winner_agent_not_in_conflict",
            )
            raise ValueError(msg)
        resolution = ConflictResolution(
            conflict_id=conflict.id,
            outcome=ConflictResolutionOutcome.RESOLVED_BY_HUMAN,
            winning_agent_id=decision.winning_agent_id,
            winning_position=winning_position,
            decided_by=decided_by,
            reasoning=decision.reasoning,
            resolved_at=datetime.now(UTC),
        )
        logger.info(
            CONFLICT_ESCALATION_RESOLVED,
            conflict_id=conflict.id,
            decided_by=decided_by,
            strategy=ConflictResolutionStrategy.HUMAN.value,
            outcome=resolution.outcome.value,
            winning_agent_id=resolution.winning_agent_id,
        )
        return resolution

    @staticmethod
    def _build_reject_resolution(
        conflict: Conflict,
        decision: RejectDecision,
        *,
        decided_by: NotBlankStr,
    ) -> ConflictResolution:
        resolution = ConflictResolution(
            conflict_id=conflict.id,
            outcome=ConflictResolutionOutcome.REJECTED_BY_HUMAN,
            winning_agent_id=None,
            winning_position=None,
            decided_by=decided_by,
            reasoning=decision.reasoning,
            resolved_at=datetime.now(UTC),
        )
        logger.info(
            CONFLICT_ESCALATION_RESOLVED,
            conflict_id=conflict.id,
            decided_by=decided_by,
            strategy=ConflictResolutionStrategy.HUMAN.value,
            outcome=resolution.outcome.value,
            note="hybrid_mode_rejected_decision",
        )
        return resolution
