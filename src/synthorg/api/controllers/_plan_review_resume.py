# module-kind: orchestrator
"""Plan-approval resume flow for the approvals controller.

Owns the ``PLAN_REVIEW`` approval source: on approval, the durable plan the
approval references is rebuilt into a dispatchable subtask tree and handed to
the coordinator (so an operator's edits are exactly what builds), and the
plan's status is synced to APPROVED; on rejection the parent task is cancelled
and the plan is marked REJECTED. Kept separate from the other resume flows so
each stays within its module-size tier. Routing is deterministic off the
persisted :attr:`ApprovalItem.source` discriminator, matching the sibling
resume flows.
"""

import asyncio
from collections.abc import Sequence
from typing import Final
from uuid import UUID

from synthorg.api.controllers._conversational_resume import _reread_approval_item
from synthorg.api.controllers._plan_decision_record import record_plan_decisions
from synthorg.api.controllers._plan_resume_writes import mark_task, sync_plan_status
from synthorg.api.lifecycle_helpers.plan_questions import (
    PLAN_ID_METADATA_KEY,
    replay_decided_questions,
)
from synthorg.api.state import AppState
from synthorg.approval.state import ApprovalStateSlice
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.plan import Plan
from synthorg.core.plan_enums import PlanStatus
from synthorg.core.task import Task
from synthorg.core.task_enums import TaskStatus
from synthorg.core.types import NotBlankStr
from synthorg.engine.coordination.models import (
    CoordinationContext,
    CoordinationResult,
)
from synthorg.engine.coordination.service import MultiAgentCoordinator
from synthorg.engine.decomposition.models import DecompositionResult
from synthorg.engine.decomposition.plan_mapping import decomposition_from_plan
from synthorg.engine.initiative.project_writes import link_project_to_plan
from synthorg.engine.state import EngineStateSlice, task_engine_of
from synthorg.hr.state import agent_registry_of
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.background_tasks import log_task_exceptions
from synthorg.observability.events.approval_gate import (
    APPROVAL_GATE_PLAN_CHILDREN_FILED,
    APPROVAL_GATE_PLAN_DISPATCH_FAILED,
    APPROVAL_GATE_RESUME_TRIGGERED,
)
from synthorg.persistence.lifecycle_ledger import ledger_for
from synthorg.persistence.state import persistence_of
from synthorg.workers.state import RuntimeStateSlice

logger = get_logger(__name__)

# The operator decides the approval; everything downstream of it is the
# dispatcher's. Recording a dispatch failure against the approver puts the one
# name an operator searches the ledger for on a transition they did not make,
# and leaves the decision they did make attributed to nobody.
_DISPATCH_ACTOR: Final[str] = "plan-dispatch"


async def try_plan_review_resume(
    app_state: AppState,
    approval_id: str,
    *,
    approved: bool,
    decided_by: str,
) -> bool:
    """Dispatch (or cancel) a decided plan-approval, if this is one.

    Deterministic routing off ``ApprovalItem.source``: only ``PLAN_REVIEW``
    approvals are owned here; everything else returns ``False`` so the caller
    falls through to the parked-context / review-gate flows. Once owned, the
    decision is fully resolved on this path and ``True`` is returned even on
    failure so the approval is never double-handled.

    The decision is reflected onto the durable plan first (APPROVED / REJECTED)
    so the ``/plans`` view matches the recorded decision regardless of what
    happens next. On approval the durable plan (referenced by ``plan_id``) is
    then loaded and rebuilt into a ``DecompositionResult`` dispatched via
    ``coordinate(precomputed_plan=...)``; a dispatch failure marks the parent
    task ``FAILED`` (the plan stays APPROVED, since the decision stands). On
    rejection the parent task is cancelled and nothing builds.

    Returns:
        ``True`` when this flow owns the decision, ``False`` otherwise.

    Raises:
        MemoryError: Re-raised uncaught so a genuine OOM is never masked.
        RecursionError: Re-raised uncaught alongside ``MemoryError``.
    """
    from synthorg.approval.enums import ApprovalSource  # noqa: PLC0415

    item = await _reread_approval_item(app_state, approval_id)
    if item is None or item.source is not ApprovalSource.PLAN_REVIEW:
        return False
    logger.info(
        APPROVAL_GATE_RESUME_TRIGGERED,
        approval_id=approval_id,
        approved=approved,
        note="plan review decision",
    )
    task_id = item.task_id
    plan_id = item.metadata.get(PLAN_ID_METADATA_KEY)
    if not approved:
        await sync_plan_status(
            app_state, plan_id, PlanStatus.REJECTED, requested_by=decided_by
        )
        await _cancel_task(app_state, task_id, decided_by)
        return True
    await sync_plan_status(
        app_state, plan_id, PlanStatus.APPROVED, requested_by=decided_by
    )
    await _dispatch_approved_plan(
        app_state,
        approval_id=approval_id,
        task_id=task_id,
        plan_id=plan_id,
        decided_by=decided_by,
    )
    return True


