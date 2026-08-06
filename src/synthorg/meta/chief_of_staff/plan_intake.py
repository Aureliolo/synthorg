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
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final, Protocol

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.domain_errors import ServiceUnavailableError
from synthorg.core.persistence_errors import DuplicateRecordError
from synthorg.core.project import Project
from synthorg.core.project_enums import ProjectStatus
from synthorg.core.task import Task
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
from synthorg.settings.resolver import ConfigResolver

logger = get_logger(__name__)

_ORIGIN_ADAPTER_ID: NotBlankStr = NotBlankStr("conversational-cos")
# Fixed namespace for deriving a deterministic, retry-stable project id from the
# normalised objective via uuid5. The derivation is what makes an identical
# brief converge on one project without a lock or a lookup: two processes racing
# the same text derive the same id, so one create wins and the other is told so.
# Follows the same derivation pattern as the charter dispatcher (a distinct
# namespace uuid). Keyed on the objective rather than the conversation because
# every turn opens a new conversation, which made every re-send a new project.
_PROJECT_NAMESPACE: uuid.UUID = uuid.UUID("6f1d4c2e-0000-4000-8000-000000000003")

# Used when the window's own setting cannot be read, which is the boot window
# before the resolver is wired. Matches the registered default rather than
# standing in for it.
_DEFAULT_DEDUPE_WINDOW_SECONDS: Final[float] = 300.0


def _normalise_objective(text: str) -> str:
    """Reduce a brief to the key two re-sends of it share.

    Casefold plus whitespace collapse, which is what differs between an
    impatient operator's second send and their first. Nothing semantic: a
    reworded brief is a different request and must get its own project.

    Args:
        text: The operator's raw brief.

    Returns:
        The comparison key.
    """
    return " ".join(text.split()).casefold()


