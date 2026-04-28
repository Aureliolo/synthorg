"""Shared helpers for evaluation metric extractors.

Centralizes logging and other utilities across pillar extractors
so audit trails and error handling stay consistent.
"""

from collections.abc import Mapping
from typing import TYPE_CHECKING

from synthorg.observability import get_logger
from synthorg.observability.events.evaluation import EVAL_METRIC_SKIPPED

if TYPE_CHECKING:
    from synthorg.hr.evaluation.enums import EvaluationPillar
    from synthorg.hr.evaluation.models import EvaluationContext

logger = get_logger(__name__)


def log_disabled_metrics(
    context: EvaluationContext,
    pillar: EvaluationPillar,
    metrics: tuple[str, ...] | Mapping[str, str],
    *,
    default_reason: str = "disabled_by_config",
) -> None:
    """Emit DEBUG audit trail when sub-metrics are disabled.

    Args:
        context: The evaluation context (provides ``agent_id`` for
            logging).
        pillar: The pillar being evaluated.
        metrics: Either a tuple of metric names (all disabled for
            the same ``default_reason``) or a mapping of
            ``metric_name -> reason`` when callers need to
            distinguish reasons per metric (e.g. Efficiency
            distinguishes ``disabled_by_config`` from
            ``disabled_by_resolver`` so the operator audit trail
            points at the right cause).
        default_reason: Reason emitted for tuple-form callers.
    """
    if isinstance(metrics, Mapping):
        for metric, reason in metrics.items():
            logger.debug(
                EVAL_METRIC_SKIPPED,
                agent_id=context.agent_id,
                pillar=pillar.value,
                metric=metric,
                reason=reason,
            )
    else:
        for metric in metrics:
            logger.debug(
                EVAL_METRIC_SKIPPED,
                agent_id=context.agent_id,
                pillar=pillar.value,
                metric=metric,
                reason=default_reason,
            )
