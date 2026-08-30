# module-kind: code
"""Graft a workstream's next extension onto a live plan.

Split out from :mod:`synthorg.engine.initiative.replan_trigger` for the same
reason :mod:`synthorg.engine.initiative.rollup_stages` was split out of the
rollup: this is the part of the trigger that grows every time the graft's own
shape changes, and keeping it here is what lets the service stay within its
module-size tier.

An extension is an ordinary recursive split running later than usual. The
decision that a leaf still needs one belongs to
:func:`synthorg.engine.initiative.extension_state.workstream_needs_extension`;
this module only acts on that answer: decompose the leaf's remaining claimed
scope under a fresh planning budget, append the result under the leaf via a
version-guarded update, file the new tasks, and hand the plan back to the same
:class:`~synthorg.engine.initiative.ports.PlanDriver` port every other
dispatch edge uses.

Nothing here touches plan status. A workstream's extension landing keeps the
plan at ``EXECUTING``: the newly grafted items are not yet done, so the
rollup's own ``derive_plan_status`` call naturally holds the plan there on the
next pass, with no branch added to that function.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Final
from uuid import uuid4

from synthorg.core.agent import AgentIdentity
from synthorg.core.clock import Clock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.persistence_errors import PersistenceVersionConflictError
from synthorg.core.plan import Plan, PlanItem
from synthorg.core.plan_tree import PlanTree
from synthorg.core.task import Task
from synthorg.core.types import NotBlankStr
from synthorg.engine.assembly import assembly_title, build_assembly
from synthorg.engine.decomposition._ids import subtask_uuid
from synthorg.engine.decomposition._item_tasks import task_from_item
from synthorg.engine.decomposition.context import DecompositionContext
from synthorg.engine.decomposition.models import DecompositionResult
from synthorg.engine.decomposition.plan_mapping import items_from_decomposition
from synthorg.engine.decomposition.service import DecompositionService
from synthorg.engine.initiative.extension_autonomy import (
    EffectiveAutonomyForPlan,
    auto_approved,
)
from synthorg.engine.initiative.extension_state import (
    ExtensionDisposition,
    workstream_extension_generation,
)
from synthorg.engine.initiative.ports import DriveOutcome, PlanDriver
from synthorg.engine.initiative.stage_runner import DeadlineResolver, StageRunner
from synthorg.engine.task_engine import TaskEngine
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.initiative import (
    INITIATIVE_EXTENSION_CONSIDERED,
    INITIATIVE_EXTENSION_DRIVE_FAILED,
    INITIATIVE_EXTENSION_FAILED,
    INITIATIVE_EXTENSION_GRAFTED,
    INITIATIVE_EXTENSION_GRANTED,
    INITIATIVE_EXTENSION_REFUSED,
    INITIATIVE_EXTENSION_SETTINGS_DEGRADED,
    INITIATIVE_EXTENSION_SKIPPED,
    INITIATIVE_EXTENSION_VERSION_CONFLICT,
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
DEFAULT_EXTENSION_ENABLED: Final[bool] = True
DEFAULT_EXTENSION_MAX_GENERATIONS: Final[int] = 2

#: Mirrors ``replan_trigger.py``'s own fallback: capture stays on, and an
#: extension attempt that hangs is bounded rather than left to run forever.
_DEFAULT_TIMEOUT_SECONDS: Final[float] = 600.0


async def resolve_extension_enabled(resolver: ConfigResolver | None) -> bool:
    """Return whether auto-extension is switched on right now.

    Returns:
        The live ``engine.auto_extension_enabled`` value, or the default when no
        resolver is wired or the read fails.
    """
    if resolver is None:
        return DEFAULT_EXTENSION_ENABLED
    try:
        return await resolver.get_bool("engine", "auto_extension_enabled")
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        # lint-allow: swallow-ok -- best-effort settings read
        reraise_critical(exc)
        logger.warning(
            INITIATIVE_EXTENSION_SETTINGS_DEGRADED,
            key="auto_extension_enabled",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return DEFAULT_EXTENSION_ENABLED


async def resolve_extension_max_generations(resolver: ConfigResolver | None) -> int:
    """Return the live per-workstream extension generation cap.

    Returns:
        The ``engine.auto_extension_max_generations`` value, or the default when
        no resolver is wired or the read fails.
    """
    if resolver is None:
        return DEFAULT_EXTENSION_MAX_GENERATIONS
    try:
        return await resolver.get_int("engine", "auto_extension_max_generations")
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        # lint-allow: swallow-ok -- best-effort settings read
        reraise_critical(exc)
        logger.warning(
            INITIATIVE_EXTENSION_SETTINGS_DEGRADED,
            key="auto_extension_max_generations",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return DEFAULT_EXTENSION_MAX_GENERATIONS


async def resolve_extension_timeout_seconds(resolver: ConfigResolver | None) -> float:
    """Return the live wall-clock ceiling on one extension attempt.

    Returns:
        The ``engine.auto_extension_timeout_seconds`` value, or the default
        when no resolver is wired, the read fails, or it resolved to a
        non-positive value.
    """
    if resolver is None:
        return _DEFAULT_TIMEOUT_SECONDS
    try:
        resolved = await resolver.get_float("engine", "auto_extension_timeout_seconds")
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        # lint-allow: swallow-ok -- best-effort settings read
        reraise_critical(exc)
        logger.warning(
            INITIATIVE_EXTENSION_SETTINGS_DEGRADED,
            key="auto_extension_timeout_seconds",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return _DEFAULT_TIMEOUT_SECONDS
    return resolved if resolved > 0 else _DEFAULT_TIMEOUT_SECONDS


@dataclass(frozen=True, slots=True)
class ExtensionCollaborators:
    """Everything :func:`consider_extension` needs from its caller, as one argument.

    Bundled for the same reason :class:`~synthorg.engine.initiative.ports.
    StagePorts` bundles the tail stages: passing each separately put the
    caller's own method over the argument cap, and every field here is
    resolved from the same service construction the caller already holds.

    Attributes:
        persistence: Backend supplying the plan and task repositories.
        task_engine: Reads the leaf's own dispatched task.
        decomposition_service: Produces the extension's items.
        config_resolver: Live settings source for the master switch, the
            generation cap, and the per-attempt timeout. ``None`` fails
            OPEN: every resolver function above falls back to its default
            (capture stays on, the cap stays tight) rather than refusing an
            extension a settings outage cannot otherwise explain.
        runner: The service's own extension-dedicated :class:`StageRunner`
            (not shared with replan, so the two mechanisms' timeouts and
            failures are never misattributed to one another), giving one
            process-wide view of in-flight extension work per plan.
        owner_resolver: Resolves the plan's accountable owner.
        roster_resolver: Resolves the roles the org staffs right now.
        effective_autonomy: Resolves the autonomy governing the deterministic
            gate below. ``None`` fails CLOSED: :func:`auto_approved` reads an
            unresolved autonomy as "not explicitly granted", the same
            posture an unreadable project row takes, since a missing
            resolution is not evidence a person already agreed to this.
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


