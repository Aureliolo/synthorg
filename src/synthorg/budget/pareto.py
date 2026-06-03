"""Cost / quality Pareto frontier models + analyzer.

The Pareto frontier is the operator-facing answer to "90 percent of
the quality at 40 percent of the cost if you downgrade these roles".
:class:`ParetoAnalyzer` walks the current per-role model assignments
+ observed costs and produces a :class:`ParetoFrontier` ranked by
``cost_saving_pct`` so the dashboard can render "biggest wins first".

Quality scores come from a :class:`BenchmarkScoreProvider` (see
:mod:`synthorg.budget.benchmark_protocol`). :class:`StubBenchmarkScoreProvider`
supplies calibrated per-tier constants pending a measured benchmark
integration; a real provider swaps in behind the protocol with no
analyzer change, and the provenance is surfaced verbatim via
:attr:`ParetoPoint.source` so the dashboard never mistakes stub data
for measured data.
"""

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field

from synthorg.budget.benchmark_protocol import (
    BenchmarkScoreProvider,
)
from synthorg.budget.config import (
    BudgetConfig,
)
from synthorg.budget.model_tier import ModelTierMap, resolve_tier
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger

logger = get_logger(__name__)


class ParetoPoint(BaseModel):
    """A single downgrade candidate on the cost / quality frontier.

    Each point answers "if you downgrade ``role_id`` from
    ``current_model`` to ``candidate_model``, you lose
    ``quality_delta_pct`` of quality and save ``cost_saving_pct`` of
    cost". The :attr:`source` carries the provenance of the benchmark
    score used to compute the quality delta so the dashboard can flag
    stub data versus measured data.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    role_id: NotBlankStr = Field(description="Identifier of the role")
    role_label: NotBlankStr = Field(description="Human-readable role label")
    current_model: NotBlankStr = Field(description="Canonical model id in use")
    candidate_model: NotBlankStr = Field(description="Canonical model id of downgrade")
    quality_delta_pct: float = Field(
        ge=0.0,
        le=100.0,
        description="Percent of quality lost (0 to 100)",
    )
    cost_saving_pct: float = Field(
        ge=0.0,
        le=100.0,
        description="Percent of cost saved (0 to 100)",
    )
    source: NotBlankStr = Field(description="Benchmark-score provenance identifier")


class ParetoFrontier(BaseModel):
    """The full cost / quality frontier for a company at a moment in time.

    Points are returned sorted by ``cost_saving_pct`` descending so the
    dashboard's "biggest wins first" rendering is a direct iteration.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    points: tuple[ParetoPoint, ...] = Field(
        default=(),
        description="Frontier points sorted by cost_saving_pct descending",
    )
    generated_at: datetime = Field(description="When the analyzer ran")
    baseline_window_size: int = Field(
        ge=1,
        description="Historical records consulted",
    )
    source: NotBlankStr = Field(description="Aggregate provenance identifier")


