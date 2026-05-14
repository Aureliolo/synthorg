"""Decision processor strategies for the escalation queue.

Two concrete strategies satisfy :class:`DecisionProcessor`:

- :class:`WinnerOnlyDecisionProcessor` accepts only
  :class:`WinnerDecision`. Receiving a :class:`RejectDecision` raises
  :class:`EscalationDecisionShapeError`; the REST layer translates that
  into a ``422`` response.
- :class:`HybridDecisionProcessor` additionally accepts
  :class:`RejectDecision`, producing a ``REJECTED_BY_HUMAN`` outcome so
  callers can fall back to a different strategy.
"""

from typing import Final, assert_never
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
from synthorg.communication.errors import (
    EscalationDecisionAgentError,
    EscalationDecisionShapeError,
)
from synthorg.core.clock import Clock, SystemClock

# ``NotBlankStr`` MUST be imported at runtime (not under TYPE_CHECKING)
# so PEP 649 lazy annotation evaluation can resolve
# ``decided_by: NotBlankStr`` when frameworks / ``typing.get_type_hints``
# introspect the public ``process()`` signature; ruff TC001's
# "move into type-checking block" hint is incorrect for this symbol.
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.observability import get_logger
from synthorg.observability.events.conflict import (
    CONFLICT_ESCALATION_DECISION_FAILED,
)

logger = get_logger(__name__)

_SYSTEM_CLOCK: Clock = SystemClock()

# Dissent ids are ``f"dissent-{uuid4().hex[:N]}"``; N=12 keeps the id
# short enough for human-readable audit lines while preserving ~48 bits
# of entropy per row -- enough to avoid collisions within a single
# escalation's position set without inflating the audit payload.
_DISSENT_ID_HEX_LEN: Final[int] = 12

_NO_WINNER_OUTCOMES = frozenset(
    {
        ConflictResolutionOutcome.ESCALATED_TO_HUMAN,
        ConflictResolutionOutcome.REJECTED_BY_HUMAN,
    },
)


def _build_dissent_records_from_resolution(
    conflict: Conflict,
    resolution: ConflictResolution,
    *,
    clock: Clock | None = None,
) -> tuple[DissentRecord, ...]:
    """Emit one dissent record per non-winning position.

    For outcomes without a winner (escalated / rejected), every
    position is recorded so auditors keep the full stance history.
    """
    effective_clock = clock or _SYSTEM_CLOCK
    if resolution.outcome in _NO_WINNER_OUTCOMES:
        targets = conflict.positions
    else:
        targets = tuple(
            p for p in conflict.positions if p.agent_id != resolution.winning_agent_id
        )
    timestamp = effective_clock.now()
    return tuple(
        DissentRecord(
            id=f"dissent-{uuid4().hex[:_DISSENT_ID_HEX_LEN]}",
            conflict=conflict,
            resolution=resolution,
            dissenting_agent_id=pos.agent_id,
            dissenting_position=pos.position,
            strategy_used=ConflictResolutionStrategy.HUMAN,
            timestamp=timestamp,
            metadata=(("escalation_reason", "human_review_required"),),
        )
        for pos in targets
    )


def _build_winner_resolution(
    conflict: Conflict,
    decision: WinnerDecision,
    *,
    decided_by: NotBlankStr,
    clock: Clock | None = None,
) -> ConflictResolution:
    """Build a RESOLVED_BY_HUMAN resolution from a winner decision.

    The success path does not emit ``CONFLICT_ESCALATION_RESOLVED`` --
    callers persist the resolution first and log post-write so the
    audit trail only records committed transitions.

    Raises:
        EscalationDecisionAgentError: ``decision.winning_agent_id`` does
            not match any agent in ``conflict.positions``.
    """
    effective_clock = clock or _SYSTEM_CLOCK
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
            CONFLICT_ESCALATION_DECISION_FAILED,
            conflict_id=conflict.id,
            decided_by=decided_by,
            winning_agent_id=decision.winning_agent_id,
            strategy=ConflictResolutionStrategy.HUMAN.value,
            note="winner_agent_not_in_conflict",
        )
        raise EscalationDecisionAgentError(msg)
    return ConflictResolution(
        conflict_id=conflict.id,
        outcome=ConflictResolutionOutcome.RESOLVED_BY_HUMAN,
        winning_agent_id=decision.winning_agent_id,
        winning_position=winning_position,
        decided_by=decided_by,
        reasoning=decision.reasoning,
        resolved_at=effective_clock.now(),
    )


