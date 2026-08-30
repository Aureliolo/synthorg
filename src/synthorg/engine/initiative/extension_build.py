# module-kind: code
"""Build and persist one workstream extension's new items and tasks.

Split out from :mod:`synthorg.engine.initiative.extension_graft` for the same
reason :mod:`synthorg.engine.initiative.extension_state` is its own module:
that file keeps deciding whether and how to start an extension (the guards,
the two doors, the detached run), while this one is purely the mechanics of
turning a decomposition into grafted items, tasks, and a persisted plan, so
each stays within its own module-size tier.
"""

from typing import TYPE_CHECKING
from uuid import uuid4

from synthorg.core.clock import Clock
from synthorg.core.persistence_errors import PersistenceVersionConflictError
from synthorg.core.plan import Plan, PlanItem
from synthorg.core.plan_tree import PlanTree
from synthorg.core.task import Task
from synthorg.core.types import NotBlankStr
from synthorg.engine.assembly import assembly_title, build_assembly
from synthorg.engine.decomposition._item_tasks import task_from_item
from synthorg.engine.decomposition.context import DecompositionContext
from synthorg.engine.decomposition.models import DecompositionResult
from synthorg.engine.decomposition.plan_mapping import items_from_decomposition
from synthorg.engine.initiative.ports import DriveOutcome, PlanDriver
from synthorg.observability import get_logger
from synthorg.observability.events.initiative import (
    INITIATIVE_EXTENSION_DRIVE_FAILED,
    INITIATIVE_EXTENSION_GRAFTED,
    INITIATIVE_EXTENSION_VERSION_CONFLICT,
)
from synthorg.persistence.plan_protocol import PlanRepository
from synthorg.persistence.protocol import PersistenceBackend

if TYPE_CHECKING:
    # Genuine cycle breaker: ``extension_graft`` imports ``graft_extension``
    # from this module, so importing its ``ExtensionCollaborators`` back at
    # module level here would be circular. The type is only ever used in
    # annotations below.
    from synthorg.engine.initiative.extension_graft import ExtensionCollaborators

logger = get_logger(__name__)


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
    what keying the in-flight guard per workstream (see
    ``extension_graft._in_flight_key``) exists to keep rare, by never running
    two extensions for one workstream at once.

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


__all__ = ["graft_extension"]