async def _resolve_dispatch_inputs(
    app_state: AppState,
    *,
    approval_id: str,
    task_id: str | None,
    plan_id: str | None,
) -> tuple[MultiAgentCoordinator, Task, Plan] | None:
    """Resolve the three things a dispatch cannot proceed without.

    Each absence is the same outcome reported differently, so they are
    settled together and before anything is written: the approval already
    stands, so a precondition that fails has to fail the task and the plan
    rather than return quietly, and doing that per-check inside the dispatch
    body buried the one path that actually builds.

    Args:
        app_state: Application state.
        approval_id: The decided approval, for the failure record.
        task_id: The parent task the plan decomposes, if the approval named
            one.
        plan_id: The durable plan, if the approval named one.

    Returns:
        The ``(coordinator, task, plan)`` triple, or ``None`` when one was
        missing and the failure has already been recorded.
    """
    coordinator = app_state.slice(RuntimeStateSlice).coordinator
    task = (
        await task_engine_of(app_state).get_task(task_id)
        if coordinator is not None and task_id is not None
        else None
    )
    plan = await persistence_of(app_state).plans.get(plan_id) if plan_id else None
    if coordinator is None or task_id is None:
        why = "coordinator/task missing"
    elif task is None:
        why = "parent task no longer exists"
    elif plan is None:
        why = "durable plan not found"
    else:
        return coordinator, task, plan
    await _fail_dispatch(
        app_state, approval_id, task_id=task_id, plan_id=plan_id, why=why
    )
    return None


async def _file_child_tasks(app_state: AppState, children: Sequence[Task]) -> None:
    """Persist the rebuilt child tasks so the plan's work is queryable.

    Saved rather than created through the engine: the ids are already
    derived from the plan items (``subtask_uuid``), which is what makes a
    re-dispatch of the same plan idempotent, and asking the engine to
    create them would mint new ones and duplicate the tree on every retry.

    One transaction, because a plan's children are a tree and half a tree
    is not a smaller plan: the parent rollup would compute over subtasks
    the plan does not have, and the dispatch that failed marks the plan
    failed while some of its work sits queryable and unowned.

    Args:
        app_state: Application state carrying the persistence backend.
        children: The tasks rebuilt from the approved plan's work items.
    """
    await persistence_of(app_state).tasks.save_many(tuple(children))
    logger.info(
        APPROVAL_GATE_PLAN_CHILDREN_FILED,
        child_count=len(children),
    )


async def _dispatch_approved_plan(
    app_state: AppState,
    *,
    approval_id: str,
    task_id: str | None,
    plan_id: str | None,
    decided_by: str,
) -> None:
    """Connect the graph for an approved plan, then build it in the background.

    Split in two because the two halves take different amounts of time and the
    operator is only waiting on one of them. Everything the decision implies
    for the durable graph (answers replayed, decisions recorded, project
    linked, plan EXECUTING, child tasks filed) happens here and is finished
    before the approve response is written. The build itself is handed to a
    tracked background task.

    That is not a preference. Awaiting the whole wave inside the request left
    the approve call open for the length of a build: measured at 900 seconds
    on a three-item widget before the client gave up, while the server carried
    on, so the operator's client reported a failure for a decision that was
    recorded and work that was running.
    """
    resolved = await _resolve_dispatch_inputs(
        app_state, approval_id=approval_id, task_id=task_id, plan_id=plan_id
    )
    if resolved is None:
        return
    coordinator, task, plan = resolved
    prepared = await _prepare_dispatch(
        app_state,
        approval_id=approval_id,
        task=task,
        plan=plan,
        task_id=task_id,
        plan_id=plan_id,
        decided_by=decided_by,
    )
    if prepared is None:
        return
    background = asyncio.create_task(
        _build_approved_plan(
            app_state,
            coordinator=coordinator,
            decomposition=prepared,
            task=task,
            approval_id=approval_id,
            task_id=task_id,
            plan_id=plan_id,
        )
    )
    background.add_done_callback(
        log_task_exceptions(
            logger,
            APPROVAL_GATE_PLAN_DISPATCH_FAILED,
            approval_id=approval_id,
            plan_id=plan_id,
        ),
    )
    app_state.plan_dispatch_background_tasks.add(background)
    background.add_done_callback(app_state.plan_dispatch_background_tasks.discard)