@dataclass(frozen=True, slots=True)
class _Dispatched:
    """One objective this process has already filed, and where.

    Attributes:
        project: The project it was filed under.
        at: When it was filed, against which the dedupe window is measured.
    """

    project: NotBlankStr
    at: datetime


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
        work_pipeline: The work pipeline spine entry.
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
        "_recent",
        "_work_pipeline",
    )

    def __init__(
        self,
        *,
        project_repo: ProjectRepository,
        work_pipeline: WorkPipeline,
        clock: Clock | None = None,
        dispatch_port: ConversationalWorkDispatchPort | None = None,
        config_resolver: ConfigResolver | None = None,
    ) -> None:
        self._project_repo = project_repo
        self._work_pipeline = work_pipeline
        self._clock: Clock = clock or SystemClock()
        self._dispatch_port = dispatch_port
        self._config_resolver = config_resolver
        # What this process has filed recently, pruned to the window on every
        # intake so it stays bounded by the burst rather than by uptime. A
        # cache, never an authority: losing it (a restart, a second worker)
        # falls back to the derived id below, which is durable.
        self._recent: dict[str, _Dispatched] = {}

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
            project the plan is being drafted for.

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
        work_item = self._build_work_item(conversation, args, work, project_id, now)
        task = await self._work_pipeline.intake_only(work_item)
        # The port spawns a tracked spine that fails the task on its own error;
        # it must not raise synchronously for a runtime-execution failure.
        port.dispatch_conversational_execution(
            work_pipeline=self._work_pipeline,
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
        return await self._provision(conversation, work, now)

    async def _provision(
        self,
        conversation: Conversation,
        work: ProposedWork,
        now: datetime,
    ) -> _ProjectChoice:
        """Provision the project for an unnamed brief, deduping a re-send.

        An operator who gets no feedback for fifteen seconds sends the brief
        again, and each send used to fork its own project, its own plan and its
        own decomposition run over the same objective. A re-send inside the
        dedupe window that finds its earlier request still unacted-on joins it.

        Returns:
            The resolved project and whether this intake joined a request that
            was already in flight.

        Raises:
            ServiceUnavailableError: When even a fresh random id is taken,
                which is a store that is not accepting projects rather than a
                collision, and is not something to retry into.
        """
        objective = _normalise_objective(work.raw_intent)
        window = await self._dedupe_window()
        remembered = self._recent.get(objective)
        self._forget_stale(now, window)
        # Read before the prune, because "this objective was filed, and the
        # window has since closed" is a verdict, and an entry that is merely
        # dropped is indistinguishable from one that never existed.
        aged_out = remembered is not None and now - remembered.at > window
        if remembered is not None and not aged_out:
            joined = await self._still_in_flight(remembered.project)
            if joined is not None:
                return self._joined(conversation, objective, joined, now)
        derived = uuid.uuid5(_PROJECT_NAMESPACE, f"objective-{objective}")
        created = await self._create(work, derived)
        if created is not None:
            return self._filed(conversation, objective, created, now)
        if not aged_out:
            # The derived id is taken and this process has no record of taking
            # it: another worker did, or this one restarted. Its status is then
            # the evidence the window would have given, since a project still
            # PLANNING has not been acted on. Skipped when the window HAS
            # spoken, or a closed window would be overridden by the very
            # project it closed on.
            standing = await self._still_in_flight(NotBlankStr(str(derived)))
            if standing is not None:
                return self._joined(conversation, objective, standing, now)
        # A genuine second run of the same words, so it gets its own project.
        fresh = await self._create(work, uuid.uuid4())
        if fresh is None:  # pragma: no cover -- a uuid4 collision
            msg = "Could not provision a project for the objective."
            raise ServiceUnavailableError(msg)
        return self._filed(conversation, objective, fresh, now)

    def _filed(
        self,
        conversation: Conversation,
        objective: str,
        project: NotBlankStr,
        now: datetime,
    ) -> _ProjectChoice:
        """Record and report an intake that opened its own project.

        Args:
            conversation: The conversation the brief arrived on.
            objective: The normalised objective key.
            project: The project just provisioned.
            now: The intake time, from which the window runs.

        Returns:
            The choice naming the new project.
        """
        self._recent[objective] = _Dispatched(project=project, at=now)
        logger.debug(
            COS_PROPOSE_PROPOSED,
            conversation_id=str(conversation.id),
            project=project,
            note="project provisioned for conversational objective",
        )
        return _ProjectChoice(project=project)

    async def _create(
        self, work: ProposedWork, project_id: uuid.UUID
    ) -> NotBlankStr | None:
        """Create the project under *project_id*.

        Args:
            work: The brief the project is being provisioned for.
            project_id: The id to claim.

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
                )
            )
        except DuplicateRecordError:
            return None
        return NotBlankStr(str(project_id))

    async def _still_in_flight(self, project: NotBlankStr | None) -> NotBlankStr | None:
        """Report whether a candidate project is still an unacted-on request.

        Args:
            project: The candidate project id, or ``None`` for none.

        Returns:
            The project id when it exists and is still PLANNING, else ``None``.
            Anything past PLANNING has been approved and dispatched, so folding
            a new brief into it would file work against a decision that was
            made about different words.
        """
        if project is None:
            return None
        existing = await self._project_repo.get(project)
        if existing is None or existing.status is not ProjectStatus.PLANNING:
            return None
        return project

    def _joined(
        self,
        conversation: Conversation,
        objective: str,
        project: NotBlankStr,
        now: datetime,
    ) -> _ProjectChoice:
        """Record and report an intake that joined a request already in flight.

        Args:
            conversation: The conversation the brief arrived on.
            objective: The normalised objective key.
            project: The project being joined.
            now: The intake time, which restarts the window.

        Returns:
            The choice naming the joined project.
        """
        self._recent[objective] = _Dispatched(project=project, at=now)
        logger.info(
            COS_PROPOSE_PROPOSED,
            conversation_id=str(conversation.id),
            project=project,
            note="objective already in flight; joined its project",
        )
        return _ProjectChoice(project=project, reused=True)

    def _forget_stale(self, now: datetime, window: timedelta) -> None:
        """Drop remembered dispatches the window no longer covers.

        Pruned per intake rather than on a timer, so the memory is bounded by
        one window's traffic instead of by uptime.

        Args:
            now: The current time.
            window: How long a dispatch stays a dedupe candidate.
        """
        self._recent = {
            objective: dispatched
            for objective, dispatched in self._recent.items()
            if now - dispatched.at <= window
        }

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
