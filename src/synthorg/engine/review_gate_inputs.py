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

The attempt under review supplies its own closing message when the caller
has it. A review driven from a finished run is holding that run's
``ExecutionResult``, so asking a separate store what the run delivered
introduces a second owner of the answer, and the store is an
observability one: a recorder failure would read exactly like an agent
that delivered nothing, sending real work to rework as if it were empty.
It also mis-answers a checkpoint-resumed attempt, whose recovered turns
are not in the store while the pre-recovery FAILED attempt's are, so the
highest recorded turn is the failed one and the gate judges that. The
store stays as the fallback for a detached read, where nobody is holding
the run.

Kept out of ``review_gate.py`` so the gate module imports neither the
persistence protocol nor the red-team models package at module scope
and stays within its size budget. ``build`` returns ``None`` when no
reviewable deliverable exists; the gate maps that to its
``on_missing_deliverable`` policy rather than silently passing, so a
configured security gate never depends on the flight recorder being on.
"""

import json
from collections.abc import Awaitable, Callable, Mapping
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.autonomy_enums import AutonomyLevel
from synthorg.core.redteam_review_input import (
    DeliverableArtifact,
    RedTeamReviewInput,
)
from synthorg.core.task import Task
from synthorg.core.types import NotBlankStr
from synthorg.engine.artifacts.deliverable_content import (
    ARTIFACT_STATUS_READ,
    DeliverableReader,
)
from synthorg.engine.loop_protocol import ExecutionResult
from synthorg.observability import get_logger
from synthorg.observability.events.deliverable import DELIVERABLE_NOT_REVIEWABLE
from synthorg.persistence.flight_recorder_protocol import (
    FlightRecorderFrameFilterSpec,
    FlightRecorderFrameRepository,
)
from synthorg.providers.enums import MessageRole

logger = get_logger(__name__)

#: Async callable returning the effective company autonomy level.
AutonomyProvider = Callable[[], Awaitable[AutonomyLevel]]


class AttemptDeliverable(BaseModel):
    """What the attempt under review delivered, from the attempt itself.

    Attributes:
        execution_id: The run that produced it.
        closing_message: Its final agent-authored message. Agent-authored
            and therefore untrusted, fenced by each consumer at its own
            prompt boundary exactly as the recorded value is.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    execution_id: NotBlankStr = Field(description="The run that produced this")
    closing_message: NotBlankStr = Field(description="Its final agent message")


def attempt_deliverable(result: ExecutionResult) -> AttemptDeliverable | None:
    """Read the closing message off the run that just finished.

    The last assistant message is what the terminal frame records, so the
    in-hand and recorded answers are the same value by construction rather
    than by two definitions that could drift.

    Args:
        result: The attempt whose review is about to run.

    Returns:
        The attempt's deliverable, or ``None`` when it authored nothing.
    """
    for message in reversed(result.context.conversation):
        if message.role is not MessageRole.ASSISTANT:
            continue
        content = message.content
        if content and content.strip():
            return AttemptDeliverable(
                execution_id=NotBlankStr(result.context.execution_id),
                closing_message=NotBlankStr(content),
            )
    return None


#: Outcome of consulting the workspace, when there was nothing to consult.
#: Reported rather than omitted, so the reviewer reads the reason instead of
#: an absent key it would have to interpret.
_ARTIFACTS_NONE_DECLARED: Final[str] = "none_declared"
_ARTIFACTS_UNAVAILABLE: Final[str] = "not_verified"