class RoleAssignment(BaseModel):
    """Per-role current model assignment + observed cost-per-task.

    Consumed by :class:`ParetoAnalyzer`; produced by the work-pipeline
    / coordination subsystem at boot. The analyzer is purely
    structural and never reads the agent registry directly.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    role_id: NotBlankStr = Field(description="Stable role identifier")
    role_label: NotBlankStr = Field(description="Human-readable label")
    current_model: NotBlankStr = Field(description="Canonical model id in use")
    current_cost_per_task: float = Field(
        ge=0.0,
        description="Observed mean cost-per-task on the current model",
    )


RoleAssignmentLookup = Callable[[], Awaitable[Sequence[RoleAssignment]]]
"""Async callable returning per-role current model assignments + costs."""

ClockFn = Callable[[], datetime]


async def _empty_assignments() -> Sequence[RoleAssignment]:
    """Default :data:`RoleAssignmentLookup` returning no assignments.

    Returns:
        Result of type ``Sequence[RoleAssignment]``.
    """
    return ()


def _utc_now() -> datetime:
    """Utc now.

    Returns:
        Result of type ``datetime``.
    """
    return datetime.now(UTC)


def _candidate_model_id(downgrade_map: Mapping[str, str], tier: str) -> str | None:
    """Return the downgrade target's tier-aligned canonical model id.

    Returns:
        The resulting ``str``, or ``None`` when unavailable.
    """
    candidate_tier = downgrade_map.get(tier)
    if candidate_tier is None:
        return None
    return f"example-{candidate_tier}-001"


class ParetoAnalyzer:
    """Produces a cost / quality frontier for the dashboard.

    Args:
        benchmark_provider: Source of per-model benchmark scores.
        budget_config: Live budget configuration (drives the static
            prior ratio + auto-downgrade map).
        assignment_lookup: Async callable returning per-role current
            model assignments + observed costs. Defaults to "no
            assignments" for tests and cold-start; production wiring
            uses an ``AgentRegistry`` + ``BaselineStore`` adapter.
        model_tier_map: Optional operator-configured ``model_id`` to
            tier overrides so arbitrary real ids resolve a tier for the
            downgrade traversal. Defaults to an empty map (heuristic
            resolution only), so a normal boot is unchanged.
        clock: Optional clock seam returning UTC ``datetime`` for
            ``generated_at``.
    """

    __slots__ = (
        "_assignment_lookup",
        "_benchmark_provider",
        "_budget_config",
        "_clock",
        "_model_tier_map",
    )

    def __init__(
        self,
        *,
        benchmark_provider: BenchmarkScoreProvider,
        budget_config: BudgetConfig,
        assignment_lookup: RoleAssignmentLookup | None = None,
        model_tier_map: ModelTierMap | None = None,
        clock: ClockFn | None = None,
    ) -> None:
        self._benchmark_provider = benchmark_provider
        self._budget_config = budget_config
        self._assignment_lookup = (
            assignment_lookup if assignment_lookup is not None else _empty_assignments
        )
        self._model_tier_map = model_tier_map
        self._clock = clock if clock is not None else _utc_now

    async def analyse(self) -> ParetoFrontier:
        """Compute the current cost / quality frontier.

        Returns:
            Result of type ``ParetoFrontier``.
        """
        assignments = await self._assignment_lookup()
        downgrade_map: Mapping[str, str] = dict(
            self._budget_config.auto_downgrade.downgrade_map,
        )

        # Each assignment is evaluated independently; fan them out so a
        # company with many roles does not pay the sum of the per-role
        # benchmark-lookup latencies.
        async with asyncio.TaskGroup() as tg:
            eval_tasks = [
                tg.create_task(self._evaluate(assignment, downgrade_map))
                for assignment in assignments
            ]

        points: list[ParetoPoint] = []
        sources: set[str] = set()
        for task in eval_tasks:
            point = task.result()
            if point is None:
                continue
            points.append(point)
            sources.add(point.source)

        points.sort(key=lambda p: p.cost_saving_pct, reverse=True)
        aggregate_source = (
            ", ".join(sorted(sources)) if sources else "stub:calibrated-v1"
        )
        return ParetoFrontier(
            points=tuple(points),
            generated_at=self._clock(),
            baseline_window_size=max(1, len(assignments)),
            source=aggregate_source,
        )

    async def _evaluate(
        self,
        assignment: RoleAssignment,
        downgrade_map: Mapping[str, str],
    ) -> ParetoPoint | None:
        """Evaluate a single role assignment for a frontier candidate.

        Returns:
            The resulting ``ParetoPoint``, or ``None`` when unavailable.
        """
        current_tier = resolve_tier(assignment.current_model, self._model_tier_map)
        if current_tier is None:
            return None
        candidate_model = _candidate_model_id(downgrade_map, current_tier)
        if candidate_model is None:
            return None
        current_score = await self._benchmark_provider.get_score(
            assignment.current_model,
        )
        candidate_score = await self._benchmark_provider.get_score(
            candidate_model,
        )
        if current_score is None or candidate_score is None:
            return None
        if assignment.current_cost_per_task <= 0:
            return None
        quality_delta = max(0.0, current_score.score - candidate_score.score)
        candidate_cost = self._project_candidate_cost(
            current_tier=current_tier,
            candidate_tier=downgrade_map[current_tier],
            current_cost_per_task=assignment.current_cost_per_task,
        )
        cost_saving_pct = (
            (assignment.current_cost_per_task - candidate_cost)
            / assignment.current_cost_per_task
            * 100.0
        )
        if cost_saving_pct <= 0:
            return None
        return ParetoPoint(
            role_id=assignment.role_id,
            role_label=assignment.role_label,
            current_model=assignment.current_model,
            candidate_model=candidate_model,
            quality_delta_pct=min(quality_delta, 100.0),
            cost_saving_pct=min(cost_saving_pct, 100.0),
            # quality_delta_pct blends both scores; surface both
            # provenances when they differ so the dashboard never
            # attributes a measured candidate to a stub baseline.
            source=(
                current_score.source
                if current_score.source == candidate_score.source
                else f"{current_score.source} | {candidate_score.source}"
            ),
        )

    def _project_candidate_cost(
        self,
        *,
        current_tier: str,
        candidate_tier: str,
        current_cost_per_task: float,
    ) -> float:
        """Project the candidate cost via the static-prior ratio.

        Future enhancements may consult a BaselineStore-backed
        observed ratio when sufficient history exists.

        Returns:
            Result of type ``float``.
        """
        priors: Mapping[str, float] = {
            "large": self._budget_config.forecast_static_prior_per_turn_large,
            "medium": self._budget_config.forecast_static_prior_per_turn_medium,
            "small": self._budget_config.forecast_static_prior_per_turn_small,
            "local-small": (
                self._budget_config.forecast_static_prior_per_turn_local_small
            ),
        }
        current_prior = priors.get(current_tier, 0.0)
        candidate_prior = priors.get(candidate_tier, 0.0)
        # A non-positive prior on either side means the static-prior ratio
        # cannot project a meaningful cost; fall back to the observed cost
        # (zero saving) rather than emitting a collapsed/invalid figure.
        if current_prior <= 0 or candidate_prior <= 0:
            return current_cost_per_task
        ratio = candidate_prior / current_prior
        return current_cost_per_task * ratio


__all__ = [
    "ClockFn",
    "ParetoAnalyzer",
    "ParetoFrontier",
    "ParetoPoint",
    "RoleAssignment",
    "RoleAssignmentLookup",
]
