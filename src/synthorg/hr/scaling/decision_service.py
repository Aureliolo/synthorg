"""MCP-facing scaling decision service.

Wraps :class:`ScalingService` so the MCP tools
``synthorg_scaling_list_decisions`` / ``_get_decision`` /
``_get_config`` / ``_trigger`` share one enforced contract over the
scaling pipeline.

``ScalingService`` already keeps a bounded in-memory deque of recent
decisions (``_recent_decisions``, maxlen=100). We expose that through
a ``(page, total)`` interface that matches the rest of the MCP
surface without touching the service's internals; ``get_decision``
walks the same deque since it is the canonical record for recent
history.
"""

from synthorg.core.types import NotBlankStr
from synthorg.hr.scaling.config import ScalingConfig
from synthorg.hr.scaling.models import ScalingDecision
from synthorg.hr.scaling.service import ScalingService
from synthorg.observability import get_logger
from synthorg.observability.events.hr import (
    HR_SCALING_CONTROLLER_INVALID_REQUEST,
    HR_SCALING_MANUAL_TRIGGER_REQUESTED,
)

logger = get_logger(__name__)


class ScalingDecisionService:
    """Read + manual-trigger facade over :class:`ScalingService`.

    Constructor:
        scaling: The underlying scaling service (holds strategies,
            guard chain, and recent-decision history).
    """

    __slots__ = ("_scaling",)

    def __init__(
        self,
        *,
        scaling: ScalingService,
    ) -> None:
        """Initialise with the scaling service dependency."""
        self._scaling = scaling

    async def list_decisions(
        self,
        *,
        offset: int,
        limit: int,
    ) -> tuple[tuple[ScalingDecision, ...], int]:
        """Return a newest-first page of recent scaling decisions.

        The underlying deque retains the last 100 decisions. ``total``
        reflects the deque length (not a global historical count)
        which is the contract ``get_recent_decisions`` already offers.

        Args:
            offset: Page offset (>= 0).
            limit: Page size (> 0).

        Returns:
            Tuple of ``(page, total)`` with newest-first ordering.

        Raises:
            ValueError: If ``offset`` is negative or ``limit`` is not
                strictly positive.
        """
        if offset < 0:
            msg = f"offset must be >= 0, got {offset}"
            logger.warning(
                HR_SCALING_CONTROLLER_INVALID_REQUEST,
                param="offset",
                value=offset,
                surface="mcp.list_decisions",
            )
            raise ValueError(msg)
        if limit < 1:
            msg = f"limit must be >= 1, got {limit}"
            logger.warning(
                HR_SCALING_CONTROLLER_INVALID_REQUEST,
                param="limit",
                value=limit,
                surface="mcp.list_decisions",
            )
            raise ValueError(msg)
        decisions = self._scaling.get_recent_decisions()
        # ``get_recent_decisions`` returns oldest-first (deque order).
        # Reverse for the MCP surface so handlers get newest-first.
        newest_first = tuple(reversed(decisions))
        page = newest_first[offset : offset + limit]
        return page, len(newest_first)

    async def get_decision(
        self,
        decision_id: NotBlankStr,
    ) -> ScalingDecision | None:
        """Fetch a specific decision from the recent-history deque.

        Returns:
            The matching :class:`ScalingDecision`, or ``None`` if the
            id is unknown / has already aged out of the deque.
        """
        for decision in self._scaling.get_recent_decisions():
            if str(decision.id) == str(decision_id):
                return decision
        return None

    async def get_config(self) -> ScalingConfig:
        """Return the current scaling configuration.

        Returns:
            Result of type ``ScalingConfig``.
        """
        return self._scaling.config

    async def trigger(
        self,
        agent_ids: tuple[NotBlankStr, ...],
    ) -> tuple[ScalingDecision, ...]:
        """Run a scaling evaluation cycle for *agent_ids*.

        Delegates to :meth:`ScalingService.evaluate` which applies the
        full strategy chain + guards. The returned tuple contains the
        filtered decisions ready for execution; MCP callers decide
        whether to execute them via a separate entry point (not
        exposed here).

        Args:
            agent_ids: Active agent identifiers to evaluate.

        Returns:
            The filtered :class:`ScalingDecision` tuple.
        """
        logger.info(
            HR_SCALING_MANUAL_TRIGGER_REQUESTED,
            agent_count=len(agent_ids),
            surface="mcp.scaling.trigger",
        )
        return await self._scaling.evaluate(agent_ids=agent_ids)


__all__ = ["ScalingDecisionService"]