async def _prepare_dispatch(
    app_state: AppState,
    *,
    approval_id: str,
    task: Task,
    plan: Plan,
    task_id: str | None,
    plan_id: str | None,
    decided_by: str,
) -> DecompositionResult | None:
    """Make the approved plan's graph durable, before anything runs.

    The approval is already recorded APPROVED and the plan already synced, so
    any failure here marks the parent task ``FAILED`` and drives the plan out
    of its dispatch status rather than silently returning. Both writes matter:
    ``_link_initiative`` moves the plan to EXECUTING before the task tree is
    built (so a rollup mid-dispatch never sees a PLANNING project with tasks
    running), and a dispatch that then fails would otherwise leave the plan
    EXECUTING forever with a failed parent and no children, which nothing
    watches and nothing can move.

    Returns:
        The decomposition the build runs, or ``None`` when preparation failed
        (having already failed the task and the plan).

    Raises:
        MemoryError: Re-raised uncaught so a genuine OOM is never masked.
        RecursionError: Re-raised uncaught alongside ``MemoryError``.
    """
    try:
        # Answers decided against this plan are replayed from the approvals
        # that carry them before anything is built from it, so a write-back
        # that failed after its decision was durable costs a retry rather
        # than the operator's answer.
        plan = await replay_decided_questions(
            persistence_of(app_state).plans,
            app_state.slice(ApprovalStateSlice).store,
            plan,
            clock=app_state.clock,
        )
        # Record the plan's decision-items (chosen or recommended-by-default
        # option) into the brain before dispatch, so the company's shaping
        # choices survive the strip-decisions step in ``decomposition_from_plan``
        # rather than vanishing when only work items build.
        await record_plan_decisions(app_state, plan, decided_by=decided_by)
        # Connect the graph before any task starts: the project points at the
        # plan it is executing and goes ACTIVE, and the plan enters EXECUTING.
        # Ordering is load-bearing -- the build awaits the whole subtask tree,
        # so a rollup event fired mid-dispatch would otherwise observe a
        # project still PLANNING with tasks already running.
        if not await _link_initiative(app_state, plan):
            await _fail_dispatch(
                app_state,
                approval_id,
                task_id=task_id,
                plan_id=plan_id,
                why="project could not be linked to its plan",
            )
            return None
        # Dispatch from the durable plan so an operator's edits are exactly
        # what builds; the child task tree is rebuilt deterministically from
        # its items (see ``decomposition_from_plan``).
        decomposition = decomposition_from_plan(plan, parent_task=task)
        # Filed BEFORE dispatch, and the reason is the failure this whole
        # path exists to remove: ``coordinate`` takes the rebuilt tasks by
        # value and never writes them, so an approved plan reached EXECUTING
        # with the children existing only inside the call. Everything that
        # asks afterwards -- the parent rollup reading each subtask's status,
        # the initiative rollup querying a plan's tasks, the dashboard -- goes
        # to the repository, so an unwritten child is one that never
        # happened. Before rather than after so a dispatch that dies partway
        # still leaves the tree it was working on, which is what an operator
        # needs to see to know anything was attempted at all.
        await _file_child_tasks(app_state, decomposition.created_tasks)
    except MemoryError, RecursionError:
        raise
    except Exception as exc:  # noqa: BLE001 -- dispatch failure: surface, don't 5xx
        reraise_critical(exc)
        await _record_dispatch_failure(
            app_state, exc, approval_id=approval_id, task_id=task_id, plan_id=plan_id
        )
        return None
    return decomposition


