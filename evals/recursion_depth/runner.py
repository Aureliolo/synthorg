# module-kind: orchestrator
"""The matrix: plan a tree at each depth cap, run it under each arm, score it.

One run is one ``(depth cap, arm, repetition)``. It plans its own tree, builds
every leaf, assembles every node from the bottom up, and hands the root's tree
to the held-out oracle. The arms differ in exactly one place: what looks at a
merge before the run moves on.

Each arm plans its own tree rather than sharing one. A shared tree would be the
stronger paired design, but the two arms' spend is the check on the whole
result and splitting a shared cost between them is a number nobody could
defend. Tree-shape variance therefore sits inside the comparison, which is why
the achieved-depth histogram is reported per arm: two arms compared at a depth
only one of them reached is two experiments on one axis, and the histogram is
where a reader sees that.

Failures split the way loop A/B splits them. A missing provider, a dead gateway
or a dead Docker daemon is true of every remaining run, so it stops the matrix
rather than being rediscovered once per cell at full retry cost. Anything else
records that one cell as unavailable with its reason and the sweep continues:
the report is always written, and a cell that cost real money is never dropped
from it.
"""

import asyncio
import zlib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from evals.errors import (
    EvalToolMissingError,
    HarnessDockerUnavailableError,
    HarnessGatewayUnavailableError,
    HarnessProviderMissingError,
    OracleUnusableError,
    RecursionDepthGateUnbuildableError,
    RecursionDepthNoCellsMeasuredError,
    RecursionDepthSessionCeilingError,
)
from evals.harness.workspace import CellWorkspace
from evals.recursion_depth.execute import LeafOutcome, leaf_task, run_leaf
from evals.recursion_depth.gate import (
    BlindMergeReviewer,
    MergeReviewer,
    OracleMergeReviewer,
)
from evals.recursion_depth.manifest import Arm, RecursionDepthManifest
from evals.recursion_depth.merge import (
    MergeOutcome,
    MergePiece,
    MergePlan,
    piece_slug,
    run_merge,
)
from evals.recursion_depth.models import (
    LEAF,
    MERGE,
    ORACLE_CAVEAT,
    PLAN,
    SIZING_CAVEAT,
    CellRecord,
    Provenance,
    RecursionDepthReport,
    UnitRecord,
)
from evals.recursion_depth.oracle import run_oracle
from evals.recursion_depth.planner import TreePlanner
from evals.recursion_depth.score import (
    achieved_depth_histogram,
    curve_by_achieved_depth,
    curve_by_depth_cap,
)
from evals.recursion_depth.session import SessionLimits, SweepDeps, unit_workspace
from evals.recursion_depth.staffing import SweepRoster
from evals.recursion_depth.tree import (
    SpecBrief,
    merge_nodes,
    objective_task,
    unit_definitions,
)
from evals.runner.execution import EVAL_TASK_PROJECT
from synthorg.core.agent import AgentIdentity
from synthorg.core.task import Task
from synthorg.core.types import NotBlankStr
from synthorg.engine.decomposition.models import DecompositionResult, SubtaskDefinition
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.evals import (
    EVALS_RECURSION_CELL_RECORDED,
    EVALS_RECURSION_CELL_UNAVAILABLE,
    EVALS_RECURSION_SESSION_CEILING,
)

logger = get_logger(__name__)

#: Failures that are true of the machine or the configuration rather than of a
#: run, so no other run would survive them either. Recording them per cell
#: would spend the rest of a matrix rediscovering one fact.
_SYSTEMIC_FAILURES: tuple[type[Exception], ...] = (
    HarnessProviderMissingError,
    HarnessGatewayUnavailableError,
    HarnessDockerUnavailableError,
    EvalToolMissingError,
    RecursionDepthGateUnbuildableError,
    # The oracle failing to RUN is a property of the machine: every remaining
    # run would build, spend, and then be ungradeable. A tree the oracle grades
    # and fails is an ordinary result and does not come through here.
    OracleUnusableError,
)

_CEILING_CAVEAT: str = (
    "The sweep stopped early on its session ceiling, so the depths and "
    "repetitions the manifest asked for are not all present. Read the cell "
    "list, not the manifest, for what was actually measured."
)