def _in_flight_key(plan: Plan, workstream: PlanItem) -> str:
    """The in-flight key one workstream's extension work runs under.

    Keyed on the WORKSTREAM rather than the leaf: the generation cap in
    :func:`consider_extension` is read from the workstream's whole subtree, so
    two of its oversized leaves considered on the same rollup pass would
    otherwise both read the same pre-graft generation count and both pass the
    cap, landing one extension too many between them. Keying here serialises
    them instead: the second leaf's ``consider_extension`` call sees
    ``ALREADY_RUNNING`` for this same key and defers to a later pass, by which
    time the first extension has either landed (changing the count the second
    leaf would be judged against) or failed (freeing the key).

    Returns:
        The key.
    """
    return f"extension:{plan.id}:{workstream.id}"


def _timeout_resolver(collaborators: ExtensionCollaborators) -> DeadlineResolver:
    """Build the deadline resolver an extension attempt runs under.

    Shared by both doors so a live ``engine.auto_extension_timeout_seconds``
    change applies to a grant exactly as it does to an automatic ask, and
    reads it fresh per call rather than snapshotting it: the timeout only
    matters for whichever attempt this key starts right now.

    Returns:
        A zero-argument resolver reading the live timeout on each call.
    """

    async def _timeout() -> float:
        return await resolve_extension_timeout_seconds(collaborators.config_resolver)

    return _timeout


