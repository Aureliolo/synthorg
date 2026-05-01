"""Decision processor strategies for the escalation queue."""

from datetime import UTC, datetime
from uuid import uuid4

from synthorg.communication.conflict_resolution.escalation.models import (
    EscalationDecision,
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
from synthorg.observability.events.conflict import (
    CONFLICT_ESCALATION_RESOLVED,
)

logger = get_logger(__name__)

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


class WinnerSelectProcessor:
    """Default strategy: accept only :class:`WinnerDecision`.

    Rejects ``RejectDecision`` with a precise ``ValueError`` so the
    REST layer can surface a 422 Unprocessable Entity instead of a 500.
    """

    __slots__ = ()

    def process(
        self,
        conflict: Conflict,
        decision: EscalationDecision,
        *,
        decided_by: str,
    ) -> ConflictResolution:
        """Build a RESOLVED_BY_HUMAN resolution from a winner decision.

        Raises:
            ValueError: ``decision`` is not a :class:`WinnerDecision`,
                or its ``winning_agent_id`` is not a participant.
        """
        if not isinstance(decision, WinnerDecision):
            # Raised as ValueError (rather than TypeError) because the
            # caller is the REST layer validating payload shapes; the
            # escalations controller translates this into a 422
            # ValidationError.
            msg = (
                "WinnerSelectProcessor only accepts 'winner' decisions. "
                "Configure decision_strategy='hybrid' to allow "
                "'reject' decisions."
            )
            logger.warning(
                CONFLICT_ESCALATION_RESOLVED,
                conflict_id=conflict.id,
                decided_by=decided_by,
                decision_type=getattr(decision, "type", type(decision).__name__),
                strategy=ConflictResolutionStrategy.HUMAN.value,
                note="winner_select_rejected_non_winner",
            )
            raise ValueError(msg)  # noqa: TRY004
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

    def build_dissent_records(
        self,
        conflict: Conflict,
        resolution: ConflictResolution,
    ) -> tuple[DissentRecord, ...]:
        """Build dissent records for the losing positions."""
        return _build_dissent_records_from_resolution(conflict, resolution)


class HybridDecisionProcessor:
    """Permissive strategy: accepts both winner and reject decisions.

    Reject decisions produce a ``REJECTED_BY_HUMAN`` outcome with no
    winner, signalling to the caller that the conflict could not be
    resolved through the human queue and another path (retry,
    fallback strategy, manual intervention) is required.
    """

    __slots__ = ()

    def process(
        self,
        conflict: Conflict,
        decision: EscalationDecision,
        *,
        decided_by: str,
    ) -> ConflictResolution:
        """Build a resolution matching the decision's discriminator."""
        if isinstance(decision, WinnerDecision):
            return WinnerSelectProcessor().process(
                conflict,
                decision,
                decided_by=decided_by,
            )
        # Union is exhaustive (``winner`` | ``reject``) per
        # :data:`EscalationDecision`.  mypy confirms no other member
        # can reach this branch.
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
            note="hybrid_processor_rejected",
        )
        return resolution

    def build_dissent_records(
        self,
        conflict: Conflict,
        resolution: ConflictResolution,
    ) -> tuple[DissentRecord, ...]:
        """Build dissent records covering all non-winning positions."""
        return _build_dissent_records_from_resolution(conflict, resolution)