def _build_reject_resolution(
    conflict: Conflict,
    decision: RejectDecision,
    *,
    decided_by: NotBlankStr,
    clock: Clock | None = None,
) -> ConflictResolution:
    """Build a REJECTED_BY_HUMAN resolution from a reject decision.

    The success path does not emit ``CONFLICT_ESCALATION_RESOLVED`` --
    callers persist the resolution first and log post-write so the
    audit trail only records committed transitions.
    """
    effective_clock = clock or _SYSTEM_CLOCK
    return ConflictResolution(
        conflict_id=conflict.id,
        outcome=ConflictResolutionOutcome.REJECTED_BY_HUMAN,
        winning_agent_id=None,
        winning_position=None,
        decided_by=decided_by,
        reasoning=decision.reasoning,
        resolved_at=effective_clock.now(),
    )


class WinnerOnlyDecisionProcessor:
    """Safest decision surface: only :class:`WinnerDecision` is accepted.

    A :class:`RejectDecision` arriving at this processor raises
    :class:`EscalationDecisionShapeError`. The REST layer maps the
    domain error to a ``422 Unprocessable Entity`` response.
    """

    __slots__ = ()

    def process(
        self,
        conflict: Conflict,
        decision: EscalationDecision,
        *,
        decided_by: NotBlankStr,
        clock: Clock | None = None,
    ) -> ConflictResolution:
        """Build a resolution matching the decision.

        Raises:
            EscalationDecisionShapeError: ``decision`` is a
                :class:`RejectDecision`; this processor only accepts
                :class:`WinnerDecision`.
            EscalationDecisionAgentError: ``decision`` is a
                :class:`WinnerDecision` whose ``winning_agent_id`` is
                not in the conflict.
        """
        if isinstance(decision, WinnerDecision):
            return _build_winner_resolution(
                conflict,
                decision,
                decided_by=decided_by,
                clock=clock,
            )
        # An unknown subclass of ``EscalationDecision`` (e.g. a future
        # variant added alongside ``WinnerDecision`` / ``RejectDecision``)
        # must not silently fall through; detect ``RejectDecision``
        # explicitly and raise on anything else.
        if isinstance(decision, RejectDecision):
            msg = (
                "WinnerOnlyDecisionProcessor only accepts 'winner' "
                "decisions. Configure decision_strategy='hybrid' to "
                "allow 'reject' decisions."
            )
            logger.warning(
                CONFLICT_ESCALATION_DECISION_FAILED,
                conflict_id=conflict.id,
                decided_by=decided_by,
                decision_type=getattr(decision, "type", type(decision).__name__),
                strategy=ConflictResolutionStrategy.HUMAN.value,
                note="winner_mode_rejected_reject_decision",
            )
            raise EscalationDecisionShapeError(msg)
        # ``EscalationDecision`` is a closed union of the two variants
        # above; ``assert_never`` proves exhaustiveness to the type
        # checker and raises ``AssertionError`` if a new variant is
        # introduced without updating this branch.
        assert_never(decision)

    def build_dissent_records(
        self,
        conflict: Conflict,
        resolution: ConflictResolution,
        *,
        clock: Clock | None = None,
    ) -> tuple[DissentRecord, ...]:
        """Build dissent records covering all non-winning positions."""
        return _build_dissent_records_from_resolution(
            conflict,
            resolution,
            clock=clock,
        )


class HybridDecisionProcessor:
    """Accepts both :class:`WinnerDecision` and :class:`RejectDecision`.

    Reject decisions resolve to ``REJECTED_BY_HUMAN`` so the caller can
    fall back to a different strategy (retry, alternative resolution,
    manual intervention).
    """

    __slots__ = ()

    def process(
        self,
        conflict: Conflict,
        decision: EscalationDecision,
        *,
        decided_by: NotBlankStr,
        clock: Clock | None = None,
    ) -> ConflictResolution:
        """Build a resolution matching the decision.

        Raises:
            EscalationDecisionAgentError: ``decision`` is a
                :class:`WinnerDecision` whose ``winning_agent_id`` is
                not in the conflict.
        """
        if isinstance(decision, WinnerDecision):
            return _build_winner_resolution(
                conflict,
                decision,
                decided_by=decided_by,
                clock=clock,
            )
        if isinstance(decision, RejectDecision):
            return _build_reject_resolution(
                conflict,
                decision,
                decided_by=decided_by,
                clock=clock,
            )
        assert_never(decision)

    def build_dissent_records(
        self,
        conflict: Conflict,
        resolution: ConflictResolution,
        *,
        clock: Clock | None = None,
    ) -> tuple[DissentRecord, ...]:
        """Build dissent records covering all non-winning positions."""
        return _build_dissent_records_from_resolution(
            conflict,
            resolution,
            clock=clock,
        )