class SessionBudget:
    """How many agent sessions the whole sweep may run.

    Checked between units rather than before each session, so the sweep can
    overrun by at most the unit in flight. Bounding it any tighter would mean
    predicting a merge's repair rounds before they happen, and the cost of
    being wrong here is spend rather than a wrong answer.

    Args:
        ceiling: The sweep's session ceiling.
    """

    __slots__ = ("_ceiling", "_spent")

    def __init__(self, ceiling: int) -> None:
        self._ceiling = ceiling
        self._spent = 0

    @property
    def spent(self) -> int:
        """How many sessions the sweep has run."""
        return self._spent

    def spend(self, sessions: int) -> None:
        """Book *sessions* and refuse to go past the ceiling.

        Args:
            sessions: How many sessions just ran.

        Raises:
            RecursionDepthSessionCeilingError: The sweep has spent its budget.
        """
        self._spent += sessions
        if self._spent > self._ceiling:
            msg = (
                f"the sweep has run {self._spent} sessions against a ceiling of "
                f"{self._ceiling}; raise max_sessions or narrow --depths"
            )
            raise RecursionDepthSessionCeilingError(msg)


@dataclass(frozen=True)
class SweepContext:
    """Everything one sweep is driven with.

    Attributes:
        manifest: The recording matrix.
        spec: The specification, as the planner and the units are told it.
        spec_dir: Where the specification and its held-out oracle live.
        work_root: Directory per-unit trees are created under.
        deps: The runtime collaborators every session is built from.
        roster: The org the work is dispatched to and judged by.
        planner: What writes the tree each run is executed from.
        budget: The sweep's session ceiling.
    """

    manifest: RecursionDepthManifest
    spec: SpecBrief
    spec_dir: Path
    work_root: Path
    deps: SweepDeps
    roster: SweepRoster
    planner: TreePlanner
    budget: SessionBudget

    @property
    def limits(self) -> SessionLimits:
        """The bounds every session in this sweep gets.

        Returns:
            The turn and spend bounds.
        """
        return SessionLimits(
            max_turns=self.manifest.unit_max_turns,
            cost_ceiling=self.manifest.unit_cost_ceiling,
            token_ceiling=self.manifest.unit_token_ceiling,
        )

    def reviewer_for(self, arm: Arm) -> MergeReviewer:
        """What looks at a merge in *arm*.

        Returns:
            The shipped gate, or the blind pass that spends the same budget
            with nobody independent in it.
        """
        if arm is Arm.GATED:
            return OracleMergeReviewer(deps=self.deps, roster=self.roster)
        return BlindMergeReviewer(deps=self.deps)


@dataclass(frozen=True)
class SweepCell:
    """One ``(depth cap, arm, repetition)`` run.

    Attributes:
        depth_cap: The ``max_depth`` this run is allowed.
        arm: Gated or ungated.
        repetition: Zero-based index within the cell.
    """

    depth_cap: int
    arm: Arm
    repetition: int

    @property
    def key(self) -> str:
        """The name this run's trees and ledgers are keyed by.

        Returns:
            The key.
        """
        return f"d{self.depth_cap}-{self.arm.value}-r{self.repetition}"


def planned_cells(manifest: RecursionDepthManifest) -> tuple[SweepCell, ...]:
    """Every run the matrix asks for, in the order it is recorded.

    Arms are adjacent within a repetition so a matrix stopped early has both
    arms of what it did record rather than a complete gated curve and nothing
    to compare it against.

    Args:
        manifest: The recording matrix.

    Returns:
        The runs.
    """
    return tuple(
        SweepCell(depth_cap=depth, arm=arm, repetition=repetition)
        for depth in manifest.depths
        for repetition in range(manifest.repetitions[depth])
        for arm in manifest.arms
    )


