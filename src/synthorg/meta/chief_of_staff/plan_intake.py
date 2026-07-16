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
from datetime import datetime
from typing import Protocol

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

logger = get_logger(__name__)

_ORIGIN_ADAPTER_ID: NotBlankStr = NotBlankStr("conversational-cos")
# Fixed namespace for deriving a deterministic, retry-stable project id from
# the (unique) conversation id via uuid5, so a re-dispatched brief on the same
# conversation reuses one project rather than minting duplicates. Follows the
# same derivation pattern as the charter dispatcher (a distinct namespace uuid).
_PROJECT_NAMESPACE: uuid.UUID = uuid.UUID("6f1d4c2e-0000-4000-8000-000000000003")


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
    """

    __slots__ = ("_clock", "_dispatch_port", "_project_repo", "_work_pipeline")

    def __init__(
        self,
        *,
        project_repo: ProjectRepository,
        work_pipeline: WorkPipeline,
        clock: Clock | None = None,
        dispatch_port: ConversationalWorkDispatchPort | None = None,
    ) -> None:
        self._project_repo = project_repo
        self._work_pipeline = work_pipeline
        self._clock: Clock = clock or SystemClock()
        self._dispatch_port = dispatch_port

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
        project_id = await self._resolve_project(conversation, args, work)
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
        )

    async def _resolve_project(
        self,
        conversation: Conversation,
        args: ProposeArgs,
        work: ProposedWork,
    ) -> NotBlankStr:
        """Reuse a named project or mint one idempotently for the request.

        A brief that names an existing project files under it; otherwise a
        project is minted with an id derived from the conversation, so a
        re-dispatched brief on the same conversation reuses that project
        rather than creating a duplicate.

        Returns:
            The resolved project id.
        """
        named = work.project or args.project
        if named is not None:
            existing = await self._project_repo.get(named)
            # A read, not a lock: if the named project is deleted between here
            # and dispatch, the pipeline's own project resolution re-checks and
            # raises a typed ProjectNotFoundError -- that check is the authority.
            if existing is not None:
                return named
        project_uuid = uuid.uuid5(_PROJECT_NAMESPACE, f"conversation-{conversation.id}")
        project_id = NotBlankStr(str(project_uuid))
        project = Project(
            id=project_uuid,
            name=NotBlankStr(work.title),
            description=work.raw_intent,
            status=ProjectStatus.PLANNING,
        )
        try:
            await self._project_repo.create(project)
        except DuplicateRecordError:
            # Idempotent re-dispatch: the project from a prior turn stands.
            logger.info(
                COS_PROPOSE_PROPOSED,
                conversation_id=str(conversation.id),
                project=project_id,
                note="project already provisioned on a prior turn",
            )
        else:
            logger.debug(
                COS_PROPOSE_PROPOSED,
                conversation_id=str(conversation.id),
                project=project_id,
                note="project provisioned for conversational objective",
            )
        return project_id

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