async def consider_extension(
    *,
    plan: Plan,
    tree: PlanTree,
    workstream: PlanItem,
    leaf: PlanItem,
    drive: PlanDriver | None,
    collaborators: ExtensionCollaborators,
) -> ExtensionDisposition:
    """Graft another extension onto *leaf* if the org may still do so unasked.

    Every guard is decided HERE, before any work starts, on the same
    reasoning ``ReplanTriggerService.consider`` carries for a stall: a caller
    reading "a trigger is attached" as "an extension will be planned" would
    otherwise re-ask on every recompute for a workstream whose budget is
    already spent, for ever. ``ASKED`` is not a refusal in that sense: it
    means a person, not an automatic rule, decides next, and it is the caller
    that turns that into a parked decision (:mod:`extension_escalation`) or,
    where nothing can ask, into simply not holding the plan.

    Returns immediately once the work is started; the graft itself runs
    detached on a tracked task, keyed per workstream (see
    :func:`_in_flight_key`) so two workstreams in one plan may extend
    independently while two leaves within one workstream serialise. Safe to
    call from the best-effort rollup: it never raises.

    Returns:
        What became of the ask.
    """
    key = _in_flight_key(plan, workstream)
    if key in collaborators.runner.inflight:
        return ExtensionDisposition.ALREADY_RUNNING
    if not await resolve_extension_enabled(collaborators.config_resolver):
        return _refuse_extension(plan, leaf, ExtensionDisposition.DISABLED)
    generation = workstream_extension_generation(plan.items, tree, workstream)
    cap = await resolve_extension_max_generations(collaborators.config_resolver)
    if generation >= cap:
        return _refuse_extension(plan, leaf, ExtensionDisposition.BUDGET_EXHAUSTED)
    if not await auto_approved(collaborators.effective_autonomy, plan):
        return _refuse_extension(plan, leaf, ExtensionDisposition.ASKED)

    started = collaborators.runner.start(
        key=key,
        work=_run_extension(plan, leaf, drive=drive, collaborators=collaborators),
        deadline=_timeout_resolver(collaborators),
        fallback_seconds=_DEFAULT_TIMEOUT_SECONDS,
        fields={"plan_id": str(plan.id), "leaf_id": leaf.id},
    )
    if not started:
        # The three awaits above (settings, cap, autonomy) are where a second
        # caller for this same key could land: the loser of that race reads
        # UNAVAILABLE only when the runner itself genuinely refused to start
        # anything; when another attempt for this key won it in the meantime,
        # the accurate answer is the one the caller would have gotten from
        # the inflight check above, had it run a moment later.
        if key in collaborators.runner.inflight:
            return ExtensionDisposition.ALREADY_RUNNING
        return ExtensionDisposition.UNAVAILABLE
    logger.info(
        INITIATIVE_EXTENSION_CONSIDERED,
        plan_id=str(plan.id),
        leaf_id=leaf.id,
        generation=generation,
    )
    return ExtensionDisposition.GRAFTED