async def run_sweep(
    context: SweepContext, *, provenance: Provenance
) -> RecursionDepthReport:
    """Run the whole matrix and assemble the report.

    Args:
        context: Everything the sweep is driven with.
        provenance: What this recording is measured against.

    Returns:
        The report, always written, carrying every run that was attempted.

    Raises:
        RecursionDepthNoCellsMeasuredError: Not one run was measured. An
            all-unavailable report exits successfully with a file that looks
            like a curve.
        HarnessProviderMissingError: A pair names an absent provider.
        HarnessGatewayUnavailableError: The hosted gateway is gone.
        HarnessDockerUnavailableError: The Docker daemon is gone.
        EvalToolMissingError: A grading command is absent from PATH.
        OracleUnusableError: The held-out oracle could not be run at all.
        RecursionDepthGateUnbuildableError: The gated arm has nowhere to read a
            verdict from.
    """
    records: list[CellRecord] = []
    # Seeded, not accumulated: these two hold for every sweep this harness can
    # run, and a report that states them only when something went wrong states
    # them in exactly the runs nobody reads closely.
    caveats: list[str] = [SIZING_CAVEAT, ORACLE_CAVEAT]
    independence = context.manifest.caveat()
    if independence is not None:
        caveats.append(independence)
    for cell in planned_cells(context.manifest):
        # Owned out here rather than inside the run, because a cell that raises
        # part-way has still been paid for and the sweep's spend is the check on
        # the whole result. A cell that built fourteen leaves before tripping
        # the ceiling reported nothing at all while the money was gone.
        units: list[UnitRecord] = []
        try:
            records.append(await _run_cell(context, cell, units))
        except MemoryError, RecursionError:
            raise
        except RecursionDepthSessionCeilingError as exc:
            # Stops the sweep without losing what it has already paid for.
            logger.warning(
                EVALS_RECURSION_SESSION_CEILING,
                spent=context.budget.spent,
                ceiling=context.manifest.max_sessions,
                measured_cells=len(records),
                error=safe_error_description(exc),
            )
            records.append(_unavailable(cell, exc, units))
            caveats.append(_CEILING_CAVEAT)
            break
        except _SYSTEMIC_FAILURES:
            raise
        except Exception as exc:  # noqa: BLE001 -- recorded as an unavailable cell
            records.append(_unavailable(cell, exc, units))
    measured = tuple(record for record in records if record.achieved_depth is not None)
    if not measured:
        msg = (
            "the recursion-depth sweep measured no cells; every run is "
            "unavailable, and a report of those is not a curve"
        )
        raise RecursionDepthNoCellsMeasuredError(msg)
    return RecursionDepthReport(
        provenance=provenance,
        cells=tuple(records),
        by_achieved_depth=curve_by_achieved_depth(measured),
        by_depth_cap=curve_by_depth_cap(measured),
        achieved_depth_histogram=achieved_depth_histogram(measured),
        caveats=tuple(caveats),
    )


def _unavailable(
    cell: SweepCell, exc: Exception, units: Sequence[UnitRecord]
) -> CellRecord:
    """Record one run that could not be measured, with its reason.

    Carries whatever the run had already built. It contributes no claims and
    enters no curve, because ``achieved_depth`` is what a curve is keyed on, but
    its spend is real and belongs in the sweep total.

    Returns:
        The unavailable cell.
    """
    reason = f"{type(exc).__name__}: {safe_error_description(exc)}"
    logger.warning(
        EVALS_RECURSION_CELL_UNAVAILABLE,
        depth_cap=cell.depth_cap,
        arm=cell.arm.value,
        repetition=cell.repetition,
        units_built=len(units),
        cost=sum(unit.cost for unit in units),
        error_type=type(exc).__name__,
        error=safe_error_description(exc),
    )
    return CellRecord(
        depth_cap=cell.depth_cap,
        arm=cell.arm,
        repetition=cell.repetition,
        units=tuple(units),
        unavailable_reason=reason,
    )


