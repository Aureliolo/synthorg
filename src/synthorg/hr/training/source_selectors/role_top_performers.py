"""Role-based top performers source selector.

Selects the top N agents in the new hire's role, ranked by recent
quality score from the performance tracker.
"""

import asyncio
from typing import TYPE_CHECKING

from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.training import (
    HR_TRAINING_SELECTION_COMPLETE,
    HR_TRAINING_SELECTION_SKIPPED,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from synthorg.core.agent import AgentIdentity
    from synthorg.core.enums import SeniorityLevel
    from synthorg.core.types import NotBlankStr
    from synthorg.hr.performance.models import AgentPerformanceSnapshot
    from synthorg.hr.performance.tracker import PerformanceTracker
    from synthorg.hr.registry import AgentRegistryService

logger = get_logger(__name__)

_DEFAULT_TOP_N = 3


class RoleTopPerformers:
    """Select top N performers in the new hire's role.

    Queries the registry for active agents matching the role,
    fetches performance snapshots concurrently, and returns the
    top N by overall quality score.

    Args:
        registry: Agent registry service.
        tracker: Performance tracker.
        top_n: Number of top performers to select (must be positive).
    """

    def __init__(
        self,
        *,
        registry: AgentRegistryService,
        tracker: PerformanceTracker,
        top_n: int = _DEFAULT_TOP_N,
    ) -> None:
        if top_n <= 0:
            msg = f"top_n must be a positive integer, got {top_n}"
            logger.warning(msg, top_n=top_n)
            raise ValueError(msg)
        self._registry = registry
        self._tracker = tracker
        self._top_n = top_n

    @property
    def name(self) -> str:
        """Selector strategy name."""
        return "role_top_performers"

    async def select(
        self,
        *,
        new_agent_role: NotBlankStr,
        new_agent_level: SeniorityLevel,  # noqa: ARG002
        new_agent_department: NotBlankStr | None = None,  # noqa: ARG002
    ) -> tuple[NotBlankStr, ...]:
        """Select top N agents in the same role by quality score.

        Args:
            new_agent_role: Role of the new hire.
            new_agent_level: Seniority level (unused, reserved).
            new_agent_department: Department of the new hire (unused).

        Returns:
            Agent IDs ordered by quality score descending.
        """
        active = await self._registry.list_active()
        role_lower = str(new_agent_role).lower()
        candidates = [a for a in active if str(a.role).lower() == role_lower]

        if not candidates:
            logger.debug(
                HR_TRAINING_SELECTION_SKIPPED,
                selector="role_top_performers",
                role=str(new_agent_role),
                candidates=0,
            )
            return ()

        snapshots = await self._fetch_snapshots(candidates)
        scored: list[tuple[float, str]] = [
            (
                snapshot.overall_quality_score
                if snapshot.overall_quality_score is not None
                else 0.0,
                str(agent.id),
            )
            for agent, snapshot in zip(candidates, snapshots, strict=True)
        ]

        scored.sort(key=lambda x: x[0], reverse=True)
        selected = tuple(agent_id for _, agent_id in scored[: self._top_n])

        logger.info(
            HR_TRAINING_SELECTION_COMPLETE,
            selector="role_top_performers",
            role=str(new_agent_role),
            candidates=len(candidates),
            selected=len(selected),
        )
        return selected

    async def _fetch_snapshots(
        self,
        candidates: Sequence[AgentIdentity],
    ) -> list[AgentPerformanceSnapshot]:
        """Fetch quality snapshots for all candidates concurrently."""

        async def _fetch_one(
            agent: AgentIdentity,
        ) -> AgentPerformanceSnapshot:
            try:
                return await self._tracker.get_snapshot(str(agent.id))
            except Exception as exc:
                logger.warning(
                    HR_TRAINING_SELECTION_SKIPPED,
                    selector="role_top_performers",
                    agent_id=str(agent.id),
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise

        async with asyncio.TaskGroup() as tg:
            tasks = [tg.create_task(_fetch_one(agent)) for agent in candidates]
        return [task.result() for task in tasks]
