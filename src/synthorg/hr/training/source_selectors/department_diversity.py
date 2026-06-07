"""Department diversity sampling source selector.

Selects a mix of top performers and complementary-role agents
from the new hire's department.
"""

import asyncio
from typing import TYPE_CHECKING, Final

from synthorg.core.normalization import compare_ci
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.training import (
    HR_TRAINING_SELECTION_COMPLETE,
    HR_TRAINING_SELECTION_SKIPPED,
    HR_TRAINING_SELECTOR_CONFIG_INVALID,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from synthorg.core.agent import AgentIdentity
    from synthorg.hr.performance.models import AgentPerformanceSnapshot
    from synthorg.hr.performance.tracker import PerformanceTracker
    from synthorg.hr.registry import AgentRegistryService
    from synthorg.hr.seniority import SeniorityLevel

logger = get_logger(__name__)

_DEFAULT_TOP_PERFORMER_COUNT: Final[int] = 2
_DEFAULT_COMPLEMENTARY_COUNT: Final[int] = 2


class DepartmentDiversitySampling:
    """Select a diverse sample from the new hire's department.

    Splits department agents into same-role (top performers) and
    different-role (complementary), then selects from each group.
    The department is taken directly from ``new_agent_department``
    when provided on the plan; otherwise it is resolved from the
    new hire's identity via the registry.

    Args:
        registry: Agent registry service.
        tracker: Performance tracker.
        top_performer_count: Number of same-role top performers.
        complementary_count: Number of different-role agents.
    """

    def __init__(
        self,
        *,
        registry: AgentRegistryService,
        tracker: PerformanceTracker,
        top_performer_count: int = _DEFAULT_TOP_PERFORMER_COUNT,
        complementary_count: int = _DEFAULT_COMPLEMENTARY_COUNT,
    ) -> None:
        if top_performer_count < 0 or complementary_count < 0:
            msg = (
                "top_performer_count and complementary_count must be "
                f">= 0, got top={top_performer_count}, "
                f"complementary={complementary_count}"
            )
            logger.warning(
                HR_TRAINING_SELECTOR_CONFIG_INVALID,
                selector="department_diversity",
                top_performer_count=top_performer_count,
                complementary_count=complementary_count,
                reason=msg,
            )
            raise ValueError(msg)
        self._registry = registry
        self._tracker = tracker
        self._top_performer_count = top_performer_count
        self._complementary_count = complementary_count

    @property
    def name(self) -> str:
        """Selector strategy name."""
        return "department_diversity"

    async def select(
        self,
        *,
        new_agent_role: NotBlankStr,
        new_agent_level: SeniorityLevel,  # noqa: ARG002
        new_agent_department: NotBlankStr | None = None,
    ) -> tuple[NotBlankStr, ...]:
        """Select diverse agents from the department.

        Args:
            new_agent_role: Role of the new hire.
            new_agent_level: Seniority level (unused, reserved).
            new_agent_department: Department of the new hire.  When
                ``None`` the selector returns an empty tuple rather
                than guessing the department from other agents.

        Returns:
            Agent IDs mixing top performers and complementary roles.
        """
        if new_agent_department is None:
            logger.warning(
                HR_TRAINING_SELECTION_SKIPPED,
                selector="department_diversity",
                role=str(new_agent_role),
                reason="missing_department",
            )
            return ()

        department = str(new_agent_department)
        role = str(new_agent_role)

        dept_agents = await self._registry.list_by_department(department)
        if not dept_agents:
            logger.debug(
                HR_TRAINING_SELECTION_SKIPPED,
                selector="department_diversity",
                department=department,
                candidates=0,
            )
            return ()

        same_role = [a for a in dept_agents if compare_ci(str(a.role), role)]
        diff_role = [a for a in dept_agents if not compare_ci(str(a.role), role)]

        top_performers = await self._rank_by_quality(
            same_role,
            self._top_performer_count,
        )
        complementary = await self._rank_by_quality(
            diff_role,
            self._complementary_count,
        )

        # Merge and deduplicate preserving order.
        seen: set[str] = set()
        result: list[NotBlankStr] = []
        for agent_id in (*top_performers, *complementary):
            if agent_id not in seen:
                seen.add(agent_id)
                result.append(NotBlankStr(agent_id))

        logger.info(
            HR_TRAINING_SELECTION_COMPLETE,
            selector="department_diversity",
            department=department,
            top_performers=len(top_performers),
            complementary=len(complementary),
        )
        return tuple(result)

    async def _rank_by_quality(
        self,
        agents: Sequence[AgentIdentity],
        limit: int,
    ) -> tuple[str, ...]:
        """Rank agents by quality score and return top N IDs.

        Returns:
            Tuple of ``str``.
        """
        if not agents or limit <= 0:
            return ()

        snapshots = await self._fetch_snapshots(agents)
        scored: list[tuple[float, str]] = [
            (
                snapshot.overall_quality_score
                if snapshot.overall_quality_score is not None
                else 0.0,
                str(agent.id),
            )
            for agent, snapshot in zip(agents, snapshots, strict=True)
        ]

        scored.sort(key=lambda x: x[0], reverse=True)
        return tuple(agent_id for _, agent_id in scored[:limit])

    async def _fetch_snapshots(
        self,
        agents: Sequence[AgentIdentity],
    ) -> list[AgentPerformanceSnapshot]:
        """Fetch quality snapshots for all agents concurrently.

        Returns:
            List of ``AgentPerformanceSnapshot``.
        """

        async def _fetch_one(
            agent: AgentIdentity,
        ) -> AgentPerformanceSnapshot:
            """Fetch one.

            Returns:
                Result of type ``AgentPerformanceSnapshot``.

            Raises:
                Exception: Raised when the relevant invariant fails.
            """
            try:
                return await self._tracker.get_snapshot(str(agent.id))
            except Exception as exc:
                logger.warning(
                    HR_TRAINING_SELECTION_SKIPPED,
                    selector="department_diversity",
                    agent_id=str(agent.id),
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise

        async with asyncio.TaskGroup() as tg:
            tasks = [tg.create_task(_fetch_one(agent)) for agent in agents]
        return [task.result() for task in tasks]
