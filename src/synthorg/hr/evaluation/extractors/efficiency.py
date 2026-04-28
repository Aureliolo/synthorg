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
        """Compute cost / time / tokens sub-scores; gate cost+time via resolver.

        Splits into three focused steps:
        1. Resolve the cost + latency runtime kill switches in parallel.
        2. Audit-log any metric disabled by config (per-pillar trail).
        3. Build the score/weight dicts via per-metric helpers.
        """
        cost_enabled, latency_enabled = await self._resolve_kill_switches(cfg)
        _audit_disabled_metrics(
            context,
            cost_enabled=cost_enabled,
            latency_enabled=latency_enabled,
            tokens_enabled=cfg.tokens_enabled,
        )
        return _build_score_weight_dicts(
            cfg=cfg,
            window=window,
            cost_enabled=cost_enabled,
            latency_enabled=latency_enabled,
        )

    async def _resolve_kill_switches(
        self,
        cfg: EfficiencyConfig,
    ) -> tuple[bool, bool]:
        """Resolve the cost + latency kill switches concurrently.

        The two lookups are independent, so a ``TaskGroup`` saves a
        resolver round-trip per evaluation versus serial awaits.

        Args:
            cfg: Efficiency config; supplies the YAML-baked fallback
                values for both flags when no resolver is wired.

        Returns:
            ``(cost_enabled, latency_enabled)``.
        """
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
        return cost_task.result(), latency_task.result()


def _audit_disabled_metrics(
    context: EvaluationContext,
    *,
    cost_enabled: bool,
    latency_enabled: bool,
    tokens_enabled: bool,
) -> None:
    """Emit DEBUG audit trail for any sub-metric gated off."""
    disabled = tuple(
        metric
        for metric, enabled in (
            ("cost", cost_enabled),
            ("time", latency_enabled),
            ("tokens", tokens_enabled),
        )
        if not enabled
    )
    if disabled:
        log_disabled_metrics(context, EvaluationPillar.EFFICIENCY, disabled)


def _build_score_weight_dicts(
    *,
    cfg: EfficiencyConfig,
    window: WindowMetrics,
    cost_enabled: bool,
    latency_enabled: bool,
) -> tuple[dict[str, float], dict[str, float]]:
    """Compute the cost / time / tokens score+weight dicts.

    Each sub-metric is added only when its toggle is on AND the
    window carries the source value. Returned dicts share keys.
    """
    scores: dict[str, float] = {}
    weights: dict[str, float] = {}

    if cost_enabled and window.avg_cost_per_task is not None:
        scores["cost"] = _normalize_to_score(
            window.avg_cost_per_task,
            cfg.reference_cost,
        )
        weights["cost"] = cfg.cost_weight

    if latency_enabled and window.avg_completion_time_seconds is not None:
        scores["time"] = _normalize_to_score(
            window.avg_completion_time_seconds,
            cfg.reference_time_seconds,
        )
        weights["time"] = cfg.time_weight

    if cfg.tokens_enabled and window.avg_tokens_per_task is not None:
        scores["tokens"] = _normalize_to_score(
            window.avg_tokens_per_task,
            cfg.reference_tokens,
        )
        weights["tokens"] = cfg.tokens_weight

    return scores, weights


def _normalize_to_score(observed: float, reference: float) -> float:
    """Convert an observed value to a 0-10 score against ``reference``.

    ``score = clamp(MAX_SCORE * (1 - observed / reference), 0, MAX_SCORE)``.
    Values at or above ``reference`` clamp to 0; values at zero clamp
    to ``MAX_SCORE``.
    """
    return min(MAX_SCORE, max(0.0, MAX_SCORE * (1.0 - observed / reference)))
