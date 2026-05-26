"""Governance pillar metric extractor.

Lifts audit-compliance / trust-level / autonomy-compliance
sub-metrics from the audit counts, trust system, and autonomy
counters in ``EvaluationContext``. Composed with
``ConfigurablePillarScorer`` to produce the governance
``PillarScoringStrategy``.

The trust-level mapping (``_TRUST_LEVEL_SCORES``) and
``_DOWNGRADE_PENALTY`` constant are preserved verbatim from the
prior ``AuditBasedGovernanceStrategy`` so behavioural parity is
exact (same trust-level numerics, same demotion penalty).
"""

from typing import TYPE_CHECKING

from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.hr.evaluation.constants import MAX_SCORE, NEUTRAL_SCORE
from synthorg.hr.evaluation.enums import EvaluationPillar
from synthorg.hr.evaluation.extractors._shared import log_disabled_metrics
from synthorg.hr.evaluation.metric_extractor_protocol import ExtractedMetrics
from synthorg.observability import get_logger
from synthorg.observability.events.evaluation import EVAL_TRUST_LEVEL_UNKNOWN

if TYPE_CHECKING:
    from synthorg.hr.evaluation.models import EvaluationContext

logger = get_logger(__name__)

# Trust level to score mapping (preserved verbatim from
# AuditBasedGovernanceStrategy).
_TRUST_LEVEL_SCORES: dict[str, float] = {
    "sandboxed": 2.5,
    "restricted": 5.0,
    "standard": 7.5,
    "elevated": 10.0,
}

# Penalty per autonomy / trust-level downgrade.
_DOWNGRADE_PENALTY: float = 2.5


class GovernanceMetricExtractor:
    """Extract audit/trust/autonomy compliance sub-metrics."""

    __slots__ = ()

    @property
    def pillar(self) -> EvaluationPillar:
        """Which pillar this extractor produces metrics for."""
        return EvaluationPillar.GOVERNANCE

    async def extract(self, context: EvaluationContext) -> ExtractedMetrics:
        """Read audit / trust / autonomy fields and emit sub-metric scores.

        Returns:
            Result of type ``ExtractedMetrics``.
        """
        cfg = context.config.governance
        total_audits = (
            context.audit_allow_count
            + context.audit_deny_count
            + context.audit_escalate_count
        )

        has_autonomy = cfg.autonomy_compliance_enabled
        if total_audits == 0 and context.trust_level is None and not has_autonomy:
            return ExtractedMetrics(
                insufficient_data=True,
                insufficient_data_event_kwargs={"reason": "no_governance_data"},
            )

        scores: dict[str, float] = {}
        weights: dict[str, float] = {}
        data_points = 0

        # Collect disabled metrics for audit logging.
        disabled_metrics: list[str] = []

        if cfg.audit_compliance_enabled and total_audits > 0:
            scores["audit_compliance"] = _audit_score(context, total_audits)
            weights["audit_compliance"] = cfg.audit_compliance_weight
            data_points += total_audits
        elif not cfg.audit_compliance_enabled:
            disabled_metrics.append("audit_compliance")

        if cfg.trust_level_enabled and context.trust_level is not None:
            scores["trust_level"] = _trust_score(context, context.trust_level)
            weights["trust_level"] = cfg.trust_level_weight
            data_points += 1
        elif not cfg.trust_level_enabled:
            disabled_metrics.append("trust_level")

        if cfg.autonomy_compliance_enabled:
            scores["autonomy_compliance"] = max(
                0.0,
                MAX_SCORE - context.autonomy_downgrades_in_window * _DOWNGRADE_PENALTY,
            )
            weights["autonomy_compliance"] = cfg.autonomy_compliance_weight
            data_points += 1
        else:
            disabled_metrics.append("autonomy_compliance")

        if disabled_metrics:
            log_disabled_metrics(
                context,
                EvaluationPillar.GOVERNANCE,
                tuple(disabled_metrics),
            )

        if not weights:
            return ExtractedMetrics(
                insufficient_data=True,
                insufficient_data_event_kwargs={
                    "reason": "no_enabled_metrics_with_data",
                },
            )

        return ExtractedMetrics(
            scores=scores,
            weights=weights,
            data_points=data_points,
        )


def _audit_score(ctx: EvaluationContext, total: int) -> float:
    """Compute audit compliance sub-score with high-risk penalty.

    Returns:
        Result of type ``float``.
    """
    compliance = ctx.audit_allow_count / total
    high_risk_penalty = ctx.audit_high_risk_count / total
    return min(
        MAX_SCORE,
        max(0.0, compliance * MAX_SCORE - high_risk_penalty * MAX_SCORE),
    )


def _trust_score(context: EvaluationContext, trust_level: NotBlankStr) -> float:
    """Compute trust-level sub-score with demotion penalty.

    Returns:
        Result of type ``float``.
    """
    trust_key = str(trust_level).lower()
    base_trust = _TRUST_LEVEL_SCORES.get(trust_key, NEUTRAL_SCORE)
    if trust_key not in _TRUST_LEVEL_SCORES:
        logger.warning(
            EVAL_TRUST_LEVEL_UNKNOWN,
            agent_id=context.agent_id,
            pillar=EvaluationPillar.GOVERNANCE.value,
            trust_level=trust_key,
            fallback_score=NEUTRAL_SCORE,
        )
    demotion_penalty = min(
        base_trust,
        context.trust_demotions_in_window * _DOWNGRADE_PENALTY,
    )
    return base_trust - demotion_penalty
