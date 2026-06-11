# module-kind: code
"""Live role-assignment lookup for the cost / quality Pareto frontier.

Sources the per-role current model assignment from the live
``AgentRegistry`` and the observed per-model cost from the
``CostTracker``, so :class:`~synthorg.budget.pareto.ParetoAnalyzer`
renders a real frontier in production instead of the empty default.

Kept in ``budget/`` so the analyzer stays purely structural (it never
reads the registry or the tracker directly). A role whose current model
has no observed spend in the window is omitted: the analyzer cannot
project a saving without a positive cost baseline, so a frontier point
for it would be meaningless.
"""

from collections import Counter, defaultdict
from collections.abc import Sequence
from datetime import timedelta

from synthorg.budget._cost_window import COST_WINDOW_DAYS, ClockFn, utc_now
from synthorg.budget.pareto import RoleAssignment
from synthorg.budget.tracker import CostTracker
from synthorg.hr.registry import AgentRegistryService
from synthorg.observability import get_logger

logger = get_logger(__name__)


class AgentRegistryAssignmentLookup:
    """Build ``RoleAssignment``s from the live registry + cost tracker.

    Satisfies :data:`~synthorg.budget.pareto.RoleAssignmentLookup`. Each
    active role contributes one assignment whose ``current_model`` is the
    model the most active agents in that role currently run on, and whose
    ``current_cost_per_task`` is the mean observed per-record cost for
    that model.

    Args:
        registry: Live agent registry (active agents -> role + model).
        cost_tracker: Source of observed per-model spend.
        clock: UTC clock seam for the cost-window lower bound.
    """

    def __init__(
        self,
        *,
        registry: AgentRegistryService,
        cost_tracker: CostTracker,
        clock: ClockFn | None = None,
    ) -> None:
        self._registry = registry
        self._cost_tracker = cost_tracker
        self._clock = clock if clock is not None else utc_now

    async def __call__(self) -> Sequence[RoleAssignment]:
        """Return one assignment per active role with observed spend.

        Returns:
            Per-role assignments whose representative model has observed
            spend in the window; roles with no observed cost are omitted.
        """
        agents = await self._registry.list_active()
        if not agents:
            return ()
        mean_cost = await self._mean_cost_per_model()
        models_by_role: dict[str, Counter[str]] = defaultdict(Counter)
        for agent in agents:
            models_by_role[str(agent.role)][agent.model.model_id] += 1
        assignments: list[RoleAssignment] = []
        for role, model_counts in models_by_role.items():
            model_id, _ = model_counts.most_common(1)[0]
            cost = mean_cost.get(model_id, 0.0)
            if cost <= 0:
                continue
            assignments.append(
                RoleAssignment(
                    role_id=role,
                    role_label=role,
                    current_model=model_id,
                    current_cost_per_task=cost,
                ),
            )
        return tuple(assignments)

    async def _mean_cost_per_model(self) -> dict[str, float]:
        """Compute mean observed cost per model over the cost window.

        Returns:
            Mapping of canonical model id to mean per-record cost; empty
            when no spend was recorded in the window.
        """
        end = self._clock()
        start = end - timedelta(days=COST_WINDOW_DAYS)
        records = await self._cost_tracker.get_records(start=start, end=end)
        totals: dict[str, float] = defaultdict(float)
        counts: dict[str, int] = defaultdict(int)
        for record in records:
            totals[record.model] += record.cost
            counts[record.model] += 1
        return {model: totals[model] / counts[model] for model in totals}


__all__ = ["AgentRegistryAssignmentLookup"]
