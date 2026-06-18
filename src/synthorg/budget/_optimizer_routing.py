"""Routing / downgrade analysis mixin for ``CostOptimizer``.

Owns ``recommend_downgrades``, ``suggest_routing_optimizations``,
``_build_recommendations``, ``_find_routing_suggestions``, and
``_compute_budget_pressure``. Relies on ``_cost_tracker``,
``_budget_config``, ``_config``, and ``_model_resolver`` declared on the
concrete service.
"""

import asyncio
from datetime import datetime

from synthorg.budget._aggregation import group_by_agent
from synthorg.budget._optimizer_helpers import (
    _build_downgrade_recommendation,
    _build_efficiency_from_records,
    _find_most_used_model,
)
from synthorg.budget.billing import billing_period_start
from synthorg.budget.config import BudgetConfig
from synthorg.budget.cost_record import CostRecord
from synthorg.budget.currency import format_cost
from synthorg.budget.optimizer_models import (
    CostOptimizerConfig,
    DowngradeAnalysis,
    DowngradeRecommendation,
    EfficiencyAnalysis,
    EfficiencyRating,
    RoutingOptimizationAnalysis,
    RoutingSuggestion,
)
from synthorg.budget.tracker import CostTracker
from synthorg.budget.tracker_protocol import collect_all_records
from synthorg.constants import BUDGET_ROUNDING_PRECISION
from synthorg.observability import get_logger
from synthorg.observability.events.cfo import (
    CFO_DOWNGRADE_RECOMMENDED,
    CFO_DOWNGRADE_SKIPPED,
    CFO_EFFICIENCY_ANALYSIS_COMPLETE,
    CFO_INVALID_TIME_RANGE,
    CFO_RESOLVER_MISSING,
    CFO_ROUTING_OPTIMIZATION_COMPLETE,
)
from synthorg.providers.routing.models import ResolvedModel
from synthorg.providers.routing.resolver import ModelResolver

logger = get_logger(__name__)


