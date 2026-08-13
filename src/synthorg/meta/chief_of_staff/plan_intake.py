# module-kind: service
"""Conversational request to Plan Review dispatch.

Turns one clarified conversational work brief into a single objective run:
it provisions (or reuses) the project, builds the kickoff
:class:`WorkItem` with ``plan_required=True``, runs intake synchronously
so the chat gets a task id to subscribe to, then backgrounds the
decompose+park spine. Because the work item is plan-gated, that spine
decomposes the brief into one durable :class:`~synthorg.core.plan.Plan`
and parks it for holistic human review (Plan Review) rather than
building anything or fragmenting the request into per-item approvals.

This is the conversational sibling of ``CharterDispatcher``: both drive
the same plan-review spine, differing only in their entry envelope (a
free-form chat brief here, a structured charter there). The background
dispatch rides a narrow :class:`ConversationalWorkDispatchPort` so the
``meta`` layer never imports the ``workers`` layer; the worker execution
service satisfies the port and is attached at startup. The port is
required: drafting fails closed (``ServiceUnavailableError``) when it is
absent, so no intake task is ever created without a spine to run it.
"""

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final, Protocol

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.domain_errors import ServiceUnavailableError
from synthorg.core.pagination import collect_all
from synthorg.core.persistence_errors import DuplicateRecordError
from synthorg.core.project import Project
from synthorg.core.project_enums import ProjectStatus
from synthorg.core.task import Task
from synthorg.core.task_enums import TaskStatus
from synthorg.core.types import NotBlankStr
from synthorg.engine.pipeline.models import WorkItem, WorkSource
from synthorg.engine.pipeline.protocol import WorkPipeline
from synthorg.meta.chief_of_staff.models import (
    Conversation,
    PlanDraftSummary,
    ProposeArgs,
    ProposedWork,
)
from synthorg.observability import get_logger
from synthorg.observability.events.chief_of_staff import (
    COS_PROPOSE_FAILED,
    COS_PROPOSE_PROPOSED,
)
from synthorg.persistence.project_protocol import ProjectRepository
from synthorg.persistence.task_protocol import TaskFilterSpec, TaskRepository
from synthorg.settings.resolver import ConfigResolver

logger = get_logger(__name__)

_ORIGIN_ADAPTER_ID: NotBlankStr = NotBlankStr("conversational-cos")
# Fixed namespace for deriving a deterministic, retry-stable project id from
# the requester and their normalised objective via uuid5. The derivation is
# what makes an identical brief converge on one project without a lock or a
# lookup: two processes racing the same brief derive the same id, so one
# create wins and the other is told so. Follows the same derivation pattern as
# the charter dispatcher (a distinct namespace uuid). Keyed on the objective
# rather than the conversation because every turn opens a new conversation.
_PROJECT_NAMESPACE: uuid.UUID = uuid.UUID("6f1d4c2e-0000-4000-8000-000000000003")

# Used when the window's own setting cannot be read, which is the boot window
# before the resolver is wired. Matches the registered default rather than
# standing in for it.
_DEFAULT_DEDUPE_WINDOW_SECONDS: Final[float] = 300.0

#: Separates the two halves of the dedupe key so no requester id and objective
#: can concatenate into another pair's key.
_KEY_SEPARATOR: Final[str] = "\n"

#: A window at or below this switches deduping off entirely.
_NO_WINDOW: Final[timedelta] = timedelta(0)

#: An objective in one of these can no longer produce a plan, so it does not
#: count as the project's standing run. ``FAILED`` is included even though the
#: engine treats it as reassignable, because nothing on the conversational path
#: reassigns one: re-sending the brief IS the retry, and answering it with the
#: failed id would hand the operator a run that never continues.
_DEAD_OBJECTIVE_STATUSES: Final[frozenset[TaskStatus]] = frozenset(
    {
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
        TaskStatus.REJECTED,
    }
)


