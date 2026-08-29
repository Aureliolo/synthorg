# module-kind: orchestrator
"""Plan-approval resume flow for the approvals controller.

Owns the plan's own approval: on approval, the durable plan the approval
references is rebuilt into a subtask tree and filed (so an operator's edits are
exactly what builds), and the plan is moved into SKELETON, where the rollup
takes over; on rejection the parent task is cancelled and the plan is marked
REJECTED. Kept separate from the other resume flows so each stays within its
module-size tier.

Routing is deterministic off the persisted :attr:`ApprovalItem.source`, as the
sibling flows are, AND off the action type, which they do not need: the
``PLAN_REVIEW`` source is shared by the plan approval and by every question
parked alongside it, so the source alone identifies the group rather than the
gate.
"""

import asyncio
from collections.abc import Sequence
from typing import Final

from synthorg.api.controllers._conversational_resume import _reread_approval_item
from synthorg.api.controllers._plan_decision_record import record_plan_decisions
from synthorg.api.controllers._plan_resume_writes import (
    fail_dispatch,
    mark_task,
    record_dispatch_failure,
    sync_plan_status,
)
from synthorg.api.lifecycle_helpers.plan_decisions import record_resolved_decisions
from synthorg.api.lifecycle_helpers.plan_questions import (
    PLAN_ID_METADATA_KEY,
    apply_plan_question_answer,
    replay_decided_questions,
    retire_open_questions,
)
from synthorg.api.state import AppState
from synthorg.approval.plan_review import is_plan_approval
from synthorg.approval.questions import is_question
from synthorg.approval.state import ApprovalStateSlice
from synthorg.core.approval import ApprovalItem
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.plan import Plan
from synthorg.core.plan_enums import PlanStatus
from synthorg.core.task import Task
from synthorg.core.task_enums import TaskStatus
from synthorg.core.types import NotBlankStr
from synthorg.engine.decomposition.models import DecompositionResult
from synthorg.engine.decomposition.plan_mapping import decomposition_from_plan
from synthorg.engine.initiative.project_writes import link_project_to_plan
from synthorg.engine.state import EngineStateSlice, task_engine_of
from synthorg.observability import get_logger
from synthorg.observability.background_tasks import log_task_exceptions
from synthorg.observability.events.approval_gate import (
    APPROVAL_GATE_PLAN_CHILDREN_FILED,
    APPROVAL_GATE_PLAN_DISPATCH_FAILED,
    APPROVAL_GATE_RESUME_TRIGGERED,
)
from synthorg.persistence.lifecycle_ledger import ledger_for
from synthorg.persistence.state import persistence_of

logger = get_logger(__name__)

# The operator decides the approval; everything downstream of it is the
# dispatcher's. Recording a dispatch failure against the approver puts the one
# name an operator searches the ledger for on a transition they did not make,
# and leaves the decision they did make attributed to nobody.
_DISPATCH_ACTOR: Final[str] = "plan-dispatch"


async def _settle_plan_question(
    app_state: AppState,
    item: ApprovalItem,
    *,
    approved: bool,
    decision_reason: str | None,
) -> None:
    """Land a decided plan question on the plan the agents execute.

    The whole action a question's resume has: the dispatch tree is rebuilt from
    the durable plan, so an answer that stops at the approval row is an answer
    nobody hears. Nothing is dispatched and no status moves; the plan stays
    where it was, awaiting its own approval.

    A declined question carries ``None`` rather than the server-owned decline
    text, because the plan records what was DECIDED and "no answer is coming"
    is the decision.

    Raises:
        Exception: Propagated, so a failed settle rolls the decision back and
            answers the operator rather than reporting a landing that did not
            happen. That rollback is the caller's (``_save_decision_and_notify``).
    """
    await apply_plan_question_answer(
        persistence_of(app_state).plans,
        item,
        answer=decision_reason if approved else None,
        clock=app_state.clock,
    )


