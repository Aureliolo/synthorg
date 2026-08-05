# module-kind: code
"""Builds the review gates' input from a completed task.

The review gate fires the completion oracle's peer reviewer and the
adversarial red-team gate on the production completion path (human
approve -> COMPLETED). Both need a ``RedTeamReviewInput`` carrying the
deliverable and the execution that produced it.

The deliverable is what the task promised: the content of the files at
its declared artifact paths. The agent's closing message travels
*alongside* it as its own field, never instead of it, because a reviewer
given only the closing message approves a convincing summary rather than
working code.

Both halves are agent-authored, and every consumer fences the composed
value at its own prompt boundary: the completion-oracle and red-team
prompts wrap it as an untrusted artifact, and the grounding checker
truncates then wraps it as task data. This builder therefore does not
fence again. A second fence here would nest an inner tag inside the one
the prompts tell the model about, and the grounding path's
truncate-then-wrap would be free to cut the inner closing tag off.

A task that declared no artifacts, or whose workspace cannot be read,
still yields a document: the artifacts section says which of the two it
was, so a reviewer can tell "nothing was promised" from "could not
verify" instead of silently receiving prose alone.

Kept out of ``review_gate.py`` so the gate module imports neither the
persistence protocol nor the red-team models package at module scope
and stays within its size budget. ``build`` returns ``None`` when no
reviewable deliverable exists; the gate maps that to its
``on_missing_deliverable`` policy rather than silently passing, so a
configured security gate never depends on the flight recorder being on.
"""

import json
from collections.abc import Awaitable, Callable

from synthorg.core.autonomy_enums import AutonomyLevel
from synthorg.core.redteam_review_input import RedTeamReviewInput
from synthorg.core.task import Task
from synthorg.engine.artifacts.deliverable_content import DeliverableReader
from synthorg.observability import get_logger
from synthorg.observability.events.deliverable import DELIVERABLE_NOT_REVIEWABLE
from synthorg.persistence.flight_recorder_protocol import (
    FlightRecorderFrameFilterSpec,
    FlightRecorderFrameRepository,
)

logger = get_logger(__name__)

#: Async callable returning the effective company autonomy level.
AutonomyProvider = Callable[[], Awaitable[AutonomyLevel]]


class DeliverableReviewInputBuilder:
    """Assemble a ``RedTeamReviewInput`` for a completed task.

    The deliverable is the content of the task's declared artifacts, with
    the terminal flight-recorder frame's closing message alongside it. The
    ``execution_id`` comes from that frame. ``build`` returns ``None``
    when no reviewable deliverable exists (no assignee, no acceptance
    criteria, no recorded frame, or nothing readable to review).

    Args:
        frame_repository: Authoritative flight-recorder frame store.
        autonomy_provider: Async callable returning the effective
            company autonomy level (drives the gate's severity-tiered
            routing).
        deliverable_reader: Reads the declared artifacts from the
            project's workspace. ``None`` leaves the reviewer with only
            the recorded closing message.
    """

    def __init__(
        self,
        *,
        frame_repository: FlightRecorderFrameRepository,
        autonomy_provider: AutonomyProvider,
        deliverable_reader: DeliverableReader | None = None,
    ) -> None:
        self._frames = frame_repository
        self._autonomy_provider = autonomy_provider
        self._deliverable_reader = deliverable_reader

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
        execution_id, summary = deliverable
        autonomy = await self._autonomy_provider()
        return RedTeamReviewInput(
            task_id=str(task.id),
            execution_id=execution_id,
            deliverable_content=await self._compose(task, summary=summary),
            agent_summary=summary,
            acceptance_criteria=criteria,
            assigned_agent_id=task.assigned_to,
            autonomy=autonomy,
            project_id=task.project,
        )

    async def _compose(self, task: Task, *, summary: str) -> str:
        """Render the produced artifacts plus the agent's closing message.

        The two halves occupy separate keys of one JSON document rather
        than being concatenated under text headings. A heading is
        forgeable: a produced file whose body spells the heading, or a
        second artifact delimiter, would present itself to the reviewer
        as further delivered work. A key cannot be forged from inside a
        value.

        Returns:
            The reviewable deliverable, as a JSON document.
        """
        document: dict[str, object] = {"agent_closing_message": summary}
        artifacts = await self._read_artifacts(task)
        if artifacts is not None:
            document["produced_artifacts"] = artifacts
        return json.dumps(document)

    async def _read_artifacts(self, task: Task) -> object | None:
        """Read the task's declared artifacts from its project workspace.

        Returns:
            The artifacts section, or ``None`` when there is no reader, no
            project, or nothing declared.
        """
        if self._deliverable_reader is None or not task.artifacts_expected:
            return None
        project_id = str(task.project)
        if not project_id.strip():
            return None
        return await self._deliverable_reader(project_id, task.artifacts_expected)

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
            DELIVERABLE_NOT_REVIEWABLE,
            task_id=task_id,
            reason=reason,
        )
