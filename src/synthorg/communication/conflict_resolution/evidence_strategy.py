"""Evidence-weighted conflict resolution strategy.

Strategy 5: a deterministic, no-LLM synthesizer that scores every
position by the strength of its reasoning (see :mod:`._evidence`) and
selects the best-supported one. Ties on evidence score break toward the
more senior agent, then stably toward the first-stated position. Each
overruled position's weighted evidence is preserved on its dissent
record so a later review can see what the minority view was backed by.
"""

from datetime import UTC, datetime
from uuid import uuid4

from synthorg.communication.conflict_resolution._evidence import (
    extract_evidence,
    score_position,
)
from synthorg.communication.conflict_resolution._helpers import find_losers
from synthorg.communication.conflict_resolution.models import (
    Conflict,
    ConflictPosition,
    ConflictResolution,
    ConflictResolutionOutcome,
    DissentRecord,
)
from synthorg.communication.enums import ConflictResolutionStrategy
from synthorg.core.authority import compare_authority
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.conflict import CONFLICT_EVIDENCE_DECIDED

logger = get_logger(__name__)


class EvidenceWeightedResolver:
    """Resolve conflicts by the strongest-supported position.

    Stateless: scoring is a pure function of each position's reasoning, so
    a single instance is safe to share across conflicts.
    """

    __slots__ = ()

    async def resolve(self, conflict: Conflict) -> ConflictResolution:
        """Resolve by evidence weight -- best-supported position wins.

        Args:
            conflict: The conflict to resolve.

        Returns:
            Resolution with the ``RESOLVED_BY_EVIDENCE`` outcome.
        """
        winner = self._pick_winner(conflict)
        winner_score = score_position(winner)
        non_winners = [p for p in conflict.positions if p.agent_id != winner.agent_id]
        logger.info(
            CONFLICT_EVIDENCE_DECIDED,
            conflict_id=str(conflict.id),
            winner=winner.agent_id,
            winner_score=winner_score,
            losers=[p.agent_id for p in non_winners],
        )
        return ConflictResolution(
            conflict_id=str(conflict.id),
            outcome=ConflictResolutionOutcome.RESOLVED_BY_EVIDENCE,
            winning_agent_id=winner.agent_id,
            winning_position=winner.position,
            decided_by=NotBlankStr("evidence_weighted"),
            reasoning=NotBlankStr(
                f"Evidence-weighted decision: {winner.agent_id} carried the "
                f"strongest support (score {winner_score:.2f})"
            ),
            resolved_at=datetime.now(UTC),
        )

    def build_dissent_records(
        self,
        conflict: Conflict,
        resolution: ConflictResolution,
    ) -> tuple[DissentRecord, ...]:
        """Build dissent records carrying each loser's weighted evidence.

        Args:
            conflict: The original conflict.
            resolution: The resolution decision.

        Returns:
            One dissent record per overruled agent, each with its
            ``minority_evidence`` populated.
        """
        losers = find_losers(conflict, resolution)
        return tuple(
            DissentRecord(
                id=uuid4(),
                conflict=conflict,
                resolution=resolution,
                dissenting_agent_id=loser.agent_id,
                dissenting_position=loser.position,
                strategy_used=ConflictResolutionStrategy.EVIDENCE_WEIGHTED,
                timestamp=datetime.now(UTC),
                minority_evidence=extract_evidence(loser.reasoning),
            )
            for loser in losers
        )

    @staticmethod
    def _pick_winner(conflict: Conflict) -> ConflictPosition:
        """Pick the highest-scoring position, breaking ties deterministically.

        Higher evidence score wins; an exact tie favours the more senior
        agent, and a remaining tie keeps the first-stated position (stable).

        Returns:
            The winning position.
        """
        best = conflict.positions[0]
        best_score = score_position(best)
        for pos in conflict.positions[1:]:
            pos_score = score_position(pos)
            if pos_score > best_score or (
                pos_score == best_score
                and compare_authority(pos.agent_role, best.agent_role) > 0
            ):
                best = pos
                best_score = pos_score
        return best
