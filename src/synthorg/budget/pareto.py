"""Cost / quality Pareto frontier models + analyzer.

The Pareto frontier is the operator-facing answer to "90 percent of
the quality at 40 percent of the cost if you downgrade these roles".
:class:`ParetoAnalyzer` walks the current per-role model assignments
+ observed costs and produces a :class:`ParetoFrontier` ranked by
``cost_saving_pct`` so the dashboard can render "biggest wins first".

Quality scores come from a :class:`BenchmarkScoreProvider` (see
:mod:`synthorg.budget.benchmark_protocol`). A model with no measured
score is skipped rather than assigned a fabricated number, and the
provenance of each scored point is surfaced verbatim via
:attr:`ParetoPoint.source` so the dashboard shows the real source of
every measured row.
"""

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from synthorg.budget.benchmark_protocol import (
    BenchmarkScoreProvider,
)
from synthorg.budget.config import (
    BudgetConfig,
)
from synthorg.budget.model_capability import (
    ModelCapabilityMap,
    heuristic_is_local,
    resolve_capability,
)
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger

logger = get_logger(__name__)

# Provenance label for a frontier computed with no measured scores: the
# frontier is empty and the quality axis is honestly absent, never a
# fabricated benchmark source.
_NO_MEASURED_SOURCE: Final[str] = "no-measured-scores"


class ParetoPoint(BaseModel):
    """A single downgrade candidate on the cost / quality frontier.

    Each point answers "if you downgrade ``role_id`` from
    ``current_model`` to ``candidate_model``, you lose
    ``quality_delta_pct`` of quality and save ``cost_saving_pct`` of
    cost". The :attr:`source` carries the provenance of the measured
    benchmark score used to compute the quality delta so the dashboard
    can show where each score came from.
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


def _candidate_model_id(downgrade_map: Mapping[str, str], bucket: str) -> str | None:
    """Return the downgrade target's canonical archetype model id.

    Returns:
        The resulting ``str``, or ``None`` when unavailable.
    """
    candidate = downgrade_map.get(bucket)
    if candidate is None:
        return None
    return f"example-{candidate}-001"


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
        model_capability_map: Optional operator-configured ``model_id`` to
            capability overrides so arbitrary real ids resolve a rung for
            the downgrade traversal. Defaults to an empty map (heuristic
            resolution only), so a normal boot is unchanged.
        clock: Optional clock seam returning UTC ``datetime`` for
            ``generated_at``.
    """

    __slots__ = (
        "_assignment_lookup",
        "_benchmark_provider",
        "_budget_config",
        "_clock",
        "_model_capability_map",
    )

    def __init__(
        self,
        *,
        benchmark_provider: BenchmarkScoreProvider,
        budget_config: BudgetConfig,
        assignment_lookup: RoleAssignmentLookup | None = None,
        model_capability_map: ModelCapabilityMap | None = None,
        clock: ClockFn | None = None,
    ) -> None:
        self._benchmark_provider = benchmark_provider
        self._budget_config = budget_config
        self._assignment_lookup = (
            assignment_lookup if assignment_lookup is not None else _empty_assignments
        )
        self._model_capability_map = model_capability_map
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
            ", ".join(sorted(sources)) if sources else _NO_MEASURED_SOURCE
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
        current_bucket = self._cost_bucket(assignment.current_model)
        if current_bucket is None:
            return None
        candidate_model = _candidate_model_id(downgrade_map, current_bucket)
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
            current_bucket=current_bucket,
            candidate_bucket=downgrade_map[current_bucket],
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
            # attributes a measured candidate to an unmeasured baseline.
            source=(
                current_score.source
                if current_score.source == candidate_score.source
                else f"{current_score.source} | {candidate_score.source}"
            ),
        )

    def _cost_bucket(self, model_id: str) -> str | None:
        """Resolve the cost bucket an assigned model sits in.

        Locality is asked first because an operator override names a rung
        and so cannot express it. The rung itself then goes through
        ``resolve_capability``, which reads the override map before the
        archetype heuristic: an operator who has mapped an id onto a rung
        has said something the heuristic does not get to overrule, which is
        what ``budget.model_capability_overrides`` promises.

        Returns:
            The bucket, or ``None`` when the id resolves neither way.
        """
        if heuristic_is_local(model_id):
            return "local"
        return resolve_capability(model_id, self._model_capability_map)

    def _project_candidate_cost(
        self,
        *,
        current_bucket: str,
        candidate_bucket: str,
        current_cost_per_task: float,
    ) -> float:
        """Project the candidate cost via the static-prior ratio.

        Future enhancements may consult a BaselineStore-backed
        observed ratio when sufficient history exists.

        Returns:
            Result of type ``float``.
        """
        priors: Mapping[str, float] = {
            "expert": self._budget_config.forecast_static_prior_per_turn_expert,
            "capable": self._budget_config.forecast_static_prior_per_turn_capable,
            "basic": self._budget_config.forecast_static_prior_per_turn_basic,
            "local": self._budget_config.forecast_static_prior_per_turn_local,
        }
        current_prior = priors.get(current_bucket, 0.0)
        candidate_prior = priors.get(candidate_bucket, 0.0)
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