async def grant_extension(
    *,
    plan: Plan,
    workstream: PlanItem,
    leaf: PlanItem,
    drive: PlanDriver | None,
    requested_by: str,
    collaborators: ExtensionCollaborators,
) -> bool:
    """Graft *leaf*'s extension once on a person's authority, gates aside.

    The other door on the same owner ``consider_extension`` is. Neither the
    master switch, the generation cap, nor the deterministic autonomy gate
    applies: all three bound what the organisation does UNASKED, and somebody
    has just asked. Only ``ALREADY_RUNNING`` is still checked, keyed the same
    way ``consider_extension`` keys it (per workstream): granting an extension
    while one is already in flight for this workstream would be a second,
    uncoordinated dispatch rather than an answer to anything.

    Returns:
        Whether the detached graft started.
    """
    key = _in_flight_key(plan, workstream)
    if key in collaborators.runner.inflight:
        return False

    started = collaborators.runner.start(
        key=key,
        work=_run_extension(plan, leaf, drive=drive, collaborators=collaborators),
        deadline=_timeout_resolver(collaborators),
        fallback_seconds=_DEFAULT_TIMEOUT_SECONDS,
        fields={"plan_id": str(plan.id), "leaf_id": leaf.id},
    )
    if started:
        logger.info(
            INITIATIVE_EXTENSION_GRANTED,
            plan_id=str(plan.id),
            leaf_id=leaf.id,
            requested_by=requested_by,
        )
    return started


def _refuse_extension(
    plan: Plan, leaf: PlanItem, disposition: ExtensionDisposition
) -> ExtensionDisposition:
    """Log an extension refusal at WARNING and hand it back to the caller.

    Returns:
        *disposition*, unchanged, so the caller reads one expression.
    """
    logger.warning(
        INITIATIVE_EXTENSION_REFUSED,
        plan_id=str(plan.id),
        leaf_id=leaf.id,
        disposition=disposition.value,
    )
    return disposition


async def _run_extension(
    plan: Plan,
    leaf: PlanItem,
    *,
    drive: PlanDriver | None,
    collaborators: ExtensionCollaborators,
) -> None:
    """Re-confirm *leaf* still needs an extension, then decompose and graft it.

    Re-read rather than trusted, the same staleness guard a stall re-check
    applies: this attempt was scheduled from a snapshot that may have changed
    by the time it actually runs. ``unsplit_reason`` is never cleared once
    written (see :mod:`extension_state`), so it cannot by itself tell a leaf
    still awaiting its extension apart from one that already received it;
    only the freshly rebuilt tree can say whether *leaf* already gained
    children (another writer grafted it first), which is the actual condition
    that means nothing is left to extend here. A leaf whose plan or item
    vanished entirely (an operator deleted it) has nothing left either.
    """
    fresh = await collaborators.persistence.plans.get(NotBlankStr(str(plan.id)))
    if fresh is None:
        logger.debug(
            INITIATIVE_EXTENSION_SKIPPED,
            plan_id=str(plan.id),
            leaf_id=leaf.id,
            reason="plan_missing",
        )
        return
    fresh_leaf = next((item for item in fresh.items if item.id == leaf.id), None)
    fresh_tree = PlanTree.of(fresh.items)
    if (
        fresh_leaf is None
        or fresh_leaf.unsplit_reason is None
        or fresh_tree.is_container(fresh_leaf.id)
    ):
        logger.debug(
            INITIATIVE_EXTENSION_SKIPPED,
            plan_id=str(fresh.id),
            leaf_id=leaf.id,
            reason="leaf_gone_or_already_extended",
        )
        return
    leaf_task = await collaborators.task_engine.get_task(str(subtask_uuid(leaf.id)))
    if leaf_task is None:
        logger.warning(
            INITIATIVE_EXTENSION_FAILED,
            plan_id=str(fresh.id),
            leaf_id=leaf.id,
            reason="leaf_task_missing",
        )
        return
    roster = DecompositionContext(
        owner_identity=await collaborators.owner_resolver(fresh),
        available_roles=await collaborators.roster_resolver(),
    )
    result = await graft_extension(
        fresh,
        fresh_leaf,
        leaf_task=leaf_task,
        roster=roster,
        tree=fresh_tree,
        collaborators=collaborators,
        drive=drive,
    )
    if result is None:
        # The specific cause (a lost version-conflict retry, a plan deleted
        # mid-append, or a refused drive) was already logged by
        # ``graft_extension`` under its own dedicated event; this is the
        # generic "an extension attempt did not land" signal above that.
        logger.warning(
            INITIATIVE_EXTENSION_FAILED,
            plan_id=str(fresh.id),
            leaf_id=leaf.id,
            reason="graft_failed",
        )


