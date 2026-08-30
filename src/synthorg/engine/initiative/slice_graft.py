# module-kind: code
"""Graft a workstream's next slice onto a live plan.

Split out from :mod:`synthorg.engine.initiative.replan_trigger` for the same
reason :mod:`synthorg.engine.initiative.rollup_stages` was split out of the
rollup: this is the part of the trigger that grows every time the graft's own
shape changes, and keeping it here is what lets the service stay within its
module-size tier.

A slice is an ordinary recursive split running later than usual. The decision
that a leaf still needs one belongs to
:func:`synthorg.engine.initiative.completion.workstream_needs_slice`; this
module only acts on that answer: decompose the leaf's remaining claimed scope
under a fresh planning budget, append the result under the leaf via a
version-guarded update, file the new tasks, and hand the plan back to the same
:class:`~synthorg.engine.initiative.ports.PlanDriver` port every other
dispatch edge uses.

Nothing here touches plan status. A workstream's slice landing keeps the plan
at ``EXECUTING``: the newly grafted items are not yet done, so the rollup's own
``derive_plan_status`` call naturally holds the plan there on the next pass,
with no branch added to that function.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Final

from synthorg.core.agent import AgentIdentity
from synthorg.core.clock import Clock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.persistence_errors import PersistenceVersionConflictError
from synthorg.core.plan import Plan, PlanItem
from synthorg.core.plan_tree import PlanTree
from synthorg.core.task import Task
from synthorg.core.types import NotBlankStr
from synthorg.engine.decomposition._ids import subtask_uuid
from synthorg.engine.decomposition.context import DecompositionContext
from synthorg.engine.decomposition.models import DecompositionResult
from synthorg.engine.decomposition.plan_mapping import items_from_decomposition
from synthorg.engine.decomposition.service import DecompositionService
from synthorg.engine.initiative.ports import DriveOutcome, PlanDriver
from synthorg.engine.initiative.slice_autonomy import (
    EffectiveAutonomyForPlan,
    auto_approved,
)
from synthorg.engine.initiative.slice_state import (
    SliceDisposition,
    workstream_slice_generation,
)
from synthorg.engine.initiative.stage_runner import StageRunner
from synthorg.engine.task_engine import TaskEngine
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.initiative import (
    INITIATIVE_SLICE_CONSIDERED,
    INITIATIVE_SLICE_DRIVE_FAILED,
    INITIATIVE_SLICE_FAILED,
    INITIATIVE_SLICE_GRAFTED,
    INITIATIVE_SLICE_GRANTED,
    INITIATIVE_SLICE_REFUSED,
    INITIATIVE_SLICE_SETTINGS_DEGRADED,
    INITIATIVE_SLICE_VERSION_CONFLICT,
)
from synthorg.persistence.plan_protocol import PlanRepository
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.settings.resolver import ConfigResolver

#: Resolves the accountable owner for a plan's initiative, as
#: ``ReplanTriggerService._owner`` already does; threaded in rather than
#: called directly so this module never needs the agent registry or
#: persistence dependencies that resolution carries.
OwnerResolver = Callable[[Plan], Awaitable[AgentIdentity | None]]

#: Resolves the roles the org staffs right now, as
#: ``ReplanTriggerService._roster`` already does.
RosterResolver = Callable[[], Awaitable[tuple[NotBlankStr, ...]]]

logger = get_logger(__name__)

#: Fallbacks for when no resolver is wired or a read fails, on the same
#: reasoning ``replan_trigger.py`` uses for its own pair: capture stays on (a
#: settings outage must not silently stop a workstream that could otherwise
#: finish its objective) and the generation cap stays tight.
DEFAULT_SLICE_ENABLED: Final[bool] = True
DEFAULT_SLICE_MAX_GENERATIONS: Final[int] = 2

#: Mirrors ``replan_trigger.py``'s own fallback: capture stays on, and a
#: slice attempt that hangs is bounded rather than left to run forever.
_DEFAULT_TIMEOUT_SECONDS: Final[float] = 600.0


async def resolve_slice_enabled(resolver: ConfigResolver | None) -> bool:
    """Return whether auto-slicing is switched on right now.

    Returns:
        The live ``engine.auto_slice_enabled`` value, or the default when no
        resolver is wired or the read fails.
    """
    if resolver is None:
        return DEFAULT_SLICE_ENABLED
    try:
        return await resolver.get_bool("engine", "auto_slice_enabled")
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        # lint-allow: swallow-ok -- best-effort settings read
        reraise_critical(exc)
        logger.warning(
            INITIATIVE_SLICE_SETTINGS_DEGRADED,
            key="auto_slice_enabled",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return DEFAULT_SLICE_ENABLED


async def resolve_slice_max_generations(resolver: ConfigResolver | None) -> int:
    """Return the live per-workstream slice generation cap.

    Returns:
        The ``engine.auto_slice_max_generations`` value, or the default when
        no resolver is wired or the read fails.
    """
    if resolver is None:
        return DEFAULT_SLICE_MAX_GENERATIONS
    try:
        return await resolver.get_int("engine", "auto_slice_max_generations")
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        # lint-allow: swallow-ok -- best-effort settings read
        reraise_critical(exc)
        logger.warning(
            INITIATIVE_SLICE_SETTINGS_DEGRADED,
            key="auto_slice_max_generations",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return DEFAULT_SLICE_MAX_GENERATIONS


@dataclass(frozen=True, slots=True)
class SliceCollaborators:
    """Everything :func:`consider_slice` needs from its caller, as one argument.

    Bundled for the same reason :class:`~synthorg.engine.initiative.ports.
    StagePorts` bundles the tail stages: passing each separately put the
    caller's own method over the argument cap, and every field here is
    resolved from the same service construction the caller already holds.

    Attributes:
        persistence: Backend supplying the plan and task repositories.
        task_engine: Reads the leaf's own dispatched task.
        decomposition_service: Produces the slice's items.
        config_resolver: Live settings source for the master switch and the
            generation cap.
        runner: The service's own :class:`StageRunner`, shared with replan so
            one process-wide view of in-flight work exists per plan.
        owner_resolver: Resolves the plan's accountable owner.
        roster_resolver: Resolves the roles the org staffs right now.
        effective_autonomy: Resolves the autonomy governing the deterministic
            gate below.
        clock: Clock seam for the append's ``updated_at`` stamp.
    """

    persistence: PersistenceBackend
    task_engine: TaskEngine
    decomposition_service: DecompositionService
    config_resolver: ConfigResolver | None
    runner: StageRunner
    owner_resolver: OwnerResolver
    roster_resolver: RosterResolver
    effective_autonomy: EffectiveAutonomyForPlan
    clock: Clock


async def consider_slice(
    *,
    plan: Plan,
    tree: PlanTree,
    workstream: PlanItem,
    leaf: PlanItem,
    drive: PlanDriver | None,
    collaborators: SliceCollaborators,
) -> SliceDisposition:
    """Graft another slice onto *leaf* if the org may still do so unasked.

    Every guard is decided HERE, before any work starts, on the same
    reasoning ``ReplanTriggerService.consider`` carries for a stall: a caller
    reading "a trigger is attached" as "a slice will be planned" would
    otherwise re-ask on every recompute for a workstream whose budget is
    already spent, for ever. ``ASKED`` is not a refusal in that sense: it
    means a person, not an automatic rule, decides next, and it is the caller
    that turns that into a parked decision (:mod:`slice_escalation`) or, where
    nothing can ask, into simply not holding the plan.

    Returns immediately once the work is started; the graft itself runs
    detached on a tracked task, keyed per workstream rather than per plan so
    two workstreams in one plan may slice independently. Safe to call from
    the best-effort rollup: it never raises.

    Returns:
        What became of the ask.
    """
    key = f"slice:{plan.id}:{leaf.id}"
    if key in collaborators.runner.inflight:
        return SliceDisposition.ALREADY_RUNNING
    if not await resolve_slice_enabled(collaborators.config_resolver):
        return _refuse_slice(plan, leaf, SliceDisposition.DISABLED)
    generation = workstream_slice_generation(plan.items, tree, workstream)
    if generation >= await resolve_slice_max_generations(collaborators.config_resolver):
        return _refuse_slice(plan, leaf, SliceDisposition.BUDGET_EXHAUSTED)
    if not await auto_approved(collaborators.effective_autonomy, plan):
        return _refuse_slice(plan, leaf, SliceDisposition.ASKED)

    async def _timeout() -> float:
        return _DEFAULT_TIMEOUT_SECONDS

    started = collaborators.runner.start(
        key=key,
        work=_run_slice(plan, leaf, drive=drive, collaborators=collaborators),
        deadline=_timeout,
        fallback_seconds=_DEFAULT_TIMEOUT_SECONDS,
        fields={"plan_id": str(plan.id), "leaf_id": leaf.id},
    )
    if not started:
        return SliceDisposition.UNAVAILABLE
    logger.info(
        INITIATIVE_SLICE_CONSIDERED,
        plan_id=str(plan.id),
        leaf_id=leaf.id,
        generation=generation,
    )
    return SliceDisposition.GRAFTED


async def grant_slice(
    *,
    plan: Plan,
    leaf: PlanItem,
    drive: PlanDriver | None,
    requested_by: str,
    collaborators: SliceCollaborators,
) -> bool:
    """Graft *leaf*'s slice once on a person's authority, gates aside.

    The other door on the same owner ``consider_slice`` is. Neither the
    master switch, the generation cap, nor the deterministic autonomy gate
    applies: all three bound what the organisation does UNASKED, and somebody
    has just asked. Only ``ALREADY_RUNNING`` is still checked, because
    granting a slice already in flight would be a second dispatch onto the
    same leaf rather than an answer to anything.

    Returns:
        Whether the detached graft started.
    """
    key = f"slice:{plan.id}:{leaf.id}"
    if key in collaborators.runner.inflight:
        return False

    async def _timeout() -> float:
        return _DEFAULT_TIMEOUT_SECONDS

    started = collaborators.runner.start(
        key=key,
        work=_run_slice(plan, leaf, drive=drive, collaborators=collaborators),
        deadline=_timeout,
        fallback_seconds=_DEFAULT_TIMEOUT_SECONDS,
        fields={"plan_id": str(plan.id), "leaf_id": leaf.id},
    )
    if started:
        logger.info(
            INITIATIVE_SLICE_GRANTED,
            plan_id=str(plan.id),
            leaf_id=leaf.id,
            requested_by=requested_by,
        )
    return started


def _refuse_slice(
    plan: Plan, leaf: PlanItem, disposition: SliceDisposition
) -> SliceDisposition:
    """Log a slice refusal at WARNING and hand it back to the caller.

    Returns:
        *disposition*, unchanged, so the caller reads one expression.
    """
    logger.warning(
        INITIATIVE_SLICE_REFUSED,
        plan_id=str(plan.id),
        leaf_id=leaf.id,
        disposition=disposition.value,
    )
    return disposition


async def _run_slice(
    plan: Plan,
    leaf: PlanItem,
    *,
    drive: PlanDriver | None,
    collaborators: SliceCollaborators,
) -> None:
    """Re-confirm *leaf* still needs a slice, then decompose and graft it.

    Re-read rather than trusted, the same staleness guard a stall re-check
    applies: this attempt was scheduled from a snapshot that may have changed
    by the time it actually runs, and a leaf that already gained children
    (another writer sliced it first) or lost its ``unsplit_reason`` (an
    operator edited the plan) has nothing left to slice.
    """
    fresh = await collaborators.persistence.plans.get(NotBlankStr(str(plan.id)))
    if fresh is None:
        return
    fresh_leaf = next((item for item in fresh.items if item.id == leaf.id), None)
    if fresh_leaf is None or fresh_leaf.unsplit_reason is None:
        return
    leaf_task = await collaborators.task_engine.get_task(str(subtask_uuid(leaf.id)))
    if leaf_task is None:
        logger.warning(
            INITIATIVE_SLICE_FAILED,
            plan_id=str(fresh.id),
            leaf_id=leaf.id,
            reason="leaf_task_missing",
        )
        return
    roster = DecompositionContext(
        owner_identity=await collaborators.owner_resolver(fresh),
        available_roles=await collaborators.roster_resolver(),
    )
    result = await graft_slice(
        fresh,
        fresh_leaf,
        leaf_task=leaf_task,
        roster=roster,
        decomposition_service=collaborators.decomposition_service,
        persistence=collaborators.persistence,
        clock=collaborators.clock,
        drive=drive,
    )
    if result is None:
        logger.warning(INITIATIVE_SLICE_FAILED, plan_id=str(fresh.id), leaf_id=leaf.id)


def _slice_context(
    leaf: PlanItem, *, roster: DecompositionContext
) -> DecompositionContext:
    """Build the context the slice decomposes under, as a fresh root.

    A genuinely new decomposition, not one more level of the original tree:
    ``current_depth`` and ``address`` both start at their defaults (zero and
    empty) because the original tree's depth budget and evidence-directory
    address were already spent reaching *leaf*, and ``max_depth``/
    ``max_subtasks`` are left undeclared so the service resolves them live,
    exactly as any other decomposition would.

    ``objective_criteria`` is declared directly as *leaf*'s own ``satisfies``
    set, not narrowed through ``child_context``: that helper narrows a
    PARENT's already-stamped vocabulary down to a child's claim inside one
    matching objective, and *leaf*'s claim has no such parent here (this
    context IS the new tree's root, and declaring the field directly is what
    stops ``DecompositionService.decompose_task``'s own
    ``stamp_objective_criteria`` overwriting it from the leaf task's
    acceptance criteria instead).

    Args:
        leaf: The oversized, completed leaf being sliced further.
        roster: The caller's roster/owner context (``available_roles``,
            ``owner_identity``), with everything else left at its default so
            this call resolves its own budgets and depth.

    Returns:
        The context the slice's root level plans under.
    """
    return DecompositionContext(
        objective_criteria=leaf.satisfies,
        owner_identity=roster.owner_identity,
        available_roles=roster.available_roles,
    )


def _reparent_root(
    items: tuple[PlanItem, ...], *, parent_id: str
) -> tuple[PlanItem, ...]:
    """Move a decomposition's root-level items under *parent_id*.

    :func:`items_from_decomposition` always projects a decomposition's own
    root level as parentless, because it is normally called at the top of a
    fresh tree, where that root IS a workstream. A slice's root is not a
    workstream: it is the next level under an existing leaf, so only the
    items with no parent of their own (the slice's root level) are re-parented;
    every deeper level already carries its own local parent's id unchanged.

    Returns:
        *items* with its parentless (root) items re-parented onto
        *parent_id*.
    """
    return tuple(
        item
        if item.parent_id is not None
        else item.model_copy(update={"parent_id": parent_id})
        for item in items
    )


async def _file_new_tasks(
    persistence: PersistenceBackend, tasks: tuple[Task, ...]
) -> None:
    """Persist only the tasks a slice actually adds.

    Filed the same way a resumed run files a stopped dispatch's missing rows
    (:func:`synthorg.api.lifecycle_helpers.run_recovery_wiring._file_missing_
    children`): every id here is freshly derived from a brand-new subtask, so
    in the ordinary case none exist yet, but re-saving one that did would
    reset whatever status it had already reached.
    """
    probes = [(task, await persistence.tasks.get(str(task.id))) for task in tasks]
    missing = tuple(task for task, existing in probes if existing is None)
    if missing:
        await persistence.tasks.save_many(missing)


async def _append_slice(
    plans: PlanRepository,
    plan: Plan,
    new_items: tuple[PlanItem, ...],
    *,
    clock: Clock,
) -> Plan | None:
    """Append *new_items* to *plan* under a version guard, retried once.

    Bumps ``version`` and ``updated_at`` itself: the repository's ``update``
    writes exactly the row it is handed (see ``Plan.fail`` for the same
    convention on the failure path), so a caller that appended without
    bumping either would write the SAME version back, leaving a concurrent
    reader's own optimistic-concurrency check none the wiser that this write
    ever happened.

    A conflict means another writer landed between the read this slice was
    considered against and this write; re-reading and retrying the same
    append once is the ordinary recovery, on the same shape every other
    version-guarded write in this codebase uses. A second conflict is left
    for the next rollup recompute, which re-derives the same "needs a slice"
    answer from scratch and tries again.

    Returns:
        The updated, persisted plan, or ``None`` when both attempts conflicted.
    """
    candidate = plan
    for _ in range(2):
        updated = candidate.model_copy(
            update={
                "items": candidate.items + new_items,
                "version": candidate.version + 1,
                "updated_at": clock.now(),
            }
        )
        try:
            await plans.update(updated, expected_version=candidate.version)
        except PersistenceVersionConflictError:
            fresh = await plans.get(NotBlankStr(str(plan.id)))
            if fresh is None:
                return None
            logger.info(
                INITIATIVE_SLICE_VERSION_CONFLICT,
                plan_id=str(plan.id),
                note="another writer moved the plan; retrying the append once",
            )
            candidate = fresh
            continue
        return updated
    return None


async def graft_slice(
    plan: Plan,
    leaf: PlanItem,
    *,
    leaf_task: Task,
    roster: DecompositionContext,
    decomposition_service: DecompositionService,
    persistence: PersistenceBackend,
    clock: Clock,
    drive: PlanDriver | None,
) -> Plan | None:
    """Decompose *leaf*'s remaining scope and graft it onto *plan*.

    The ordinary recursive split, run after the fact: *leaf* already
    dispatched and completed, so this asks the same decomposition service the
    same question a mid-tree recursion would have asked at planning time, had
    the backstop that produced ``leaf.unsplit_reason`` not stopped it first.

    Returns:
        The plan as the graft left it, or ``None`` when the leaf's own task
        could not be re-read, the decomposition produced nothing, or the
        append lost both its attempts to a concurrent writer.
    """
    result: DecompositionResult = await decomposition_service.decompose_task(
        leaf_task, _slice_context(leaf, roster=roster)
    )
    new_items = _reparent_root(items_from_decomposition(result), parent_id=leaf.id)
    updated = await _append_slice(persistence.plans, plan, new_items, clock=clock)
    if updated is None:
        return None
    await _file_new_tasks(persistence, result.all_tasks)
    logger.info(
        INITIATIVE_SLICE_GRAFTED,
        plan_id=str(plan.id),
        leaf_id=leaf.id,
        item_count=len(new_items),
    )
    if drive is not None:
        outcome = await drive(updated)
        if outcome is DriveOutcome.REFUSED:
            logger.warning(
                INITIATIVE_SLICE_DRIVE_FAILED,
                plan_id=str(plan.id),
                leaf_id=leaf.id,
            )
    return updated


__all__ = [
    "DEFAULT_SLICE_ENABLED",
    "DEFAULT_SLICE_MAX_GENERATIONS",
    "OwnerResolver",
    "RosterResolver",
    "SliceCollaborators",
    "consider_slice",
    "graft_slice",
    "grant_slice",
    "resolve_slice_enabled",
    "resolve_slice_max_generations",
]