class _CostOptimizerRoutingMixin:
    """Routing / downgrade analysis for ``CostOptimizer``."""

    _cost_tracker: CostTracker
    _budget_config: BudgetConfig
    _config: CostOptimizerConfig
    _model_resolver: ModelResolver | None

    async def recommend_downgrades(
        self,
        *,
        start: datetime,
        end: datetime,
    ) -> DowngradeAnalysis:
        """Recommend model downgrades for inefficient agents.

        Runs efficiency analysis and uses the model resolver and
        downgrade map to find cheaper alternatives.

        Args:
            start: Inclusive period start.
            end: Exclusive period end.

        Returns:
            Downgrade analysis with recommendations. Empty when no
            model_resolver is configured.

        Raises:
            ValueError: If ``start >= end``.
        """
        if start >= end:
            logger.warning(
                CFO_INVALID_TIME_RANGE,
                error="start_after_end",
                start=start.isoformat(),
                end=end.isoformat(),
            )
            msg = f"start ({start.isoformat()}) must be before end ({end.isoformat()})"
            raise ValueError(msg)

        if self._model_resolver is None:
            logger.warning(
                CFO_RESOLVER_MISSING,
                reason="no_model_resolver_configured",
            )
            budget_pressure = await self._compute_budget_pressure()
            return DowngradeAnalysis(
                recommendations=(),
                budget_pressure_percent=budget_pressure,
            )

        async with asyncio.TaskGroup() as tg:
            records_task = tg.create_task(
                collect_all_records(self._cost_tracker, start=start, end=end),
            )
            pressure_task = tg.create_task(self._compute_budget_pressure())

        records = records_task.result()
        budget_pressure = pressure_task.result()

        efficiency = _build_efficiency_from_records(
            records,
            start=start,
            end=end,
            threshold_factor=self._config.inefficiency_threshold_factor,
            lower_bound_factor=self._config.efficiency_lower_bound_factor,
        )

        logger.info(
            CFO_EFFICIENCY_ANALYSIS_COMPLETE,
            agent_count=len(efficiency.agents),
            inefficient_count=efficiency.inefficient_agent_count,
            global_avg_cost_per_1k=efficiency.global_avg_cost_per_1k,
        )

        by_agent = group_by_agent(records)
        recommendations = self._build_recommendations(
            efficiency=efficiency,
            by_agent=by_agent,
        )

        return DowngradeAnalysis(
            recommendations=tuple(recommendations),
            budget_pressure_percent=budget_pressure,
        )

    async def suggest_routing_optimizations(
        self,
        *,
        start: datetime,
        end: datetime,
    ) -> RoutingOptimizationAnalysis:
        """Suggest routing optimizations based on actual usage patterns.

        Analyzes each agent's most-used model and suggests cheaper
        alternatives available through the model resolver, comparing by
        cost and context window size.

        Unlike ``recommend_downgrades`` which only targets INEFFICIENT
        agents, this method analyzes all agents and suggests cheaper
        alternatives regardless of efficiency rating -- any agent that
        could use a cheaper model is a candidate.

        Args:
            start: Inclusive period start.
            end: Exclusive period end.

        Returns:
            Routing optimization analysis with per-agent suggestions.
            Empty when no model_resolver is configured.

        Raises:
            ValueError: If ``start >= end``.
        """
        if start >= end:
            logger.warning(
                CFO_INVALID_TIME_RANGE,
                error="start_after_end",
                start=start.isoformat(),
                end=end.isoformat(),
            )
            msg = f"start ({start.isoformat()}) must be before end ({end.isoformat()})"
            raise ValueError(msg)

        if self._model_resolver is None:
            logger.warning(
                CFO_RESOLVER_MISSING,
                reason="no_model_resolver_configured",
            )
            return RoutingOptimizationAnalysis(
                suggestions=(),
                analysis_period_start=start,
                analysis_period_end=end,
                agents_analyzed=0,
            )

        records = await collect_all_records(
            self._cost_tracker,
            start=start,
            end=end,
        )

        by_agent = group_by_agent(records)
        all_models = self._model_resolver.all_models_sorted_by_cost()
        suggestions = self._find_routing_suggestions(by_agent, all_models)

        result = RoutingOptimizationAnalysis(
            suggestions=tuple(suggestions),
            analysis_period_start=start,
            analysis_period_end=end,
            agents_analyzed=len(by_agent),
        )

        logger.info(
            CFO_ROUTING_OPTIMIZATION_COMPLETE,
            suggestion_count=len(suggestions),
            agents_analyzed=len(by_agent),
            total_savings_per_1k=result.total_estimated_savings_per_1k,
        )

        return result

    def _build_recommendations(
        self,
        *,
        efficiency: EfficiencyAnalysis,
        by_agent: dict[str, list[CostRecord]],
    ) -> list[DowngradeRecommendation]:
        """Build downgrade recommendations for inefficient agents.

        Returns:
            List of ``DowngradeRecommendation``.
        """
        assert self._model_resolver is not None  # noqa: S101
        downgrade_map = dict(self._budget_config.auto_downgrade.downgrade_map)
        recommendations: list[DowngradeRecommendation] = []

        for agent in efficiency.agents:
            if agent.efficiency_rating != EfficiencyRating.INEFFICIENT:
                continue

            agent_records = by_agent.get(agent.agent_id, [])
            most_used_model = _find_most_used_model(agent_records)
            if most_used_model is None:
                logger.debug(
                    CFO_DOWNGRADE_SKIPPED,
                    agent_id=agent.agent_id,
                    reason="no_most_used_model",
                )
                continue

            recommendation = _build_downgrade_recommendation(
                agent_id=agent.agent_id,
                current_model=most_used_model,
                downgrade_map=downgrade_map,
                resolver=self._model_resolver,
                currency=self._budget_config.currency,
            )
            if recommendation is not None:
                recommendations.append(recommendation)
                logger.info(
                    CFO_DOWNGRADE_RECOMMENDED,
                    agent_id=agent.agent_id,
                    current_model=most_used_model,
                    recommended_model=recommendation.recommended_model,
                    estimated_savings=recommendation.estimated_savings_per_1k,
                )

        return recommendations

    def _find_routing_suggestions(
        self,
        by_agent: dict[str, list[CostRecord]],
        all_models: tuple[ResolvedModel, ...],
    ) -> list[RoutingSuggestion]:
        """Find routing suggestions for all agents.

        Returns:
            List of ``RoutingSuggestion``.
        """
        assert self._model_resolver is not None  # noqa: S101
        suggestions: list[RoutingSuggestion] = []
        cur = self._budget_config.currency

        for agent_id in sorted(by_agent):
            agent_records = by_agent[agent_id]
            most_used = _find_most_used_model(agent_records)
            if most_used is None:
                continue

            current_resolved = self._model_resolver.resolve_safe(most_used)
            if current_resolved is None:
                continue

            # Find cheapest model with sufficient context window
            for candidate in all_models:
                if candidate.model_id == current_resolved.model_id:
                    continue
                if candidate.total_cost_per_1k >= current_resolved.total_cost_per_1k:
                    continue
                if candidate.max_context < current_resolved.max_context:
                    continue

                cur_fmt = format_cost(
                    current_resolved.total_cost_per_1k,
                    cur,
                    precision=4,
                )
                cand_fmt = format_cost(
                    candidate.total_cost_per_1k,
                    cur,
                    precision=4,
                )
                suggestions.append(
                    RoutingSuggestion(
                        agent_id=agent_id,
                        current_model=most_used,
                        suggested_model=candidate.model_id,
                        current_cost_per_1k=round(
                            current_resolved.total_cost_per_1k,
                            BUDGET_ROUNDING_PRECISION,
                        ),
                        suggested_cost_per_1k=round(
                            candidate.total_cost_per_1k,
                            BUDGET_ROUNDING_PRECISION,
                        ),
                        reason=(
                            f"Switch from {most_used!r} "
                            f"({cur_fmt}/1k) to "
                            f"{candidate.model_id!r} "
                            f"({cand_fmt}/1k) "
                            f"-- sufficient context window, lower cost"
                        ),
                    ),
                )
                break  # Take first (cheapest) match per agent

        return suggestions

    async def _compute_budget_pressure(self) -> float:
        """Compute current budget utilization percentage.

        Returns:
            Result of type ``float``.
        """
        cfg = self._budget_config
        if cfg.total_monthly <= 0:
            return 0.0
        period_start = billing_period_start(cfg.reset_day)
        monthly_cost = await self._cost_tracker.get_total_cost(
            start=period_start,
        )
        return round(
            monthly_cost / cfg.total_monthly * 100,
            BUDGET_ROUNDING_PRECISION,
        )
