# module-kind: orchestrator
"""Startup wiring + adapter for the human plan-approval gate.

Attaches a plan-review gate to the work pipeline when
``coordination.plan_approval_required`` is set: splittable team work is then
parked for human approval (the decomposed plan) before any team builds, and
the approved plan is dispatched verbatim on approval. Default off, so
behaviour is unchanged unless an operator opts in.
"""

import uuid
from typing import Final

from synthorg.api.state import AppState
from synthorg.approval.enums import ApprovalRiskLevel, ApprovalSource, ApprovalStatus
from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.approval.state import approval_store_of
from synthorg.core.approval import ApprovalItem
from synthorg.core.clock import Clock
from synthorg.core.task import Task
from synthorg.core.types import NotBlankStr
from synthorg.engine.decomposition.models import DecompositionResult
from synthorg.engine.pipeline.models import PlanReviewHandoff, WorkItem
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_APP_STARTUP

logger = get_logger(__name__)

_PLAN_ACTION_TYPE = "plan:approve"

#: ``ApprovalItem.metadata`` keys carrying the parked plan + resume context.
PLAN_METADATA_KEY = "plan"
PROJECT_METADATA_KEY = "project"

_PREVIEW_SUBTASKS: Final[int] = 3


def _plan_detail(plan: DecompositionResult) -> str:
    """Human-readable one-line summary of a decomposed plan.

    Returns:
        A ``"<n> subtask(s): title, title, ..."`` summary.
    """
    subtasks = plan.plan.subtasks
    titles = ", ".join(s.title for s in subtasks[:_PREVIEW_SUBTASKS])
    suffix = ", ..." if len(subtasks) > _PREVIEW_SUBTASKS else ""
    head = f"{len(subtasks)} subtask(s)"
    return f"{head}: {titles}{suffix}" if titles else f"{head} awaiting approval"


class PlanReviewApprovalGate:
    """Parks a decomposed plan as an approval item before a team builds.

    Structurally satisfies the engine's ``PlanReviewGate`` port; wired onto
    the work pipeline by the startup hook so the engine never imports the
    approval store. The full :class:`DecompositionResult` is serialised into
    the approval's metadata so the approved plan is dispatched verbatim on
    approval (no re-decomposition).
    """

    __slots__ = ("_approval_store", "_clock")

    def __init__(
        self,
        *,
        approval_store: ApprovalStoreProtocol,
        clock: Clock,
    ) -> None:
        self._approval_store = approval_store
        self._clock = clock

    async def request_plan_approval(
        self,
        *,
        work_item: WorkItem,
        task: Task,
        plan: DecompositionResult,
    ) -> PlanReviewHandoff:
        """Park *plan* as a plan-approval item and return the handoff.

        Returns:
            A :class:`PlanReviewHandoff` naming the parked approval item.
        """
        approval_id = uuid.uuid4()
        detail = _plan_detail(plan)
        await self._approval_store.add(
            ApprovalItem(
                id=approval_id,
                action_type=NotBlankStr(_PLAN_ACTION_TYPE),
                title=NotBlankStr(f"Approve plan for: {task.title}"),
                description=NotBlankStr(detail),
                requested_by=work_item.requested_by,
                risk_level=ApprovalRiskLevel.MEDIUM,
                source=ApprovalSource.PLAN_REVIEW,
                status=ApprovalStatus.PENDING,
                created_at=self._clock.now(),
                task_id=NotBlankStr(str(task.id)),
                metadata={
                    PLAN_METADATA_KEY: plan.model_dump_json(),
                    PROJECT_METADATA_KEY: work_item.project,
                },
            )
        )
        return PlanReviewHandoff(
            approval_id=NotBlankStr(str(approval_id)),
            subtask_count=len(plan.plan.subtasks),
            detail=NotBlankStr(detail),
        )


async def wire_plan_review_gate(app_state: AppState) -> None:
    """Attach the plan-approval gate when the setting requires it.

    Best-effort + opt-in: a no-op unless ``coordination.plan_approval_required``
    is set and the work pipeline is wired. Default off keeps the historic
    dispatch-straight-to-team behaviour, so wiring this never changes an
    org that has not opted in.
    """
    from synthorg.engine.state import (  # noqa: PLC0415
        EngineStateSlice,
        work_pipeline_of,
    )
    from synthorg.settings.state import config_resolver_of  # noqa: PLC0415

    if app_state.slice(EngineStateSlice).work_pipeline is None:
        return
    required = await config_resolver_of(app_state).get_bool(
        "coordination", "plan_approval_required"
    )
    if not required:
        return
    gate = PlanReviewApprovalGate(
        approval_store=approval_store_of(app_state),
        clock=app_state.clock,
    )
    work_pipeline_of(app_state).attach_plan_review_gate(gate)
    logger.info(API_APP_STARTUP, service="plan_review_gate", note="wired")