async def _run_cell(
    context: SweepContext, cell: SweepCell, units: list[UnitRecord]
) -> CellRecord:
    """Plan, build, assemble and grade one run.

    Args:
        context: Everything the sweep is driven with.
        cell: Which run this is.
        units: Sink the per-unit records are appended to, owned by the caller so
            a run that raises part-way still reports what it had already paid
            for.

    Returns:
        The measured cell.
    """
    root = objective_task(
        context.spec,
        project=EVAL_TASK_PROJECT,
        created_by=str(context.roster.lead.id),
    )
    planned = await context.planner.plan(
        task=root, depth_cap=cell.depth_cap, execution_id=f"{cell.key}-plan"
    )
    units.append(
        UnitRecord(
            unit_id=NotBlankStr(f"{cell.key}-plan"),
            title=NotBlankStr(f"Plan: {context.spec.title}"),
            kind=PLAN,
            depth=0,
            attempts=planned.sessions,
            cost=planned.cost,
        )
    )
    context.budget.spend(planned.sessions)
    assembled = await _build_tree_units(context, cell, root, planned.result, units)
    merged = await run_oracle(
        build_sandbox=context.deps.build_sandbox,
        spec_dir=context.spec_dir,
        tree=assembled.project_dir,
    )
    record = CellRecord(
        depth_cap=cell.depth_cap,
        arm=cell.arm,
        repetition=cell.repetition,
        achieved_depth=planned.result.max_depth_reached,
        units=tuple(units),
        merged_passing=tuple(NotBlankStr(key) for key in sorted(merged.passed)),
    )
    logger.info(
        EVALS_RECURSION_CELL_RECORDED,
        depth_cap=cell.depth_cap,
        arm=cell.arm.value,
        repetition=cell.repetition,
        achieved_depth=record.achieved_depth,
        leaf_count=len(record.leaves),
        merged_passing=len(record.merged_passing),
        cost=record.total_cost,
        sessions=record.total_attempts,
    )
    return record


async def _build_tree_units(
    context: SweepContext,
    cell: SweepCell,
    root: Task,
    tree: DecompositionResult,
    units: list[UnitRecord],
) -> CellWorkspace:
    """Build every leaf and assemble every node, children before their parent.

    Args:
        context: Everything the sweep is driven with.
        cell: Which run this is.
        root: The objective every node hangs off, which is the parent of the
            root node and the only one no level created.
        tree: The decomposition tree.
        units: Sink the per-unit records are appended to, so a run that raises
            partway still reports what it had already paid for.

    Returns:
        The workspace holding the root's assembled tree.
    """
    definitions = unit_definitions(tree)
    parents: dict[str, Task] = {str(root.id): root}
    for node in merge_nodes(tree):
        parents.update({str(task.id): task for task in node.created_tasks})
    reviewer = context.reviewer_for(cell.arm)
    produced: dict[str, CellWorkspace] = {}
    delivered: dict[str, bool] = {}
    for node in merge_nodes(tree):
        pieces: list[MergePiece] = []
        for index, task in enumerate(node.created_tasks):
            key = str(task.id)
            if key not in produced:
                leaf = await _run_one_leaf(
                    context, cell, task=task, definition=definitions[key]
                )
                produced[key] = leaf.workspace
                delivered[key] = leaf.delivered
                units.append(_leaf_record(task, definitions[key], node, leaf))
                context.budget.spend(leaf.attempts)
            pieces.append(
                MergePiece(
                    title=str(task.title),
                    slug=piece_slug(str(task.title), index=index),
                    tree=produced[key].project_dir,
                    delivered=delivered[key],
                )
            )
        parent = parents[node.plan.parent_task_id]
        outcome = await _run_one_merge(
            context,
            cell,
            node=node,
            parent=parent,
            pieces=tuple(pieces),
            reviewer=reviewer,
        )
        produced[str(parent.id)] = outcome.workspace
        delivered[str(parent.id)] = outcome.delivered
        units.append(_merge_record(parent, node, outcome))
        context.budget.spend(outcome.attempts)
    return produced[str(root.id)]


async def _run_one_leaf(
    context: SweepContext,
    cell: SweepCell,
    *,
    task: Task,
    definition: SubtaskDefinition,
) -> LeafOutcome:
    """Build one leaf in its own recreated tree.

    Returns:
        The leaf's outcome.
    """
    owner = _owner_for(context.roster, str(task.id))
    workspace = await asyncio.to_thread(
        unit_workspace,
        cell_key=cell.key,
        unit_key=f"leaf-{task.id}",
        spec_dir=context.spec_dir,
        work_root=context.work_root,
    )
    return await run_leaf(
        context.deps,
        task=leaf_task(task, definition=definition, spec=context.spec, owner=owner),
        owner=owner,
        workspace=workspace,
        execution_id=f"{cell.key}-leaf-{task.id}",
        limits=context.limits,
    )