async def try_plan_review_resume(
    app_state: AppState,
    approval_id: str,
    *,
    approved: bool,
    decided_by: str,
    decision_reason: str | None = None,
) -> bool:
    """Resolve a decided plan approval, or a question parked off that plan.

    Deterministic routing off ``ApprovalItem.source`` AND ``action_type``. Both
    of the things a plan review parks are owned here, and they do opposite
    things: the plan's own approval stages or cancels the build, while a
    question settles onto the durable plan and builds nothing. Everything else
    returns ``False`` so the caller falls through to the parked-context /
    review-gate flows. Once owned, the decision is fully resolved on this path
    and ``True`` is returned even on failure so the approval is never
    double-handled.

    Settling the question HERE rather than at the door it was answered through
    is what makes every door agree: a plan question decided on the approvals
    endpoint reaches the plan the agents execute, exactly as one answered in
    the chat does.

    The decision is reflected onto the durable plan first (APPROVED / REJECTED)
    so the ``/plans`` view matches the recorded decision regardless of what
    happens next. On approval the durable plan (referenced by ``plan_id``) is
    then loaded, rebuilt into a ``DecompositionResult``, filed, and moved into
    SKELETON; a failure making that graph durable marks the parent task
    ``FAILED`` and drives the plan to ``FAILED`` with it, because a plan left
    at APPROVED or SKELETON behind a failed parent is a state nothing watches
    and nothing can move. On rejection the parent task is cancelled and nothing
    builds.

    One failure deliberately does not fail the plan: the detached recompute
    that opens the contract stage. By then the graph is durable and the plan is
    at SKELETON, which the recovery sweep re-drives, so the work is late rather
    than lost.

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
    # The source says where the approval came from, not what it asks: the plan
    # approval and every question parked off the plan share it, and the two
    # want opposite things. Owning the source alone made answering a
    # clarification approve the plan and file its children, with one question
    # still open and the gate's own approval still PENDING.
    #
    # Both are owned here, and separately. A question in particular must not
    # fall through to the flows below: they read it as a task-completion review
    # and refuse it, which rolls the operator's answer back with a 409.
    if is_question(item.action_type):
        await _settle_plan_question(
            app_state, item, approved=approved, decision_reason=decision_reason
        )
        return True
    if not is_plan_approval(item.action_type):
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
) -> tuple[Task, Plan] | None:
    """Resolve the two things making the graph durable cannot proceed without.

    Each absence is the same outcome reported differently, so they are
    settled together and before anything is written: the approval already
    stands, so a precondition that fails has to fail the task and the plan
    rather than return quietly, and doing that per-check inside the body
    buried the one path that actually builds.

    The coordinator is deliberately not among them. Nothing on this path runs
    a wave, so requiring one here would fail an initiative over a subsystem it
    never reaches, and the driver that does reach it resolves its own.

    Args:
        app_state: Application state.
        approval_id: The decided approval, for the failure record.
        task_id: The parent task the plan decomposes, if the approval named
            one.
        plan_id: The durable plan, if the approval named one.

    Returns:
        The ``(task, plan)`` pair, or ``None`` when one was missing and the
        failure has already been recorded.
    """
    task = (
        await task_engine_of(app_state).get_task(task_id)
        if task_id is not None
        else None
    )
    plan = await persistence_of(app_state).plans.get(plan_id) if plan_id else None
    if task_id is None:
        why = "no parent task named by the approval"
    elif task is None:
        why = "parent task no longer exists"
    elif plan is None:
        why = "durable plan not found"
    else:
        return task, plan
    await fail_dispatch(
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
    """Connect the graph for an approved plan, then open the contract stage.

    Everything the decision implies for the durable graph (answers replayed,
    decisions recorded, project linked, plan SKELETON, child tasks filed)
    happens here and is finished before the approve response is written.

    The units are not dispatched here. Approval opens SKELETON and hands the
    plan to the rollup, which is the single owner of "which stage is this plan
    in and what does that stage need now": the contract job runs, and only
    when it passes does the rollup move the plan to EXECUTING and drive the
    waves. Dispatching here as well would be a second owner for that decision,
    and it would dispatch units against a contract that does not exist yet,
    which is exactly what the stage prevents.

    The recompute is handed to a tracked background task. Awaiting it
    inside the request holds the approve call open for the length of a
    contract job: the client gives up while the server carries on, and the
    operator is told their decision failed when it was recorded and the work
    is running.
    """
    resolved = await _resolve_dispatch_inputs(
        app_state, approval_id=approval_id, task_id=task_id, plan_id=plan_id
    )
    if resolved is None:
        return
    task, plan = resolved
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
    background = asyncio.create_task(_open_contract_stage(app_state, plan))
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


async def _open_contract_stage(app_state: AppState, plan: Plan) -> None:
    """Ask the rollup to drive the plan now sitting in SKELETON.

    One recompute, not a dispatch: the rollup reads the stage the plan is in
    and fires it. A rollup that is not wired leaves the plan at SKELETON, which
    the recovery sweep re-asks on its cadence, so the initiative is late rather
    than lost.
    """
    rollup = app_state.slice(EngineStateSlice).project_rollup_service
    if rollup is None:
        logger.warning(
            APPROVAL_GATE_PLAN_DISPATCH_FAILED,
            plan_id=str(plan.id),
            reason="rollup_unwired",
            note="plan left at skeleton; the recovery sweep will re-ask",
        )
        return
    await rollup.recompute(plan.id)


async def _record_decisions(
    app_state: AppState, plan: Plan, *, decided_by: str
) -> Plan:
    """Land every decision the approval implies onto the durable plan.

    Ordered, and each ordering is load-bearing. Open questions are retired
    after the replay above, so a decision already taken lands before its row is
    closed; past this line the plan's context is stamped onto every child
    task's brief, and an answer arriving later reaches no task, no agent and no
    prompt while the operator is told it was sent.

    Returns:
        The plan carrying its resolved decisions.
    """
    await retire_open_questions(app_state.slice(ApprovalStateSlice).store, plan)
    # Write each decision item's resolved option onto the plan BEFORE anything
    # reads it. ``decomposition_from_plan`` strips decision ids from the work
    # items' dependencies because "the decision is already made by approval
    # time", while ``item_is_done`` asks whether ``chosen_option_id`` is set:
    # without this write the two disagree, and an initiative whose decision the
    # operator never clicked can dispatch every item and still never complete.
    plan = await record_resolved_decisions(
        persistence_of(app_state).plans, plan, clock=app_state.clock
    )
    # Record the plan's decision-items into the brain, so the company's shaping
    # choices survive the strip-decisions step in ``decomposition_from_plan``
    # rather than vanishing when only work items build. Downstream of the write
    # above, so the brain and the plan can never name different options.
    await record_plan_decisions(app_state, plan, decided_by=decided_by)
    return plan


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
    ``_link_initiative`` moves the plan to SKELETON before the task tree is
    built (so a rollup mid-preparation never sees a PLANNING project with a
    plan already staged), and a preparation that then fails would otherwise
    leave the plan SKELETON forever with a failed parent and no children,
    which nothing watches and nothing can move.

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
        plan = await _record_decisions(app_state, plan, decided_by=decided_by)
        # Connect the graph before any task starts: the project points at the
        # plan it is executing and goes ACTIVE, and the plan enters SKELETON.
        # Ordering is load-bearing -- the tree is filed next, so a rollup event
        # fired mid-preparation would otherwise observe a project still
        # PLANNING with a staged plan under it.
        if not await _link_initiative(app_state, plan):
            await fail_dispatch(
                app_state,
                approval_id,
                task_id=task_id,
                plan_id=plan_id,
                why="project could not be linked to its plan and staged",
            )
            return None
        # Rebuild from the durable plan so an operator's edits are exactly what
        # builds; the child task tree is derived deterministically from its
        # items (see ``decomposition_from_plan``).
        decomposition = decomposition_from_plan(plan, parent_task=task)
        # Filed here, and the reason is the failure this whole path exists to
        # remove: ``coordinate`` takes the rebuilt tasks by value and never
        # writes them, so an approved plan reached EXECUTING with the children
        # existing only inside the call. Everything that asks afterwards -- the
        # parent rollup reading each subtask's status, the initiative rollup
        # querying a plan's tasks, the dashboard -- goes to the repository, so
        # an unwritten child is one that never happened. The contract stage is
        # opened only once they exist, since the driver it eventually hands to
        # reads the plan's work out of the repository.
        await _file_child_tasks(app_state, decomposition.all_tasks)
    except MemoryError, RecursionError:
        raise
    except Exception as exc:  # noqa: BLE001 -- dispatch failure: surface, don't 5xx
        reraise_critical(exc)
        await record_dispatch_failure(
            app_state, exc, approval_id=approval_id, task_id=task_id, plan_id=plan_id
        )
        return None
    return decomposition


async def _link_initiative(app_state: AppState, plan: Plan) -> bool:
    """Connect the project to the plan it is about to execute.

    Points the project at *plan*, activates it, and moves the plan into
    SKELETON. Both writes use the same audited paths the rollup uses, so the
    graph has one set of status semantics whether dispatch or rollup is
    writing.

    SKELETON rather than EXECUTING: the units are filed but nothing dispatches
    them until the contract they build against exists as code. The rollup owns
    that hand-off, so approval's job ends at making the graph durable.

    Returns:
        Whether the project was linked AND the plan reached SKELETON. A failed
        link must abort the dispatch: proceeding would run the whole task tree
        against a project that never learned which plan it is executing, so its
        progress view would report no plan for the life of the initiative and
        its status would advance from PLANNING only by an illegal jump.

        The staging write is reported for a sharper reason. It can be lost (a
        repeated CAS conflict, a deleted plan), and the loss is silent by
        design at that seam, so treating it as done files the entire task tree
        behind a plan still reading APPROVED: the contract stage never opens,
        and nothing downstream can tell that from a plan that simply has not
        got there yet.
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
    return await sync_plan_status(
        app_state, str(plan.id), PlanStatus.SKELETON, requested_by=_DISPATCH_ACTOR
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