def _typed_artifacts(section: object) -> tuple[DeliverableArtifact, ...]:
    """Lift the files that were genuinely read out of the artifacts section.

    Only ``read`` entries qualify. A declaration that was absent, a directory,
    unreadable or dropped for budget produced no bytes, and a consumer asking
    a question about delivered content would otherwise be answered about a
    file that does not exist.

    Args:
        section: Whatever :meth:`DeliverableReviewInputBuilder._read_artifacts`
            returned, which is the reader's mapping or a status naming why the
            workspace could not be consulted.

    Returns:
        One entry per file that came back, in declaration order. Empty when
        the section carries no readable file, whatever the reason.
    """
    if not isinstance(section, Mapping):
        return ()
    entries = section.get("artifacts")
    if not isinstance(entries, list):
        return ()
    produced: list[DeliverableArtifact] = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        if entry.get("status") != ARTIFACT_STATUS_READ:
            continue
        path = entry.get("path")
        content = entry.get("content")
        if not isinstance(path, str) or not path.strip():
            continue
        if not isinstance(content, str):
            continue
        produced.append(DeliverableArtifact(path=NotBlankStr(path), content=content))
    return tuple(produced)


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
        attempt: AttemptDeliverable | None = None,
    ) -> None:
        self._frames = frame_repository
        self._autonomy_provider = autonomy_provider
        self._deliverable_reader = deliverable_reader
        self._attempt = attempt

    def bound_to(
        self, attempt: AttemptDeliverable | None
    ) -> DeliverableReviewInputBuilder:
        """Return a builder that answers for one specific attempt.

        A copy rather than a mutation, because the builder is a shared
        service and one review must never see another's attempt. Returns
        ``self`` unchanged when there is no attempt to bind, so the detached
        path keeps reading the recorded copy.

        Args:
            attempt: What the run under review delivered, or ``None``.

        Returns:
            A builder bound to ``attempt``, or ``self``.
        """
        if attempt is None:
            return self
        return DeliverableReviewInputBuilder(
            frame_repository=self._frames,
            autonomy_provider=self._autonomy_provider,
            deliverable_reader=self._deliverable_reader,
            attempt=attempt,
        )

    async def build(self, task: Task) -> RedTeamReviewInput | None:
        """Build the gate input for ``task``, or ``None`` when not reviewable.

        Args:
            task: The task under review.

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
        if self._attempt is not None:
            execution_id = self._attempt.execution_id
            summary = self._attempt.closing_message
        else:
            deliverable = await self._latest_deliverable(str(task.id))
            if deliverable is None:
                # WARNING, not INFO: on the path where a caller held the run
                # this means the recorder is the only witness and it has
                # nothing, and the gate is about to treat delivered work as
                # empty. That is a fact about the system, not about the task.
                self._log_missing(
                    "no_recorded_deliverable", str(task.id), is_fault=True
                )
                return None
            execution_id, summary = deliverable
        autonomy = await self._autonomy_provider()
        # Read once and derive both halves from it: a second read could see a
        # different workspace, and then the typed artifacts and the composed
        # document would be two accounts of one delivery.
        artifacts = await self._read_artifacts(task)
        return RedTeamReviewInput(
            task_id=str(task.id),
            execution_id=execution_id,
            deliverable_content=self._compose(summary=summary, artifacts=artifacts),
            agent_summary=summary,
            acceptance_criteria=criteria,
            assigned_agent_id=task.assigned_to,
            autonomy=autonomy,
            project_id=task.project,
            stakes=task.stakes,
            estimated_complexity=task.estimated_complexity,
            produced_artifacts=_typed_artifacts(artifacts),
        )

    def _compose(self, *, summary: str, artifacts: object) -> str:
        """Render the produced artifacts plus the agent's closing message.

        The two halves occupy separate keys of one JSON document rather
        than being concatenated under text headings. A heading is
        forgeable: a produced file whose body spells the heading, or a
        second artifact delimiter, would present itself to the reviewer
        as further delivered work. A key cannot be forged from inside a
        value.

        Args:
            summary: The agent's closing message.
            artifacts: The section :meth:`_read_artifacts` produced.

        Returns:
            The reviewable deliverable, as a JSON document.
        """
        return json.dumps(
            {
                "agent_closing_message": summary,
                "produced_artifacts": artifacts,
            }
        )

    async def _read_artifacts(self, task: Task) -> object:
        """Read the task's declared artifacts from its project workspace.

        Always returns something. Omitting the key on failure would make
        "this task declared no deliverable" and "this task declared one and
        nobody could look" the same absence, and a reviewer that cannot
        tell those apart approves the second on the strength of the agent's
        own closing prose, which is the reading this module exists to stop.

        Returns:
            The reader's section when the workspace could be consulted, or a
            mapping naming why it could not.
        """
        if not task.artifacts_expected:
            return {"status": _ARTIFACTS_NONE_DECLARED}
        if self._deliverable_reader is None:
            return {"status": _ARTIFACTS_UNAVAILABLE, "reason": "no_reader_wired"}
        project_id = str(task.project)
        if not project_id.strip():
            return {"status": _ARTIFACTS_UNAVAILABLE, "reason": "no_project_workspace"}
        section = await self._deliverable_reader(project_id, task.artifacts_expected)
        if section is None:
            return {"status": _ARTIFACTS_UNAVAILABLE, "reason": "reader_returned_none"}
        return section

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
    def _log_missing(reason: str, task_id: str, *, is_fault: bool = False) -> None:
        """Log why no review input could be built for ``task_id``.

        Args:
            reason: Which condition stopped the build.
            task_id: The task it was building for.
            is_fault: Whether this says something is wrong with the SYSTEM
                rather than with the task. A task with no acceptance criteria
                is ordinary and reads at INFO; a deliverable nobody can
                retrieve means the gate is about to judge delivered work as
                empty, which nothing downstream can distinguish from an agent
                that produced nothing, so it is not an ordinary event.
        """
        log = logger.warning if is_fault else logger.info
        log(
            DELIVERABLE_NOT_REVIEWABLE,
            task_id=task_id,
            reason=reason,
        )