async def _build_approved_plan(
    app_state: AppState,
    *,
    coordinator: MultiAgentCoordinator,
    decomposition: DecompositionResult,
    task: Task,
    approval_id: str,
    task_id: str | None,
    plan_id: str | None,
) -> None:
    """Run the approved plan's waves, off the request path.

    Every outcome is written to the graph rather than returned: the operator
    who approved this is no longer waiting on it, so the plan's status and the
    task's status are the only places the answer can appear.

    Raises:
        MemoryError: Re-raised uncaught so a genuine OOM is never masked.
        RecursionError: Re-raised uncaught alongside ``MemoryError``.
    """
    try:
        agents = await agent_registry_of(app_state).list_active()
        result = await coordinator.coordinate(
            CoordinationContext(task=task, available_agents=agents),
            precomputed_plan=decomposition,
        )
        # A coordination that fails every wave returns normally, so reading the
        # verdict is the only way to see it: the raise-only guard below walked
        # straight past a run where all five tasks died and left the plan
        # EXECUTING with nothing left to execute.
        if not result.result.is_success:
            await _hand_failure_to_rollup(
                app_state,
                approval_id,
                task_id=task_id,
                plan_id=plan_id,
                why=_coordination_failure_detail(result.result),
            )
    except MemoryError, RecursionError:
        raise
    except Exception as exc:  # noqa: BLE001 -- dispatch failure: surface, don't 5xx
        reraise_critical(exc)
        await _record_dispatch_failure(
            app_state, exc, approval_id=approval_id, task_id=task_id, plan_id=plan_id
        )


async def _hand_failure_to_rollup(
    app_state: AppState,
    approval_id: str,
    *,
    task_id: str | None,
    plan_id: str | None,
    why: str,
) -> None:
    """Report a failed wave and let the rollup decide what it means.

    A wave that loses one agent is not the same fact as an initiative that
    is dead, and only one authority can be allowed to say the second. The
    rollup already owns it: it derives the plan's status from the items'
    own states, runs the tail stage, and routes a stall to the replan
    trigger. Failing the plan from here instead pre-empted all of that, so
    a run whose rollup had just computed ``next_action=replan`` died anyway
    and discarded four siblings that were still working.

    The property this must not lose is the one that put the check here: a
    coordination that fails without raising used to leave the plan
    EXECUTING with nothing left to execute, which no later event repaired.
    A rollup pass answers that better than a status write does, because it
    reads what actually happened rather than which wave index reported it.

    Args:
        app_state: Application state holding the rollup and persistence.
        approval_id: The approval whose dispatch this was, for the log.
        task_id: The objective task, failed only when nothing can roll up.
        plan_id: The plan to recompute.
        why: Which phases failed, for the operator.
    """
    rollup = app_state.slice(EngineStateSlice).project_rollup_service
    if rollup is None or plan_id is None:
        # No rollup means nothing downstream will ever look at this plan
        # again, so the pre-emption this function exists to remove is the
        # only thing standing between the operator and a plan parked at
        # EXECUTING for good. Named in the log, because it is a fallback
        # for an unwired subsystem rather than a second routine owner.
        logger.warning(
            APPROVAL_GATE_PLAN_DISPATCH_FAILED,
            approval_id=approval_id,
            plan_id=plan_id,
            why=why,
            note="no rollup service; failing the plan here so it cannot hang",
        )
        await _fail_dispatch(
            app_state, approval_id, task_id=task_id, plan_id=plan_id, why=why
        )
        return
    logger.info(
        APPROVAL_GATE_PLAN_DISPATCH_FAILED,
        approval_id=approval_id,
        plan_id=plan_id,
        why=why,
        note="wave failure handed to the rollup, which owns the plan's verdict",
    )
    await rollup.recompute(UUID(plan_id))


async def _record_dispatch_failure(
    app_state: AppState,
    exc: Exception,
    *,
    approval_id: str,
    task_id: str | None,
    plan_id: str | None,
) -> None:
    """Fail the task and the plan for a dispatch that could not proceed.

    Shared by both halves of the dispatch, which fail the same way and must
    say so identically whether the request was still open or not.
    """
    log_exception_redacted(
        logger,
        APPROVAL_GATE_PLAN_DISPATCH_FAILED,
        exc,
        approval_id=approval_id,
        note="approved plan could not be resumed; failing task and plan",
    )
    await mark_task(
        app_state,
        task_id,
        _DISPATCH_ACTOR,
        target=TaskStatus.FAILED,
        reason="approved plan could not be resumed",
    )
    await _fail_plan(
        app_state, plan_id, f"dispatch failed: {safe_error_description(exc)}"
    )