async def _run_one_merge(
    context: SweepContext,
    cell: SweepCell,
    *,
    node: DecompositionResult,
    parent: Task,
    pieces: tuple[MergePiece, ...],
    reviewer: MergeReviewer,
) -> MergeOutcome:
    """Assemble one node in its own recreated tree.

    Returns:
        The merge's outcome.
    """
    workspace = await asyncio.to_thread(
        unit_workspace,
        cell_key=cell.key,
        unit_key=f"merge-{parent.id}",
        spec_dir=context.spec_dir,
        work_root=context.work_root,
    )
    return await run_merge(
        context.deps,
        MergePlan(
            task=parent,
            owner=context.roster.lead,
            workspace=workspace,
            pieces=pieces,
            criteria=_merge_criteria(context, parent, node),
            execution_prefix=f"{cell.key}-merge-{parent.id}",
            limits=context.limits,
            attempts=context.manifest.merge_attempts,
        ),
        reviewer,
    )


def _merge_criteria(
    context: SweepContext, parent: Task, node: DecompositionResult
) -> tuple[NotBlankStr, ...]:
    """What a node's assembly is judged against.

    The parent's own criteria first, because that is what the whole is FOR;
    then the criteria of the pieces being assembled, which is what "these work
    together" means at this level. A node with neither falls back to the
    specification's own requirement titles, because the review input refuses an
    empty criteria list and a judge given nothing to check against approves on
    impression.

    Returns:
        The criteria.
    """
    own = tuple(
        NotBlankStr(criterion.description) for criterion in parent.acceptance_criteria
    )
    if own:
        return own
    stated = tuple(
        NotBlankStr(criterion)
        for subtask in node.plan.subtasks
        for criterion in subtask.acceptance_criteria
    )
    if stated:
        return stated
    return tuple(
        NotBlankStr(f"{key}: {title}") for key, title in context.spec.titles.items()
    )


def _owner_for(roster: SweepRoster, unit_id: str) -> AgentIdentity:
    """Pick the builder that owns one unit.

    Spread across the roster rather than pinned to one agent, so a plan that
    assigns work to several owners finds several. Derived with a stable digest
    rather than ``hash``, whose string seed is randomised per process, so a
    re-run of the same tree reaches the same owners.

    Returns:
        The owning builder.
    """
    digest = zlib.crc32(unit_id.encode("utf-8"))
    return roster.builders[digest % len(roster.builders)]


def _leaf_record(
    task: Task,
    definition: SubtaskDefinition,
    node: DecompositionResult,
    leaf: LeafOutcome,
) -> UnitRecord:
    """Record what one leaf did.

    Returns:
        The unit record.
    """
    return UnitRecord(
        unit_id=NotBlankStr(str(task.id)),
        title=NotBlankStr(str(task.title)),
        kind=LEAF,
        depth=node.depth,
        claimed=definition.satisfies,
        delivered=leaf.delivered,
        attempts=leaf.attempts,
        turns=leaf.turns,
        cost=leaf.cost,
        tokens=leaf.tokens,
        executor=leaf.executor,
        detail=leaf.detail,
    )


def _merge_record(
    parent: Task, node: DecompositionResult, outcome: MergeOutcome
) -> UnitRecord:
    """Record what one merge did.

    Returns:
        The unit record.
    """
    return UnitRecord(
        unit_id=NotBlankStr(str(parent.id)),
        title=NotBlankStr(f"Assemble: {parent.title}"),
        kind=MERGE,
        depth=node.depth,
        delivered=outcome.delivered,
        attempts=outcome.attempts,
        turns=outcome.turns,
        cost=outcome.cost,
        tokens=outcome.tokens,
        executor=outcome.executor,
        reviewer=outcome.reviewer,
        detail=outcome.detail,
        verdict=NotBlankStr(outcome.verdict) if outcome.verdict is not None else None,
        parked=outcome.parked,
        amendments=outcome.amendments,
    )


__all__ = [
    "SessionBudget",
    "SweepCell",
    "SweepContext",
    "planned_cells",
    "run_sweep",
]
