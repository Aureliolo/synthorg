"""Behavioral telemetry collaboration scoring strategy (D3).

Weighted average of 6 collaboration components. None components
have their weight redistributed proportionally.
"""

from synthorg.core.types import NotBlankStr
from synthorg.hr.performance.models import (
    CollaborationMetricRecord,
    CollaborationScoreResult,
)
from synthorg.observability import get_logger
from synthorg.observability.events.performance import PERF_COLLABORATION_SCORED

logger = get_logger(__name__)

_MAX_SCORE: float = 10.0

# Default component weights (sum to 1.0).
_DEFAULT_WEIGHTS: dict[str, float] = {
    "delegation_success": 0.25,
    "delegation_response_latency": 0.15,
    "conflict_constructiveness": 0.15,
    "meeting_contribution": 0.15,
    "loop_prevention": 0.15,
    "handoff_completeness": 0.15,
}

# Maximum expected response time in seconds for normalization.
_MAX_RESPONSE_SECONDS: float = 300.0


def _normalize_response_time(seconds: float) -> float:
    """Convert response time to a 0-10 score (lower = better)."""
    if seconds <= 0.0:
        return _MAX_SCORE
    ratio = max(0.0, 1.0 - seconds / _MAX_RESPONSE_SECONDS)
    return ratio * _MAX_SCORE


class BehavioralTelemetryStrategy:
    """Collaboration scoring based on behavioral telemetry (D3).

    Evaluates 6 default components from collaboration metric records:
        - delegation_success: Average delegation success rate.
        - delegation_response_latency: Average response time (inverted).
        - conflict_constructiveness: Average constructiveness score.
        - meeting_contribution: Average meeting contribution score.
        - loop_prevention: Inverse of loop trigger rate.
        - handoff_completeness: Average handoff completeness.

    Default weights can be overridden via the ``role_weights`` parameter
    on :meth:`score`. None values are excluded; their weight is
    redistributed proportionally among non-None components.
    """

    @property
    def name(self) -> str:
        """Human-readable strategy name."""
        return "behavioral_telemetry"

    async def score(
        self,
        *,
        agent_id: NotBlankStr,
        records: tuple[CollaborationMetricRecord, ...],
        role_weights: dict[str, float] | None = None,
    ) -> CollaborationScoreResult:
        """Score agent collaboration behavior.

        Args:
            agent_id: Agent being evaluated.
            records: Collaboration metric records to evaluate.
            role_weights: Optional per-component weight overrides.

        Returns:
            Collaboration score with component breakdown.
        """
        if not records:
            return CollaborationScoreResult(
                score=5.0,
                strategy_name=NotBlankStr(self.name),
                component_scores=(),
                confidence=0.0,
            )

        weights = dict(_DEFAULT_WEIGHTS)
        if role_weights:
            weights.update(role_weights)

        components: dict[str, float | None] = {
            "delegation_success": self._avg_delegation_success(records),
            "delegation_response_latency": self._avg_response_latency(
                records,
            ),
            "conflict_constructiveness": self._avg_conflict(records),
            "meeting_contribution": self._avg_meeting(records),
            "loop_prevention": self._loop_prevention_score(records),
            "handoff_completeness": self._avg_handoff(records),
        }

        # Filter to non-None and redistribute weights.
        available: dict[str, float] = {
            k: v for k, v in components.items() if v is not None
        }

        if not available:
            return CollaborationScoreResult(
                score=5.0,
                strategy_name=NotBlankStr(self.name),
                component_scores=(),
                confidence=0.0,
            )

        total_weight = sum(weights.get(k, 0.0) for k in available)
        if total_weight <= 0.0:
            total_weight = 1.0

        weighted_sum = sum(
            v * (weights.get(k, 0.0) / total_weight) for k, v in available.items()
        )
        final_score = max(0.0, min(_MAX_SCORE, weighted_sum))

        component_scores = tuple((k, round(v, 4)) for k, v in sorted(available.items()))
        confidence = min(1.0, len(records) / 10.0)

        result = CollaborationScoreResult(
            score=round(final_score, 4),
            strategy_name=NotBlankStr(self.name),
            component_scores=component_scores,
            confidence=round(confidence, 4),
        )

        logger.debug(
            PERF_COLLABORATION_SCORED,
            agent_id=agent_id,
            score=result.score,
            strategy=self.name,
        )
        return result

    @staticmethod
    def _avg_delegation_success(
        records: tuple[CollaborationMetricRecord, ...],
    ) -> float | None:
        """Average delegation success as 0-10 score."""
        vals = [
            r.delegation_success for r in records if r.delegation_success is not None
        ]
        if not vals:
            return None
        return (sum(1 for v in vals if v) / len(vals)) * _MAX_SCORE

    @staticmethod
    def _avg_response_latency(
        records: tuple[CollaborationMetricRecord, ...],
    ) -> float | None:
        """Average response latency as 0-10 score (lower time = higher)."""
        vals = [
            r.delegation_response_seconds
            for r in records
            if r.delegation_response_seconds is not None
        ]
        if not vals:
            return None
        avg = sum(vals) / len(vals)
        return _normalize_response_time(avg)

    @staticmethod
    def _avg_conflict(
        records: tuple[CollaborationMetricRecord, ...],
    ) -> float | None:
        """Average conflict constructiveness as 0-10 score."""
        vals = [
            r.conflict_constructiveness
            for r in records
            if r.conflict_constructiveness is not None
        ]
        if not vals:
            return None
        return (sum(vals) / len(vals)) * _MAX_SCORE

    @staticmethod
    def _avg_meeting(
        records: tuple[CollaborationMetricRecord, ...],
    ) -> float | None:
        """Average meeting contribution as 0-10 score."""
        vals = [
            r.meeting_contribution
            for r in records
            if r.meeting_contribution is not None
        ]
        if not vals:
            return None
        return (sum(vals) / len(vals)) * _MAX_SCORE

    @staticmethod
    def _loop_prevention_score(
        records: tuple[CollaborationMetricRecord, ...],
    ) -> float | None:
        """Loop prevention as 0-10 score (fewer loops = higher)."""
        if not records:
            return None
        loop_count = sum(1 for r in records if r.loop_triggered)
        ratio = 1.0 - (loop_count / len(records))
        return ratio * _MAX_SCORE

    @staticmethod
    def _avg_handoff(
        records: tuple[CollaborationMetricRecord, ...],
    ) -> float | None:
        """Average handoff completeness as 0-10 score."""
        vals = [
            r.handoff_completeness
            for r in records
            if r.handoff_completeness is not None
        ]
        if not vals:
            return None
        return (sum(vals) / len(vals)) * _MAX_SCORE
