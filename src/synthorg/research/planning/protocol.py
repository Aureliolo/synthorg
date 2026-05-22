"""Query-planner protocol.

A planner decomposes a :class:`ResearchBrief` into a
:class:`ResearchQueryPlan` of targeted sub-queries. Implementations return
the plan plus the USD cost they incurred, so the orchestrator can enforce
the brief's cost ceiling.
"""

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from synthorg.research.models import ResearchBrief, ResearchQueryPlan


@runtime_checkable
class QueryPlanner(Protocol):
    """Decomposes a research brief into targeted sub-queries."""

    async def plan(self, brief: ResearchBrief) -> tuple[ResearchQueryPlan, float]:
        """Return a query plan and the USD cost of producing it.

        Args:
            brief: The research brief to decompose.

        Returns:
            A ``(plan, cost_usd)`` pair. The plan's sub-queries target only
            the brief's enabled sources and number at most
            ``brief.max_subqueries``.
        """
        ...
