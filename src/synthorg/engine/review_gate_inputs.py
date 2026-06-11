# module-kind: code
"""Builds the red-team gate's review input from a completed task.

The review gate fires the adversarial red-team gate on the production
completion path (human approve -> COMPLETED). The gate needs a
``RedTeamReviewInput`` carrying the deliverable text and the execution
that produced it; this module sources that from the authoritative
flight-recorder frame store (the agent's recorded output) plus the
task's own acceptance criteria and assignee.

Kept out of ``review_gate.py`` so the gate module imports neither the
persistence protocol nor the red-team models package at module scope
and stays within its size budget. ``build`` returns ``None`` when no
reviewable deliverable exists; the gate maps that to its
``on_missing_deliverable`` policy rather than silently passing, so a
configured security gate never depends on the flight recorder being on.
"""

from collections.abc import Awaitable, Callable

from synthorg.core.autonomy_enums import AutonomyLevel
from synthorg.core.redteam_review_input import RedTeamReviewInput
from synthorg.core.task import Task
from synthorg.observability import get_logger
from synthorg.observability.events.red_team import RED_TEAM_NO_DELIVERABLE
from synthorg.persistence.flight_recorder_protocol import (
    FlightRecorderFrameFilterSpec,
    FlightRecorderFrameRepository,
)

logger = get_logger(__name__)

#: Async callable returning the effective company autonomy level.
AutonomyProvider = Callable[[], Awaitable[AutonomyLevel]]


class DeliverableReviewInputBuilder:
    """Assemble a ``RedTeamReviewInput`` for a completed task.

    The deliverable text and its ``execution_id`` come from the latest
    flight-recorder frame for the task (the terminal turn's recorded
    response). ``build`` returns ``None`` when no reviewable deliverable
    exists (no assignee, no acceptance criteria, no recorded frame, or
    an empty response).

    Args:
        frame_repository: Authoritative flight-recorder frame store.
        autonomy_provider: Async callable returning the effective
            company autonomy level (drives the gate's severity-tiered
            routing).
    """

    def __init__(
        self,
        *,
        frame_repository: FlightRecorderFrameRepository,
        autonomy_provider: AutonomyProvider,
    ) -> None:
        self._frames = frame_repository
        self._autonomy_provider = autonomy_provider

    async def build(self, task: Task) -> RedTeamReviewInput | None:
        """Build the gate input for ``task``, or ``None`` when not reviewable.

        Returns:
            A ``RedTeamReviewInput`` when a deliverable is retrievable;
            ``None`` when the task has no assignee, no acceptance
            criteria, or no recorded deliverable content.
        """
        if task.assigned_to is None:
            self._log_missing("no_assignee", str(task.id))
            return None
        criteria = tuple(
            c.description for c in task.acceptance_criteria if c.description.strip()
        )
        if not criteria:
            self._log_missing("no_acceptance_criteria", str(task.id))
            return None
        deliverable = await self._latest_deliverable(str(task.id))
        if deliverable is None:
            self._log_missing("no_recorded_deliverable", str(task.id))
            return None
        execution_id, content = deliverable
        autonomy = await self._autonomy_provider()
        return RedTeamReviewInput(
            task_id=str(task.id),
            execution_id=execution_id,
            deliverable_content=content,
            acceptance_criteria=criteria,
            assigned_agent_id=task.assigned_to,
            autonomy=autonomy,
            project_id=task.project,
        )

    async def _latest_deliverable(self, task_id: str) -> tuple[str, str] | None:
        """Return ``(execution_id, content)`` for the task's latest frame.

        The aggregate identifies the most recent execution in one query;
        the terminal turn of that execution carries the deliverable (the
        agent's final recorded response).

        Returns:
            The latest execution id and its terminal response text, or
            ``None`` when no frame or no non-empty response exists.
        """
        aggregate = await self._frames.get_aggregate(
            FlightRecorderFrameFilterSpec(task_id=task_id),
        )
        execution_id = aggregate.latest_execution_id
        if execution_id is None:
            return None
        frames = await self._frames.query(
            FlightRecorderFrameFilterSpec(
                task_id=task_id,
                execution_id=execution_id,
            ),
            limit=1,
        )
        if not frames:
            return None
        content = frames[0].response_summary
        if content is None or not content.strip():
            return None
        return execution_id, content

    @staticmethod
    def _log_missing(reason: str, task_id: str) -> None:
        """Log why no review input could be built for ``task_id``."""
        logger.info(
            RED_TEAM_NO_DELIVERABLE,
            task_id=task_id,
            reason=reason,
        )
