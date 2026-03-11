"""Human escalation conflict resolution strategy (DESIGN_SPEC §5.6).

Strategy 3: Escalate to human for resolution.  Returns a stub
resolution with ``ESCALATED_TO_HUMAN`` outcome — actual human
approval queue integration depends on approval queue API (#37).
"""

from datetime import UTC, datetime
from uuid import uuid4

from ai_company.communication.conflict_resolution.models import (
    Conflict,
    ConflictResolution,
    ConflictResolutionOutcome,
    DissentRecord,
)
from ai_company.communication.enums import ConflictResolutionStrategy
from ai_company.observability import get_logger
from ai_company.observability.events.conflict import (
    CONFLICT_ESCALATED,
    CONFLICT_HUMAN_ESCALATION_STUB,
)

logger = get_logger(__name__)


class HumanEscalationResolver:
    """Escalate conflicts to a human for resolution.

    This is a stub implementation — the human approval queue (#37)
    is a dependency.  Callers receive a proper resolution
    object with ``ESCALATED_TO_HUMAN`` outcome; no
    ``NotImplementedError`` is raised.
    """

    __slots__ = ()

    async def resolve(self, conflict: Conflict) -> ConflictResolution:
        """Escalate the conflict to a human.

        Args:
            conflict: The conflict to escalate.

        Returns:
            Resolution with ``ESCALATED_TO_HUMAN`` outcome.
        """
        logger.warning(
            CONFLICT_HUMAN_ESCALATION_STUB,
            conflict_id=conflict.id,
            subject=conflict.subject,
        )
        logger.info(
            CONFLICT_ESCALATED,
            conflict_id=conflict.id,
            agent_count=len(conflict.positions),
        )

        return ConflictResolution(
            conflict_id=conflict.id,
            outcome=ConflictResolutionOutcome.ESCALATED_TO_HUMAN,
            winning_agent_id=None,
            winning_position=None,
            decided_by="human",
            reasoning=(
                "Conflict escalated to human for resolution. "
                "Awaiting human decision (approval queue not yet "
                "implemented — see #37)."
            ),
            resolved_at=datetime.now(UTC),
        )

    def build_dissent_records(
        self,
        conflict: Conflict,
        resolution: ConflictResolution,
    ) -> tuple[DissentRecord, ...]:
        """Build dissent records for all positions in an escalated conflict.

        For escalated conflicts, no agent "lost" — all positions
        are pending human review.  Each position gets a record to
        ensure the audit trail captures every stance.

        Args:
            conflict: The original conflict.
            resolution: The resolution decision.

        Returns:
            One dissent record per position.
        """
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
            for pos in conflict.positions
        )