def _extension_context(
    leaf: PlanItem, *, roster: DecompositionContext
) -> DecompositionContext:
    """Build the context the extension decomposes under, as a fresh root.

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
        leaf: The oversized, completed leaf being extended further.
        roster: The caller's roster/owner context (``available_roles``,
            ``owner_identity``), with everything else left at its default so
            this call resolves its own budgets and depth.

    Returns:
        The context the extension's root level plans under.
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
    fresh tree, where that root IS a workstream. An extension's root is not a
    workstream: it is the next level under an existing leaf, so only the
    items with no parent of their own (the extension's root level) are
    re-parented; every deeper level already carries its own local parent's id
    unchanged.

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


def _extension_assembly_item(
    leaf: PlanItem, new_root_items: tuple[PlanItem, ...], *, tree: PlanTree
) -> PlanItem:
    """Build the item that assembles *leaf*'s newly grafted children.

    *leaf*'s own task already completed under ``subtask_uuid(leaf.id)``, and
    that id cannot carry a second task: ``item_progress.py`` maps exactly one
    task per plan item by that same derived id, so a container's assembly
    cannot arrive as a rewrite of *leaf*'s own task. It arrives instead as one
    more ordinary child, alongside the extension's own new items, depending
    on all of them so it runs last. Carrying no children of its own, it reads
    as an ordinary WORK item to :func:`~synthorg.engine.decomposition.
    _item_tasks.assembly_of`, exactly the shape that module's own docstring
    describes: work whose description happens to be an assembly brief. It
    never carries ``unsplit_reason``, so it cannot be mistaken for a leaf
    still awaiting its own extension, nor inflate
    :func:`~synthorg.engine.initiative.extension_state.
    workstream_extension_generation`'s count.

    Addressed by *leaf*'s own tree position rather than its own (the address
    a real container's assembly would use, had *leaf*'s task been rewritable
    into one), so its evidence lands where any later derivation of "the
    assembly of leaf's subtree" would independently look for it.

    Returns:
        The assembly item, parented under *leaf*.
    """
    assembly = build_assembly(
        title=str(leaf.title),
        pieces=[str(item.title) for item in new_root_items],
        criteria=[str(criterion) for criterion in leaf.acceptance_criteria],
        assembled=[item.stakes for item in new_root_items],
        address=tree.address(leaf.id),
    )
    return PlanItem(
        id=str(uuid4()),
        title=NotBlankStr(assembly_title(str(leaf.title))),
        description=NotBlankStr(assembly.brief),
        parent_id=leaf.id,
        dependencies=tuple(item.id for item in new_root_items),
        acceptance_criteria=leaf.acceptance_criteria,
        expected_artifacts=tuple(NotBlankStr(p) for p in assembly.paths.declared),
        stakes=assembly.stakes,
        satisfies=leaf.satisfies,
    )


async def _file_new_tasks(
    persistence: PersistenceBackend, tasks: tuple[Task, ...]
) -> None:
    """Persist only the tasks an extension actually adds.

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


