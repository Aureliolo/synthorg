"""Shared helpers for evaluation metric extractors.

Centralizes logging and other utilities across pillar extractors
so audit trails and error handling stay consistent.
"""

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
    metrics: tuple[str, ...],
) -> None:
    """Emit DEBUG audit trail when sub-metrics are disabled by config.

    Args:
        context: The evaluation context (provides agent_id for logging).
        pillar: The pillar being evaluated.
        metrics: Names of disabled sub-metrics to audit.
    """
    for metric in metrics:
        logger.debug(
            EVAL_METRIC_SKIPPED,
            agent_id=context.agent_id,
            pillar=pillar.value,
            metric=metric,
            reason="disabled_by_config",
        )
