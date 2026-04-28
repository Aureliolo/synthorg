"""Performance/Efficiency pillar metric extractor.

Lifts cost / time / token efficiency sub-metrics from the
performance snapshot's 30d window (or 7d fallback). Honors the
runtime kill-switch resolver added by #1648 for the cost and
latency sub-metrics:

- ``hr.evaluation_cost_enabled`` gates the ``cost`` sub-metric.
- ``hr.evaluation_latency_enabled`` gates the ``time`` sub-metric.
- The token sub-metric uses the YAML-baked ``cfg.tokens_enabled``
  only (no resolver flag is defined for tokens).

When no resolver is wired the extractor falls back to the
YAML-baked ``cfg.cost_enabled`` / ``cfg.time_enabled`` fields, so
standalone construction still honours the documented defaults.
"""

import asyncio
from typing import TYPE_CHECKING

from synthorg.hr.evaluation.constants import MAX_SCORE
from synthorg.hr.evaluation.enums import EvaluationPillar
from synthorg.hr.evaluation.extractors._shared import log_disabled_metrics
from synthorg.hr.evaluation.metric_extractor_protocol import ExtractedMetrics
from synthorg.settings.kill_switch import resolve_bool_with_fallback

if TYPE_CHECKING:
    from synthorg.hr.evaluation.config import EfficiencyConfig
    from synthorg.hr.evaluation.models import EvaluationContext
    from synthorg.hr.performance.models import WindowMetrics
    from synthorg.settings.resolver import ConfigResolver


class EfficiencyMetricExtractor:
    """Extract cost / time / tokens efficiency sub-metrics.

    Accepts an optional ``ConfigResolver`` so the operator-facing
    cost/latency kill switches added in #1648 keep working after
    the inline-Efficiency block is hoisted out of ``EvaluationService``.
    """

    __slots__ = ("_config_resolver",)

    def __init__(self, config_resolver: ConfigResolver | None = None) -> None:
        self._config_resolver = config_resolver

    @property
    def pillar(self) -> EvaluationPillar:
        """Which pillar this extractor produces metrics for."""
        return EvaluationPillar.EFFICIENCY

    async def extract(self, context: EvaluationContext) -> ExtractedMetrics:
        """Pick a window, gate cost/latency via resolver, emit sub-metrics."""
        cfg = context.config.efficiency
        window_map = {w.window_size: w for w in context.snapshot.windows}
        window = window_map.get("30d") or window_map.get("7d")

        if window is None or window.data_point_count == 0:
            return ExtractedMetrics(
                insufficient_data=True,
                insufficient_data_event_kwargs={"reason": "no_window_data"},
            )

        scores, weights = await self._compute_sub_scores(context, cfg, window)
        if not weights:
            return ExtractedMetrics(
                insufficient_data=True,
                insufficient_data_event_kwargs={
                    "reason": "no_enabled_metrics_with_data",
                },
                neutral_data_point_count=window.data_point_count,
            )

        return ExtractedMetrics(
            scores=scores,
            weights=weights,
            data_points=window.data_point_count,
        )

    async def _compute_sub_scores(
        self,
        context: EvaluationContext,
        cfg: EfficiencyConfig,
        window: WindowMetrics,
    ) -> tuple[dict[str, float], dict[str, float]]:
        """Compute cost / time / tokens sub-scores; gate cost+time via resolver."""
        # Mirror the prior inline implementation: cost and latency
        # lookups are independent so a TaskGroup saves a resolver
        # round-trip per evaluation.
        async with asyncio.TaskGroup() as tg:
            cost_task = tg.create_task(
                resolve_bool_with_fallback(
                    resolver=self._config_resolver,
                    namespace="hr",
                    key="evaluation_cost_enabled",
                    fallback=cfg.cost_enabled,
                ),
            )
            latency_task = tg.create_task(
                resolve_bool_with_fallback(
                    resolver=self._config_resolver,
                    namespace="hr",
                    key="evaluation_latency_enabled",
                    fallback=cfg.time_enabled,
                ),
            )
        cost_enabled = cost_task.result()
        latency_enabled = latency_task.result()

        # Audit-trail: emit DEBUG for sub-metrics gated off by either
        # YAML config or the resolver-backed kill switches.
        disabled_metrics = tuple(
            metric
            for metric, enabled in (
                ("cost", cost_enabled),
                ("time", latency_enabled),
                ("tokens", cfg.tokens_enabled),
            )
            if not enabled
        )
        if disabled_metrics:
            log_disabled_metrics(
                context,
                EvaluationPillar.EFFICIENCY,
                disabled_metrics,
            )

        scores: dict[str, float] = {}
        weights: dict[str, float] = {}

        if cost_enabled and window.avg_cost_per_task is not None:
            score = max(
                0.0,
                MAX_SCORE * (1.0 - window.avg_cost_per_task / cfg.reference_cost),
            )
            scores["cost"] = min(MAX_SCORE, score)
            weights["cost"] = cfg.cost_weight

        if latency_enabled and window.avg_completion_time_seconds is not None:
            score = max(
                0.0,
                MAX_SCORE
                * (
                    1.0
                    - window.avg_completion_time_seconds / cfg.reference_time_seconds
                ),
            )
            scores["time"] = min(MAX_SCORE, score)
            weights["time"] = cfg.time_weight

        if cfg.tokens_enabled and window.avg_tokens_per_task is not None:
            score = max(
                0.0,
                MAX_SCORE * (1.0 - window.avg_tokens_per_task / cfg.reference_tokens),
            )
            scores["tokens"] = min(MAX_SCORE, score)
            weights["tokens"] = cfg.tokens_weight

        return scores, weights