async def _append_extension(
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

    A conflict means another writer landed between the read this extension
    was considered against and this write; re-reading and retrying the same
    append once is the ordinary recovery, on the same shape every other
    version-guarded write in this codebase uses. A second conflict is left
    for the next rollup recompute, which re-derives the same "needs an
    extension" answer from scratch and tries again; contention beyond that is
    what keying the in-flight guard per workstream (see :func:`_in_flight_key`)
    exists to keep rare, by never running two extensions for one workstream at
    once.

    Returns:
        The updated, persisted plan, or ``None`` when both attempts
        conflicted, or the plan was deleted while this was retrying.
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
                logger.warning(
                    INITIATIVE_EXTENSION_VERSION_CONFLICT,
                    plan_id=str(plan.id),
                    note="plan no longer exists; abandoning the append",
                )
                return None
            logger.info(
                INITIATIVE_EXTENSION_VERSION_CONFLICT,
                plan_id=str(plan.id),
                note="another writer moved the plan; retrying the append once",
            )
            candidate = fresh
            continue
        return updated
    return None


async def graft_extension(
    plan: Plan,
    leaf: PlanItem,
    *,
    leaf_task: Task,
    roster: DecompositionContext,
    tree: PlanTree,
    collaborators: ExtensionCollaborators,
    drive: PlanDriver | None,
) -> Plan | None:
    """Decompose *leaf*'s remaining scope and graft it onto *plan*.

    The ordinary recursive split, run after the fact: *leaf* already
    dispatched and completed, so this asks the same decomposition service the
    same question a mid-tree recursion would have asked at planning time, had
    the backstop that produced ``leaf.unsplit_reason`` not stopped it first.
    Alongside the decomposition's own items, one more is grafted: the item
    that assembles them (see :func:`_extension_assembly_item`), since *leaf*
    becoming a container has nobody else positioned to dispatch that job.

    Returns:
        The plan as the graft left it, or ``None`` when the decomposition
        produced nothing, the append lost both its attempts to a concurrent
        writer or a deleted plan, or the driver refused to dispatch it. The
        items and tasks a refused drive already wrote are not rolled back:
        *leaf* is a container from this point on, so it drops out of
        ``workstream_needs_extension``'s own answer on every later pass, and
        it is the general ``RunRecoveryReconciler`` sweep over EXECUTING
        plans, not a second extension check, that requeues the newly-grafted
        tasks a refused drive left undispatched.
    """
    result: DecompositionResult = (
        await collaborators.decomposition_service.decompose_task(
            leaf_task, _extension_context(leaf, roster=roster)
        )
    )
    new_items = _reparent_root(items_from_decomposition(result), parent_id=leaf.id)
    new_root_items = tuple(item for item in new_items if item.parent_id == leaf.id)
    assembly_item = _extension_assembly_item(leaf, new_root_items, tree=tree)
    all_new_items = (*new_items, assembly_item)
    updated = await _append_extension(
        collaborators.persistence.plans, plan, all_new_items, clock=collaborators.clock
    )
    if updated is None:
        return None
    updated_tree = PlanTree.of(updated.items)
    assembly_task = task_from_item(
        assembly_item,
        plan=updated,
        objective=leaf_task,
        parent_task_id=str(leaf_task.id),
        tree=updated_tree,
    )
    await _file_new_tasks(collaborators.persistence, (*result.all_tasks, assembly_task))
    logger.info(
        INITIATIVE_EXTENSION_GRAFTED,
        plan_id=str(plan.id),
        leaf_id=leaf.id,
        item_count=len(all_new_items),
    )
    if drive is not None:
        outcome = await drive(updated)
        if outcome is DriveOutcome.REFUSED:
            logger.warning(
                INITIATIVE_EXTENSION_DRIVE_FAILED,
                plan_id=str(plan.id),
                leaf_id=leaf.id,
            )
            return None
    return updated


__all__ = [
    "DEFAULT_EXTENSION_ENABLED",
    "DEFAULT_EXTENSION_MAX_GENERATIONS",
    "ExtensionCollaborators",
    "OwnerResolver",
    "RosterResolver",
    "consider_extension",
    "graft_extension",
    "grant_extension",
    "resolve_extension_enabled",
    "resolve_extension_max_generations",
    "resolve_extension_timeout_seconds",
]