def _dedupe_key(created_by: str, text: str) -> str:
    """Reduce a requester's brief to the key two re-sends of it share.

    Casefold plus whitespace collapse on the text, which is what differs
    between an impatient operator's second send and their first. Nothing
    semantic: a reworded brief is a different request and must get its own
    project.

    The requester is part of the key, and load-bearing rather than tidy.
    Joining a project means inheriting its governance envelope, including
    ``Project.autonomy_mode``, which the dispatch path reads live as the
    per-run autonomy override. An objective-only key would let anyone whose
    wording collided with a permissive in-flight project land inside it, and
    would equally let a generically worded permissive project be parked in
    advance for someone else's request to fall into.

    Args:
        created_by: The user id the request arrived under.
        text: The operator's raw brief.

    Returns:
        The comparison key.
    """
    return f"{created_by}{_KEY_SEPARATOR}{' '.join(text.split()).casefold()}"


@dataclass(frozen=True, slots=True)
class _ProjectChoice:
    """The project an intake resolved to, and whether it is a second visit.

    Attributes:
        project: The resolved project id.
        reused: Whether this intake joined a request already in flight rather
            than starting one. Surfaced in the turn reply: silently folding two
            requests into one is worse than forking them, because the operator
            is left believing they filed two.
    """

    project: NotBlankStr
    reused: bool = False


class ConversationalWorkDispatchPort(Protocol):
    """Backgrounds the post-intake spine of a plan-gated conversational run.

    Structurally satisfied by the worker execution service; declared here
    so the ``meta`` layer depends on a protocol it owns rather than
    importing the ``workers`` layer. The implementation spawns the spine as
    a tracked task that drives the task to a terminal status on failure, so
    a dispatched task never orphans.
    """

    def dispatch_conversational_execution(
        self,
        *,
        work_pipeline: WorkPipeline,
        work_item: WorkItem,
        task: Task,
    ) -> None:
        """Spawn the decompose+park spine as a tracked background task."""
        ...


