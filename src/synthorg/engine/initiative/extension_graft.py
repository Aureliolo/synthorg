# module-kind: code
"""Decide whether and how to graft a workstream's next extension.

Split out from :mod:`synthorg.engine.initiative.replan_trigger` for the same
reason :mod:`synthorg.engine.initiative.rollup_stages` was split out of the
rollup: this is the part of the trigger that grows every time the graft's own
shape changes, and keeping it here is what lets the service stay within its
module-size tier. The mechanics of actually building and persisting a graft
(decomposing, re-parenting, assembling, appending) live in
:mod:`synthorg.engine.initiative.extension_build`, re-exported here as
:func:`graft_extension` so this stays the one module callers import from.

An extension is an ordinary recursive split running later than usual. The
decision that a leaf still needs one belongs to
:func:`synthorg.engine.initiative.extension_state.workstream_needs_extension`;
this module acts on that answer: it decides whether the organisation may
graft one (unasked, or on a person's authority), runs the graft detached, and
hands the plan back to the same
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

from synthorg.core.agent import AgentIdentity
from synthorg.core.clock import Clock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.plan import Plan, PlanItem
from synthorg.core.plan_tree import PlanTree
from synthorg.core.types import NotBlankStr
from synthorg.engine.decomposition._ids import subtask_uuid
from synthorg.engine.decomposition.context import DecompositionContext
from synthorg.engine.decomposition.service import DecompositionService
from synthorg.engine.initiative.extension_autonomy import (
    EffectiveAutonomyForPlan,
    auto_approved,
)
from synthorg.engine.initiative.extension_build import graft_extension
from synthorg.engine.initiative.extension_state import (
    ExtensionDisposition,
    workstream_extension_generation,
)
from synthorg.engine.initiative.ports import PlanDriver
from synthorg.engine.initiative.stage_runner import DeadlineResolver, StageRunner
from synthorg.engine.task_engine import TaskEngine
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.initiative import (
    INITIATIVE_EXTENSION_CONSIDERED,
    INITIATIVE_EXTENSION_FAILED,
    INITIATIVE_EXTENSION_GRANTED,
    INITIATIVE_EXTENSION_REFUSED,
    INITIATIVE_EXTENSION_SETTINGS_DEGRADED,
    INITIATIVE_EXTENSION_SKIPPED,
)
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
