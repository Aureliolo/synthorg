# module-kind: service
"""Steering service: the single write path for mid-flight directives.

Both the operator (cockpit) and the conversational front door route through
``SteeringService.issue``. Issuing records the directive in the project brain
FIRST (so a crash cannot lose the steering history) and then, depending on the
supersede mode, either cancels operator-specified obsolete tasks immediately
(``EXPLICIT``), refines them through the pluggable proposer for confirmation
(``PROPOSE``), or does nothing (``NONE``). Cancellation always goes through the
single-writer ``TaskEngine`` and references the directive in its reason, so the
supersession is auditable.
"""

from collections.abc import Awaitable, Callable, Mapping
from typing import TYPE_CHECKING, Final

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.task_enums import TaskStatus
from synthorg.core.types import NotBlankStr
from synthorg.engine.intervention.enums import InterventionKind
from synthorg.engine.intervention.errors import (
    SteeringDirectiveFieldError,
    SteeringKindError,
    SteeringTaskProjectMismatchError,
)
from synthorg.engine.intervention.inbox import brain_entry_to_directive
from synthorg.engine.intervention.models import (
    STEERABLE_KINDS,
    STEERING_TAG,
    ActiveSteeringDirective,
    SteeringIssueResult,
    SteeringSupersessionProposal,
    SupersedeMode,
    agent_narrow_tag,
    steering_kind_tag,
    task_narrow_tag,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.cockpit import (
    STEERING_DIRECTIVE_ISSUED,
    STEERING_DIRECTIVE_REJECTED,
    STEERING_SUPERSESSION_PROPOSED,
    STEERING_TASK_SCOPE_REJECTED,
    STEERING_TASK_SUPERSEDE_FAILED,
    STEERING_TASKS_SUPERSEDED,
)
from synthorg.persistence.project_brain_protocol import (
    BrainFilterSpec,
    ProjectBrainRepository,
)
from synthorg.project_brain.models import (
    BrainEntryKind,
    BrainEntryStatus,
    PlanRevisionPayload,
)

if TYPE_CHECKING:
    from synthorg.core.task import Task
    from synthorg.engine.intervention.proposer import SteeringSupersessionProposer
    from synthorg.engine.task_engine import TaskEngine
    from synthorg.project_brain.service import ProjectBrainService

logger = get_logger(__name__)

#: Cap on candidate in-flight tasks gathered for a PROPOSE-mode refinement.
_PROPOSE_CANDIDATE_LIMIT: Final[int] = 100

#: Cap on active steering directives listed for the operator board.
_LIST_ACTIVE_LIMIT: Final[int] = 100

#: Cap on the brain entry title derived from the directive text.
_TITLE_MAX_CHARS: Final[int] = 80

SteeringNotifier = Callable[[str, Mapping[str, object]], Awaitable[None]]
"""Async callback publishing a steering WS event (event name + payload)."""


class SteeringService:
    """The single write path for mid-flight steering directives."""

    def __init__(  # noqa: PLR0913 -- collaborator wiring
        self,
        *,
        brain_service: ProjectBrainService,
        brain_repo: ProjectBrainRepository,
        task_engine: TaskEngine,
        proposer: SteeringSupersessionProposer,
        notifier: SteeringNotifier | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._brain = brain_service
        self._repo = brain_repo
        self._task_engine = task_engine
        self._proposer = proposer
        self._notifier = notifier
        self._clock: Clock = clock if clock is not None else SystemClock()

    async def issue(  # noqa: PLR0913 -- explicit directive fields
        self,
        *,
        project_id: NotBlankStr,
        kind: InterventionKind,
        text: NotBlankStr,
        author: NotBlankStr,
        narrow_task_ids: tuple[NotBlankStr, ...] = (),
        narrow_agent_ids: tuple[NotBlankStr, ...] = (),
        supersede_task_ids: tuple[NotBlankStr, ...] = (),
        supersede_mode: SupersedeMode = SupersedeMode.NONE,
    ) -> SteeringIssueResult:
        """Record a directive in the brain, then handle supersession.

        Returns:
            The :class:`SteeringIssueResult` carrying the directive id, any
            immediately-superseded task ids (``EXPLICIT``), and any
            obsolete-task proposal awaiting confirmation (``PROPOSE``).

        Raises:
            SteeringDirectiveFieldError: When ``project_id``, ``text`` or
                ``author`` is blank or whitespace-only. ``NotBlankStr`` only
                validates inside Pydantic models, so the single write path
                guards explicitly.
            SteeringKindError: When ``kind`` is not a steerable directive
                (only ``HINT`` and ``REDIRECT`` propagate into agents).
        """
        for field_name, field_value in (
            ("project_id", project_id),
            ("text", text),
            ("author", author),
        ):
            if not field_value.strip():
                logger.warning(
                    STEERING_DIRECTIVE_REJECTED,
                    project_id=project_id,
                    field=field_name,
                    reason="blank_field",
                )
                msg = f"steering directive {field_name} must not be blank"
                raise SteeringDirectiveFieldError(msg)
        if kind not in STEERABLE_KINDS:
            logger.warning(
                STEERING_DIRECTIVE_REJECTED,
                project_id=project_id,
                kind=kind.value,
                reason="non_steerable_kind",
            )
            msg = f"{kind.value!r} is not a steerable directive kind"
            raise SteeringKindError(msg)
        directive_id = await self._record_directive(
            project_id=project_id,
            kind=kind,
            text=text,
            author=author,
            narrow_task_ids=narrow_task_ids,
            narrow_agent_ids=narrow_agent_ids,
        )
        superseded, proposal = await self._handle_supersession(
            project_id=project_id,
            directive_id=directive_id,
            text=text,
            author=author,
            supersede_task_ids=supersede_task_ids,
            supersede_mode=supersede_mode,
        )
        return SteeringIssueResult(
            directive_id=directive_id,
            kind=kind,
            superseded_task_ids=superseded,
            proposal=proposal,
        )

    async def confirm_supersession(
        self,
        *,
        project_id: NotBlankStr,
        directive_id: NotBlankStr,
        task_ids: tuple[NotBlankStr, ...],
        author: NotBlankStr,
    ) -> tuple[NotBlankStr, ...]:
        """Cancel the operator-confirmed obsolete tasks for a directive.

        Returns:
            The task ids actually cancelled.
        """
        return await self._supersede_tasks(
            project_id=project_id,
            directive_id=directive_id,
            task_ids=task_ids,
            author=author,
        )

    async def list_active(
        self,
        *,
        project_id: NotBlankStr,
        limit: int = _LIST_ACTIVE_LIMIT,
    ) -> tuple[ActiveSteeringDirective, ...]:
        """Return all active steering directives for the project (operator view).

        Unlike the loop inbox, this does not narrow by task/agent: the operator
        board shows every active directive.

        Returns:
            The active directives, newest-first.
        """
        spec = BrainFilterSpec(
            project_id=project_id,
            entry_kind=BrainEntryKind.PLAN_REVISION,
            status=BrainEntryStatus.ACTIVE,
            tag=STEERING_TAG,
        )
        rows = await self._repo.list_current(spec, limit=limit, offset=0)
        directives = [
            directive
            for row in rows
            if (directive := brain_entry_to_directive(row)) is not None
        ]
        return tuple(directives)

    async def _record_directive(  # noqa: PLR0913 -- explicit directive fields
        self,
        *,
        project_id: NotBlankStr,
        kind: InterventionKind,
        text: NotBlankStr,
        author: NotBlankStr,
        narrow_task_ids: tuple[NotBlankStr, ...],
        narrow_agent_ids: tuple[NotBlankStr, ...],
    ) -> NotBlankStr:
        """Append the directive to the brain and announce it.

        Returns:
            The new brain entry's id, used as the directive id.
        """
        tags = (
            STEERING_TAG,
            steering_kind_tag(kind),
            *(task_narrow_tag(t) for t in narrow_task_ids),
            *(agent_narrow_tag(a) for a in narrow_agent_ids),
        )
        title = NotBlankStr(text[:_TITLE_MAX_CHARS])
        entry = await self._brain.append_entry(
            project_id=project_id,
            title=title,
            rationale=text,
            status=BrainEntryStatus.ACTIVE,
            author=author,
            payload=PlanRevisionPayload(summary=text),
            related_task_ids=narrow_task_ids,
            tags=tags,
        )
        directive_id = entry.entry_id
        await self._notify(
            STEERING_DIRECTIVE_ISSUED,
            {
                "project_id": project_id,
                "directive_id": directive_id,
                "kind": kind.value,
            },
        )
        return directive_id

    async def _handle_supersession(  # noqa: PLR0913 -- explicit directive fields
        self,
        *,
        project_id: NotBlankStr,
        directive_id: NotBlankStr,
        text: NotBlankStr,
        author: NotBlankStr,
        supersede_task_ids: tuple[NotBlankStr, ...],
        supersede_mode: SupersedeMode,
    ) -> tuple[tuple[NotBlankStr, ...], SteeringSupersessionProposal | None]:
        """Apply the directive's supersession mode.

        ``EXPLICIT`` cancels the given tasks immediately; ``PROPOSE`` gathers an
        obsolete-task proposal for operator confirmation and announces it;
        ``NONE`` does nothing.

        Returns:
            The immediately-superseded task ids and any proposal awaiting
            operator confirmation.
        """
        if supersede_mode is SupersedeMode.EXPLICIT and supersede_task_ids:
            superseded = await self._supersede_tasks(
                project_id=project_id,
                directive_id=directive_id,
                task_ids=supersede_task_ids,
                author=author,
            )
            return superseded, None
        if supersede_mode is SupersedeMode.PROPOSE:
            proposal = await self._propose(
                project_id=project_id,
                directive_id=directive_id,
                directive_text=text,
                seed_task_ids=supersede_task_ids,
            )
            await self._notify(
                STEERING_SUPERSESSION_PROPOSED,
                {
                    "project_id": project_id,
                    "directive_id": directive_id,
                    "proposed_task_ids": list(proposal.proposed_task_ids),
                },
            )
            return (), proposal
        return (), None

    async def _supersede_tasks(
        self,
        *,
        project_id: NotBlankStr,
        directive_id: NotBlankStr,
        task_ids: tuple[NotBlankStr, ...],
        author: NotBlankStr,
    ) -> tuple[NotBlankStr, ...]:
        """Cancel each task via the single-writer TaskEngine; log any failure.

        Ownership is validated up front: ``cancel_task`` cancels by raw id with
        no project check, so a target belonging to another project is rejected
        before anything is cancelled (no partial-batch cancellation). A missing
        task is left to the cancel loop, which logs and continues.

        Returns:
            The task ids that were cancelled successfully.

        Raises:
            SteeringTaskProjectMismatchError: When a target task exists but
                belongs to a different project than ``project_id``.
        """
        for task_id in task_ids:
            task = await self._task_engine.get_task(task_id)
            if task is not None and task.project != project_id:
                logger.warning(
                    STEERING_TASK_SCOPE_REJECTED,
                    project_id=project_id,
                    directive_id=directive_id,
                    task_id=task_id,
                    task_project=task.project,
                )
                msg = (
                    f"task {task_id!r} belongs to project {task.project!r}, "
                    f"not {project_id!r}"
                )
                raise SteeringTaskProjectMismatchError(msg)
        reason = f"Superseded by steering directive {directive_id}"
        cancelled: list[NotBlankStr] = []
        for task_id in task_ids:
            try:
                await self._task_engine.cancel_task(
                    task_id,
                    requested_by=author,
                    reason=reason,
                )
            except Exception as exc:
                reraise_critical(exc)
                logger.warning(
                    STEERING_TASK_SUPERSEDE_FAILED,
                    project_id=project_id,
                    directive_id=directive_id,
                    task_id=task_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                continue
            cancelled.append(task_id)
        if cancelled:
            await self._notify(
                STEERING_TASKS_SUPERSEDED,
                {
                    "project_id": project_id,
                    "directive_id": directive_id,
                    "task_ids": list(cancelled),
                },
            )
        return tuple(cancelled)

    async def _propose(
        self,
        *,
        project_id: NotBlankStr,
        directive_id: NotBlankStr,
        directive_text: NotBlankStr,
        seed_task_ids: tuple[NotBlankStr, ...],
    ) -> SteeringSupersessionProposal:
        """Gather in-flight project tasks and refine the obsolete set.

        Returns:
            The proposer's :class:`SteeringSupersessionProposal`.
        """
        candidates = await self._in_flight_tasks(project_id)
        return await self._proposer.propose(
            directive_id=directive_id,
            directive_text=directive_text,
            candidate_tasks=candidates,
            seed_task_ids=seed_task_ids,
        )

    async def _in_flight_tasks(
        self,
        project_id: NotBlankStr,
    ) -> tuple[Task, ...]:
        """Return the project's IN_PROGRESS and ASSIGNED tasks (capped).

        Returns:
            The candidate in-flight tasks for a supersession proposal.
        """
        tasks: list[Task] = []
        for status in (TaskStatus.IN_PROGRESS, TaskStatus.ASSIGNED):
            items, _total = await self._task_engine.list_tasks(
                status=status,
                project=project_id,
                limit=_PROPOSE_CANDIDATE_LIMIT,
            )
            tasks.extend(items)
        # The per-status queries each cap at _PROPOSE_CANDIDATE_LIMIT, so the
        # union can be twice that. Cap the combined set the proposer LLM sees
        # so a busy project cannot blow past the prompt budget.
        return tuple(tasks[:_PROPOSE_CANDIDATE_LIMIT])

    async def _notify(self, event: str, payload: Mapping[str, object]) -> None:
        """Publish a steering WS event; best-effort, never raises.

        Raises:
            MemoryError: Re-raised unconditionally.
            RecursionError: Re-raised unconditionally.
        """
        if self._notifier is None:
            return
        try:
            await self._notifier(event, payload)
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                event,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                note="steering_notify_failed",
            )