async def _link_initiative(app_state: AppState, plan: Plan) -> bool:
    """Connect the project to the plan it is about to execute.

    Points the project at *plan*, activates it, and moves the plan into
    EXECUTING. Both writes use the same audited paths the rollup uses, so the
    graph has one set of status semantics whether dispatch or rollup is
    writing.

    Returns:
        Whether the project was linked. A failed link must abort the dispatch:
        proceeding would run the whole task tree against a project that never
        learned which plan it is executing, so its progress view would report
        no plan for the life of the initiative and its status would advance
        from PLANNING only by an illegal jump.
    """
    persistence = persistence_of(app_state)
    linked = await link_project_to_plan(
        persistence.projects,
        project_id=NotBlankStr(str(plan.project)),
        plan_id=plan.id,
        ledger=ledger_for(persistence, clock=app_state.clock),
    )
    if linked is None:
        return False
    await sync_plan_status(
        app_state, str(plan.id), PlanStatus.EXECUTING, requested_by=_DISPATCH_ACTOR
    )
    return True


def _coordination_failure_detail(result: CoordinationResult) -> str:
    """Name the phases a coordination run failed, for the plan's reason.

    The reason is persisted on the plan and shown in Plan Review, so it has to
    say which stage died rather than "dispatch failed". Each phase's ``error``
    is already the scrubbed description, so it is safe to carry through.

    Returns:
        A one-line summary of the failed phases.
    """
    failed = [phase for phase in result.phases if not phase.success]
    if not failed:
        # ``is_success`` is vacuously true over no phases, so reaching here
        # means the run produced none at all: nothing ran, which is its own
        # failure and needs saying rather than reporting an empty list.
        return "coordination produced no phase results"
    parts = [
        f"{phase.phase}: {phase.error}" if phase.error else phase.phase
        for phase in failed
    ]
    return f"coordination failed ({'; '.join(parts)})"


async def _fail_dispatch(
    app_state: AppState,
    approval_id: str,
    *,
    task_id: str | None,
    plan_id: str | None,
    why: str,
) -> None:
    """Log an approved-plan dispatch precondition failure; fail task and plan.

    The approval is already persisted APPROVED, so a swallowed failure would
    leave the parent silently stuck in its pre-approval status with no
    board-visible signal. Move it to FAILED so the stuck plan surfaces and
    stays re-runnable (FAILED -> ASSIGNED is valid), and fail the plan too so
    it does not sit in a dispatch status nothing will ever advance.
    """
    logger.error(
        APPROVAL_GATE_PLAN_DISPATCH_FAILED,
        approval_id=approval_id,
        note="approved plan cannot dispatch",
        why=why,
    )
    await mark_task(
        app_state,
        task_id,
        _DISPATCH_ACTOR,
        target=TaskStatus.FAILED,
        reason=f"approved plan could not be resumed: {why}",
    )
    await _fail_plan(app_state, plan_id, f"dispatch failed: {why}")


async def _fail_plan(app_state: AppState, plan_id: str | None, why: str) -> None:
    """Drive a plan that cannot dispatch out of its dispatch status.

    Without this the plan rests in APPROVED or EXECUTING with a failed parent
    and no child tasks: a state that can be entered, has no exit, and that
    nothing watches. FAILED is terminal, carries the reason on the plan for
    Plan Review to show, and is reachable from both dispatch statuses.
    """
    await sync_plan_status(
        app_state,
        plan_id,
        PlanStatus.FAILED,
        requested_by=_DISPATCH_ACTOR,
        failure_reason=NotBlankStr(why),
    )


async def _cancel_task(
    app_state: AppState,
    task_id: str | None,
    decided_by: str,
) -> None:
    """Cancel the parent task of a rejected plan, best-effort."""
    await mark_task(
        app_state,
        task_id,
        decided_by,
        target=TaskStatus.CANCELLED,
        reason="plan rejected at human approval gate",
    )
