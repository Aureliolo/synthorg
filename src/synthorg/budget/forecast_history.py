# module-kind: code
"""CostTracker-backed historical cost-per-turn lookup for the forecaster.

Supplies the :class:`~synthorg.budget.forecaster.CostForecaster`'s
``HistoryLookup`` so its pre-flight estimate blends real observed per-turn costs
into the static prior instead of always cold-starting on the empty default.

Each productive LLM call in the react loop is one agent turn, so a productive
:class:`~synthorg.budget.cost_record.CostRecord`'s cost is a per-turn cost
observation. Records are grouped by the recording agent's CURRENT role (resolved
through the live registry) and the model's tier (resolved the same way the
forecaster derives a brief's tier), so a lookup for ``(tier, role_id)`` returns
the matching observations within the cost window.
"""

from collections import defaultdict
from collections.abc import Sequence
from datetime import timedelta
from typing import Final

from synthorg.budget._cost_window import (
    COST_WINDOW_DAYS,
    ClockFn,
    tier_from_model_id,
    utc_now,
)
from synthorg.budget.call_category import LLMCallCategory
from synthorg.budget.currency import assert_currencies_match
from synthorg.budget.tracker import CostTracker
from synthorg.core.normalization import normalize_identifier
from synthorg.hr.registry import AgentRegistryService
from synthorg.observability import get_logger

logger = get_logger(__name__)

#: Default tier for a model id the forecaster cannot classify; matches the
#: forecaster's own fallback so the lookup buckets records the same way.
_DEFAULT_TIER: Final[str] = "medium"

#: Call categories excluded from per-turn observations: only direct task work
#: (productive, or untagged legacy records) approximates an agent's turn cost.
_EXCLUDED_CATEGORIES: Final[frozenset[LLMCallCategory]] = frozenset(
    {
        LLMCallCategory.COORDINATION,
        LLMCallCategory.SYSTEM,
        LLMCallCategory.EMBEDDING,
    }
)


class CostTrackerHistoryLookup:
    """Resolve per-(tier, role) cost-per-turn history from observed spend.

    Satisfies :data:`~synthorg.budget.forecaster.HistoryLookup`. Each call
    builds a fresh ``(tier, role) -> [cost_per_turn, ...]`` index (no caching)
    from the cost-window records and the live roster, then returns the bucket
    for the requested key (empty when that role/tier has no observed productive
    spend, so the forecaster's blend collapses to the static prior for it).

    Args:
        registry: Live agent registry (active agents -> role + id).
        cost_tracker: Source of observed per-call cost records.
        clock: UTC clock seam for the cost-window lower bound.
        window_days: Width of the observed-cost window.
    """

    def __init__(
        self,
        *,
        registry: AgentRegistryService,
        cost_tracker: CostTracker,
        clock: ClockFn | None = None,
        window_days: int = COST_WINDOW_DAYS,
    ) -> None:
        if window_days < 1:
            msg = f"window_days must be >= 1, got {window_days!r}"
            raise ValueError(msg)
        self._registry = registry
        self._cost_tracker = cost_tracker
        self._clock = clock if clock is not None else utc_now
        self._window_days = window_days

    async def __call__(self, tier: str, role_id: str) -> Sequence[float]:
        """Return per-turn cost observations for ``(tier, role_id)``.

        Returns:
            Productive per-call costs recorded by agents currently in
            ``role_id`` running a ``tier`` model within the window; empty
            when there is no such observed spend.
        """
        index = await self._build_index()
        return index.get((tier, normalize_identifier(role_id)), ())

    async def _build_index(self) -> dict[tuple[str, str], tuple[float, ...]]:
        """Group windowed productive spend by (tier, current role).

        Returns:
            Mapping of ``(tier, role)`` to the per-turn cost observations.
        """
        end = self._clock()
        start = end - timedelta(days=self._window_days)
        records = await self._cost_tracker.get_records(start=start, end=end)
        if not records:
            return {}
        agents = await self._registry.list_active()
        role_by_agent = {
            str(agent.id): normalize_identifier(str(agent.role)) for agent in agents
        }
        contributing = [
            record
            for record in records
            if record.call_category not in _EXCLUDED_CATEGORIES
            and record.cost > 0
            and role_by_agent.get(str(record.agent_id)) is not None
        ]
        # Same-currency invariant: the per-turn costs about to be bucketed
        # and averaged by the forecaster must share a currency, else the
        # blended estimate is a meaningless mix. Raises (409) before any
        # aggregation runs.
        assert_currencies_match(record.currency for record in contributing)
        buckets: dict[tuple[str, str], list[float]] = defaultdict(list)
        for record in contributing:
            role = role_by_agent[str(record.agent_id)]
            tier = tier_from_model_id(record.model) or _DEFAULT_TIER
            buckets[(tier, role)].append(record.cost)
        return {key: tuple(values) for key, values in buckets.items()}


__all__ = ["COST_WINDOW_DAYS", "CostTrackerHistoryLookup"]