class ConversationalPlanDispatcher:
    """Provision a project, draft a plan, and park it for review.

    Late-binds the work pipeline and the background-dispatch port: both
    wire only after persistence connects, so the proposer attaches this
    dispatcher once they are available. The port is required at drafting
    time; an unwired port fails the request closed rather than creating an
    intake task with no spine to run it.

    Args:
        project_repo: Project store (resolve / create).
        work_pipeline: Callable returning the spine that is current now;
            resolved per draft, because a runtime reload replaces the
            instance and nothing rebuilds this dispatcher when it does.
        task_repo: Task store, read to find the run a joined project
            already has. Without it a re-send that deduped the project
            would still file a second objective and a second decomposition
            into it, which is the fork the dedupe exists to prevent.
        clock: Injectable time source.
        dispatch_port: Background-dispatch port; ``None`` until wired, at
            which point drafting fails closed.
        config_resolver: Live settings reads for the dedupe window; ``None``
            falls back to the shipped default.
    """

    __slots__ = (
        "_clock",
        "_config_resolver",
        "_dispatch_port",
        "_project_repo",
        "_task_repo",
        "_work_pipeline",
    )

    def __init__(
        self,
        *,
        project_repo: ProjectRepository,
        work_pipeline: Callable[[], WorkPipeline],
        task_repo: TaskRepository,
        clock: Clock | None = None,
        dispatch_port: ConversationalWorkDispatchPort | None = None,
        config_resolver: ConfigResolver | None = None,
    ) -> None:
        self._project_repo = project_repo
        # Annotated: an unannotated instance attribute holding a plain
        # function reads as a bound method to a type checker.
        self._work_pipeline: Callable[[], WorkPipeline] = work_pipeline
        self._task_repo = task_repo
        self._clock: Clock = clock or SystemClock()
        self._dispatch_port = dispatch_port
        self._config_resolver = config_resolver

    async def draft_plan(
        self,
        *,
        conversation: Conversation,
        args: ProposeArgs,
        work: ProposedWork,
        now: datetime,
    ) -> PlanDraftSummary:
        """Provision the project, intake the objective, and draft its plan.

        Intake runs synchronously so the returned summary names a real task
        the chat can subscribe to; the decompose+park spine (which produces
        the durable plan and parks it for review) is backgrounded via the
        dispatch port. Fails closed before any state is created when the port
        is unwired, so a task is never left with no spine to run it.

        Returns:
            The :class:`PlanDraftSummary` naming the objective task and
            project the plan is being drafted for. On a re-send that
            deduped, the task is the one the first send filed.

        Raises:
            ServiceUnavailableError: When the dispatch port is not wired.
            WorkPipelineError: When intake rejects the objective.
        """
        port = self._dispatch_port
        if port is None:
            logger.error(
                COS_PROPOSE_FAILED,
                conversation_id=str(conversation.id),
                note="work brief accepted but dispatch port not wired",
            )
            msg = "Plan drafting is unavailable: the work dispatch port is not wired."
            raise ServiceUnavailableError(msg)
        choice = await self._resolve_project(conversation, args, work, now)
        project_id = choice.project
        if choice.reused:
            standing = await self._standing_run(project_id)
            if standing is not None:
                return PlanDraftSummary(
                    task_id=NotBlankStr(str(standing.id)),
                    project=project_id,
                    title=work.title,
                    reused_project=True,
                )
        work_item = self._build_work_item(conversation, args, work, project_id, now)
        # Resolved once and handed on, so the intake and the spine that
        # continues it are the same instance even across a reload landing
        # between the two calls.
        spine = self._work_pipeline()
        task = await spine.intake_only(work_item)
        # The port spawns a tracked spine that fails the task on its own error;
        # it must not raise synchronously for a runtime-execution failure.
        port.dispatch_conversational_execution(
            work_pipeline=spine,
            work_item=work_item,
            task=task,
        )
        logger.info(
            COS_PROPOSE_PROPOSED,
            conversation_id=str(conversation.id),
            task_id=str(task.id),
            project=project_id,
            note="plan draft dispatched for conversational objective",
        )
        return PlanDraftSummary(
            task_id=NotBlankStr(str(task.id)),
            project=project_id,
            title=work.title,
            reused_project=choice.reused,
        )

    async def _resolve_project(
        self,
        conversation: Conversation,
        args: ProposeArgs,
        work: ProposedWork,
        now: datetime,
    ) -> _ProjectChoice:
        """Reuse a named project, join a request in flight, or mint one.

        Returns:
            The resolved project and whether this intake joined a request that
            was already in flight.
        """
        named = work.project or args.project
        if named is not None:
            existing = await self._project_repo.get(named)
            # A read, not a lock: if the named project is deleted between here
            # and dispatch, the pipeline's own project resolution re-checks and
            # raises a typed ProjectNotFoundError -- that check is the authority.
            if existing is not None:
                return _ProjectChoice(project=named)
        return await self._provision(conversation, args, work, now)

    async def _provision(
        self,
        conversation: Conversation,
        args: ProposeArgs,
        work: ProposedWork,
        now: datetime,
    ) -> _ProjectChoice:
        """Provision the project for an unnamed brief, deduping a re-send.

        An operator who gets no feedback for fifteen seconds sends the brief
        again. Both sends derive the same project id from the requester and
        the normalised objective, so the second finds the first's project and,
        if it is young enough and still unacted-on, joins it instead of
        forking a second plan and a second decomposition run over the same
        words.

        The evidence is the project row itself rather than an in-process
        memory of what this worker filed. A cache would answer differently
        after a restart, on a second worker, or once unrelated traffic had
        evicted the entry, and "how long ago was this filed" would then depend
        on which process happened to take the request.

        Returns:
            The resolved project and whether this intake joined a request that
            was already in flight.

        Raises:
            ServiceUnavailableError: When even a fresh random id is taken,
                which is a store that is not accepting projects rather than a
                collision, and is not something to retry into.
        """
        derived = uuid.uuid5(
            _PROJECT_NAMESPACE, _dedupe_key(args.created_by, work.raw_intent)
        )
        window = await self._dedupe_window()
        # Zero (or negative) switches deduping off, which the setting's own
        # description promises. Checked before the lookup so "off" costs no
        # read and cannot be talked out of by a standing project.
        if window > _NO_WINDOW:
            standing = await self._joinable(derived, now, window)
            if standing is not None:
                return self._joined(conversation, standing)
        created = await self._create(work, derived, now)
        if created is not None:
            return self._filed(conversation, created)
        # The insert lost the id, and two causes look identical from here:
        # a concurrent send of the same words that won the race, or a
        # project that already existed and the check above rejected. Only
        # the row separates them, so it is re-read. Without this, a burst
        # deduplicates only as far as the first send's row being visible
        # when the second one looked, which is the one case a burst does
        # not satisfy: every send checks before any of them has written.
        if window > _NO_WINDOW:
            standing = await self._joinable(derived, now, window)
            if standing is not None:
                return self._joined(conversation, standing)
        # The derived id exists but is not joinable: it was acted on, or it
        # predates the window, or deduping is off. Either way this is a
        # genuine second run of the same words, so it gets its own project.
        fresh = await self._create(work, uuid.uuid4(), now)
        if fresh is None:  # pragma: no cover -- a uuid4 collision
            msg = "Could not provision a project for the objective."
            raise ServiceUnavailableError(msg)
        return self._filed(conversation, fresh)

    async def _standing_run(self, project: NotBlankStr) -> Task | None:
        """The objective run a joined project already has, if it has one.

        Args:
            project: The project this intake joined.

        Returns:
            The objective task filed against *project*, or ``None`` when the
            send that opened it has not reached intake yet. That gap is real
            and narrow: a project row exists from ``create``, and its task
            only after ``intake_only`` returns, so a burst can join in
            between. Answering ``None`` there files a second objective,
            which is the safe direction to be wrong in: a duplicate run is
            recoverable and a brief with no run at all is the operator
            getting nothing back.

            A deduped project holds one objective by construction, this
            being what stops a second one being filed. The id ordering is
            not a preference between two of them; it is so a repository
            whose row order is unspecified cannot answer differently on two
            calls if that invariant were ever broken.

            A run in :data:`_DEAD_OBJECTIVE_STATUSES` is not standing. The
            project stays PLANNING when its objective dies, so it stays
            inside the dedupe window, and pointing the re-send at the dead
            id would answer with a run that can no longer produce a plan
            while filing no new one. Treating it as absent files a fresh
            objective, which is what re-sending a brief after a failed
            intake is asking for.
        """
        # Drained rather than read as one page: the filter has no
        # root-task predicate, so the objective is found by inspecting
        # every task in the project, and a project past one page would
        # otherwise answer "no standing run" and file a duplicate
        # objective. Each underlying query stays bounded.
        tasks = await collect_all(
            lambda limit, offset: self._task_repo.query(
                TaskFilterSpec(project=project), limit=limit, offset=offset
            )
        )
        objectives = [
            task
            for task in tasks
            if task.parent_task_id is None
            and task.status not in _DEAD_OBJECTIVE_STATUSES
        ]
        if not objectives:
            return None
        return min(objectives, key=lambda task: str(task.id))

    def _filed(
        self,
        conversation: Conversation,
        project: NotBlankStr,
    ) -> _ProjectChoice:
        """Report an intake that opened its own project.

        Args:
            conversation: The conversation the brief arrived on.
            project: The project just provisioned.

        Returns:
            The choice naming the new project.
        """
        logger.info(
            COS_PROPOSE_PROPOSED,
            conversation_id=str(conversation.id),
            project=project,
            note="project provisioned for conversational objective",
        )
        return _ProjectChoice(project=project)

    async def _create(
        self, work: ProposedWork, project_id: uuid.UUID, now: datetime
    ) -> NotBlankStr | None:
        """Create the project under *project_id*.

        Args:
            work: The brief the project is being provisioned for.
            project_id: The id to claim.
            now: The intake time, recorded as the project's start so the
                dedupe window is measured against the injected clock rather
                than against whatever the model's own default stamped.

        Returns:
            The claimed id, or ``None`` when it was already taken.
        """
        try:
            await self._project_repo.create(
                Project(
                    id=project_id,
                    name=NotBlankStr(work.title),
                    description=work.raw_intent,
                    status=ProjectStatus.PLANNING,
                    created_at=now,
                    updated_at=now,
                )
            )
        except DuplicateRecordError:
            return None
        return NotBlankStr(str(project_id))

    async def _joinable(
        self, project_id: uuid.UUID, now: datetime, window: timedelta
    ) -> NotBlankStr | None:
        """Report whether a candidate project is a re-send's own, still open.

        Args:
            project_id: The id derived from this requester and objective.
            now: The intake time.
            window: How long a filed request stays a dedupe candidate.

        Returns:
            The project id when it exists, is still PLANNING, and was opened
            inside the window; else ``None``. Anything past PLANNING has been
            approved and dispatched, so folding a new brief into it would file
            work against a decision made about different words; anything older
            than the window is a request the operator has had time to forget.
        """
        existing = await self._project_repo.get(NotBlankStr(str(project_id)))
        if existing is None or existing.status is not ProjectStatus.PLANNING:
            return None
        if now - existing.created_at > window:
            return None
        return NotBlankStr(str(project_id))

    def _joined(
        self,
        conversation: Conversation,
        project: NotBlankStr,
    ) -> _ProjectChoice:
        """Report an intake that joined a request already in flight.

        Args:
            conversation: The conversation the brief arrived on.
            project: The project being joined.

        Returns:
            The choice naming the joined project.
        """
        logger.info(
            COS_PROPOSE_PROPOSED,
            conversation_id=str(conversation.id),
            project=project,
            note="objective already in flight; joined its project",
        )
        return _ProjectChoice(project=project, reused=True)

    async def _dedupe_window(self) -> timedelta:
        """Resolve how long a dispatched objective deduplicates a re-send.

        Read per intake rather than held, so an operator narrowing the window
        (or setting it to zero to switch deduping off) is obeyed on the next
        request rather than at the next restart.

        Returns:
            The configured window.
        """
        seconds = _DEFAULT_DEDUPE_WINDOW_SECONDS
        if self._config_resolver is not None:
            seconds = await self._config_resolver.get_float(
                "chief_of_staff", "work_request_dedupe_window_seconds"
            )
        return timedelta(seconds=seconds)

    def _build_work_item(
        self,
        conversation: Conversation,
        args: ProposeArgs,
        work: ProposedWork,
        project_id: NotBlankStr,
        now: datetime,
    ) -> WorkItem:
        """Compose the plan-gated objective work item for the request.

        ``plan_required=True`` forces the spine down the splittable path so
        the brief is decomposed into one plan and parked for review, never
        run as a single solo agent.

        Returns:
            The kickoff :class:`WorkItem`.
        """
        return WorkItem(
            origin_adapter_id=_ORIGIN_ADAPTER_ID,
            source=WorkSource.CONVERSATIONAL,
            title=work.title,
            raw_intent=work.raw_intent,
            project=project_id,
            requested_by=args.created_by,
            priority=work.priority,
            task_type=work.task_type,
            estimated_complexity=work.estimated_complexity,
            acceptance_criteria=work.acceptance_criteria,
            correlation_id=str(conversation.id),
            created_at=now,
            plan_required=True,
        )


__all__ = ["ConversationalPlanDispatcher", "ConversationalWorkDispatchPort"]
