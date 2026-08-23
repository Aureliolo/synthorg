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

Failures split three ways. A missing provider, a dead gateway or a dead Docker
daemon is true of every remaining run, so it stops the matrix rather than being
rediscovered once per cell at full retry cost, and no report is written. The
account running out of quota also stops the matrix, because it is true of every
remaining cell too, but it keeps what was already paid for: the triggering cell
is recorded, a caveat is added and the report is emitted. Anything else records
that one cell as unavailable with its reason and the sweep continues: the
report is always written, and a cell that cost real money is never dropped from
it.
"""

import asyncio
import zlib
from collections.abc import Mapping, Sequence
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from evals.errors import (
    EvalToolMissingError,
    HarnessDockerUnavailableError,
    HarnessGatewayUnavailableError,
    HarnessJournalUnwritableError,
    HarnessProviderMissingError,
    OracleUnusableError,
    RecursionDepthGateUnbuildableError,
    RecursionDepthNoCellsMeasuredError,
    RecursionDepthPlannerSubstitutedError,
    RecursionDepthSessionCeilingError,
)
from evals.harness.journal import RecordedCells
from evals.harness.workspace import CellWorkspace
from evals.recursion_depth.claims import requirement_ids_of
from evals.recursion_depth.execute import LeafOutcome, leaf_task, run_leaf
from evals.recursion_depth.gate import (
    BlindMergeReviewer,
    MergeReviewer,
    OracleMergeReviewer,
)
from evals.recursion_depth.journal import (
    CellProgress,
    CellUnits,
    cell_key,
    open_cell_journal,
    open_progress_journal,
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
    PlannedTreeRecord,
    Provenance,
    RecursionDepthReport,
    UnitRecord,
)
from evals.recursion_depth.oracle import run_oracle
from evals.recursion_depth.planner import PlanningSpend, TreePlanner
from evals.recursion_depth.score import (
    achieved_depth_histogram,
    curve_by_achieved_depth,
    curve_by_depth_cap,
)
from evals.recursion_depth.session import (
    SessionLimits,
    SweepDeps,
    built_unit_workspace,
    leaf_unit_key,
    merge_unit_key,
    unit_workspace,
)
from evals.recursion_depth.staffing import SweepRoster
from evals.recursion_depth.tree import (
    SpecBrief,
    achieved_levels,
    merge_nodes,
    objective_task,
    unit_definitions,
)
from evals.runner.execution import EVAL_TASK_PROJECT
from synthorg.core.agent import AgentIdentity
from synthorg.core.resilience import GeneralRetryHandler
from synthorg.core.task import Task
from synthorg.core.types import NotBlankStr
from synthorg.engine.decomposition.models import DecompositionResult, SubtaskDefinition
from synthorg.engine.errors import DecompositionError, DecompositionTimeoutError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.evals import (
    EVALS_RECURSION_CELL_CONTINUED,
    EVALS_RECURSION_CELL_RECORDED,
    EVALS_RECURSION_CELL_RESTARTED,
    EVALS_RECURSION_CELL_UNAVAILABLE,
    EVALS_RECURSION_NO_CELLS,
    EVALS_RECURSION_PLAN_RETRIED,
    EVALS_RECURSION_QUOTA_EXHAUSTED,
    EVALS_RECURSION_SESSION_CEILING,
    EVALS_RECURSION_SYSTEMIC_FAILURE,
)
from synthorg.providers.errors import ProviderQuotaExceededError

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
    # Nothing about the roster changes between cells, so a planning session
    # with no owner to run as has none in every cell. Recording it per cell
    # would buy six identical fallback plans at full price.
    RecursionDepthPlannerSubstitutedError,
    # A journal that cannot be written is true of every remaining cell, and
    # recording this one as unavailable would try to write that row to the
    # same broken file. The sweep stops holding whatever already landed.
    HarnessJournalUnwritableError,
)

#: Attempts one cell's tree gets. Two, not more: a planner that cannot produce
#: a tree twice is telling the operator something, and a third attempt buys the
#: same answer more slowly.
_PLAN_ATTEMPTS: Final[int] = 2

#: Seconds before the second planning attempt. Long enough to be past a
#: momentary upstream refusal, short enough not to matter against a cell that
#: runs for hours.
_PLAN_RETRY_BASE_SECONDS: Final[float] = 5.0

#: Ceiling on that wait. With one retry it is never reached; it exists because
#: the retry handler refuses a cap below its base.
_PLAN_RETRY_CAP_SECONDS: Final[float] = 30.0

_CEILING_CAVEAT: str = (
    "The sweep stopped early on its session ceiling, so the depths and "
    "repetitions the manifest asked for are not all present. Read the cell "
    "list, not the manifest, for what was actually measured."
)

_UNRESOLVED_CLAIMS_CAVEAT: str = (
    "{dropped} planner claim(s) named no requirement this specification "
    "defines and were dropped before scoring. A handful is one planner "
    "inventing a requirement; a large share means the criterion template and "
    "the id pattern have drifted apart, which deflates both halves of the "
    "survival ratio and reads on the chart like a gate that does not help."
)

_QUOTA_CAVEAT: str = (
    "The sweep stopped early because the provider account ran out of quota, "
    "so the depths and repetitions the manifest asked for are not all "
    "present, and the cell it stopped on was cut off part-way rather than "
    "measured. Read the cell list, not the manifest, for what was actually "
    "measured, and re-run the remainder once the account's window resets."
)


def _quota_exhaustion(exc: BaseException) -> ProviderQuotaExceededError | None:
    """Find a quota refusal anywhere in *exc*'s cause chain.

    The refusal reaches this layer wrapped: the driver raises it, the
    decomposition service re-raises a ``DecompositionError`` naming the task,
    and only the chain still says what actually happened.

    Returns:
        The quota error, or ``None`` when this failure is not one.
    """
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        if isinstance(current, ProviderQuotaExceededError):
            return current
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return None


def _report_quota_exhaustion(
    exc: BaseException, *, measured: int, remaining: int
) -> bool:
    """Say whether *exc* is the account running dry, and log it when it is.

    Running out of quota is a property of the ACCOUNT, not of the cell that
    happened to ask last, so every remaining cell would refuse in seconds and
    be filed under a cell-shaped reason. One live sweep lost its whole
    remaining matrix in sixteen seconds that way, and its report blamed
    decomposition.

    Returns:
        True when the sweep should stop.
    """
    quota = _quota_exhaustion(exc)
    if quota is None:
        return False
    logger.warning(
        EVALS_RECURSION_QUOTA_EXHAUSTED,
        measured_cells=measured,
        remaining_cells=remaining,
        error=safe_error_description(quota),
    )
    return True


async def _run_and_record(
    context: SweepContext,
    cell: SweepCell,
    records: RecordedCells[CellRecord],
    caveats: list[str],
    *,
    remaining: int,
    units: CellUnits,
    resumed: CellProgress,
) -> bool:
    """Run one planned cell, record it however it ends, and say whether to stop.

    Args:
        context: Everything the sweep is driven with.
        cell: The planned run.
        records: Sink every outcome is recorded to, measured or not, which
            writes each one to the journal as it lands.
        caveats: Sink a stopping reason is appended to.
        remaining: Planned cells after this one, for the stop log.
        units: Sink the per-session records go to, which journals each one as
            it lands. Owned by the caller rather than created here, because a
            cell that raises part-way has still been paid for and the sweep's
            spend is the check on the whole result. A cell that built fourteen
            leaves before tripping the ceiling reported nothing at all while
            the money was gone.
        resumed: What an earlier attempt at this cell got through.

    Returns:
        True when the sweep must not continue.

    Raises:
        MemoryError: Never handled here.
        RecursionError: Never handled here.
    """
    try:
        records.add(await _run_cell(context, cell, units, resumed))
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
        records.add(_unavailable(cell, exc, units.records))
        caveats.append(_CEILING_CAVEAT)
        return True
    except _SYSTEMIC_FAILURES as exc:
        # Logged before it propagates: this ends the whole sweep and writes no
        # report, so an unhandled traceback would be the only surviving record
        # of why a run that had already spent money stopped.
        logger.error(
            EVALS_RECURSION_SYSTEMIC_FAILURE,
            measured_cells=len(records),
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise
    except Exception as exc:  # noqa: BLE001 -- recorded as an unavailable cell
        records.add(_unavailable(cell, exc, units.records))
        if not _report_quota_exhaustion(
            exc, measured=len(records) - 1, remaining=remaining
        ):
            return False
        caveats.append(_QUOTA_CAVEAT)
        return True
    return False


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
        """The name this run's trees, ledgers and journal entry are keyed by.

        Delegated rather than spelled again: a resume matches a journalled cell
        to a planned one by this string, so a second spelling would re-run
        every cell the sweep had already paid for.

        Returns:
            The key.
        """
        return cell_key(self.depth_cap, self.arm, self.repetition)


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
    context: SweepContext,
    *,
    provenance: Provenance,
    out_dir: Path,
    resume: bool,
) -> RecursionDepthReport:
    """Run the whole matrix and assemble the report.

    Every cell is journalled to *out_dir* the moment it finishes, so a sweep
    killed part-way has produced everything it had paid for rather than
    nothing, and *resume* reads those cells back instead of buying them twice.

    Args:
        context: Everything the sweep is driven with.
        provenance: What this recording is measured against.
        out_dir: Where the journal and the report are written.
        resume: Whether an existing journal for this matrix is continued.

    Returns:
        The report, always written, carrying every run that was attempted.

    Raises:
        HarnessJournalMismatchError: A journal exists that this sweep must not
            append to.
        RecursionDepthSessionCeilingError: The sessions previous attempts
            already spent are past the manifest ceiling, so this resume has
            nothing left to buy. Raised before any cell runs, unlike the same
            error inside the loop, which stops the sweep and keeps its report.
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
    # Seeded, not accumulated: these two hold for every sweep this harness can
    # run, and a report that states them only when something went wrong states
    # them in exactly the runs nobody reads closely.
    caveats: list[str] = [SIZING_CAVEAT, ORACLE_CAVEAT]
    independence = context.manifest.caveat()
    if independence is not None:
        caveats.append(independence)
    # Every handle registered the moment it is opened, because the two things
    # that happen between the opens and the loop can both raise: the second
    # open refuses a journal this sweep must not append to, and the re-booking
    # below trips the ceiling on its own. A `finally` around the loop alone
    # leaves whichever handle was already open on either exit.
    with ExitStack() as stack:
        records, resumed = open_cell_journal(
            out_dir, provenance=provenance, resume=resume
        )
        stack.callback(records.close)
        sessions, progress = open_progress_journal(
            out_dir, provenance=provenance, resume=resume
        )
        stack.callback(sessions.close)
        # Re-booked before anything runs, so a sweep resumed four times is
        # bounded like one sweep rather than like each of its attempts. Read
        # off the session rows, which is where every session appears once.
        context.budget.spend(progress.sessions_spent)
        planned = tuple(planned_cells(context.manifest))
        for index, cell in enumerate(planned):
            already = resumed.holds(cell.key)
            if already is not None:
                records.replay(already)
                continue
            if await _run_and_record(
                context,
                cell,
                records,
                caveats,
                remaining=len(planned) - index - 1,
                units=CellUnits(
                    sessions,
                    depth_cap=cell.depth_cap,
                    arm=cell.arm,
                    repetition=cell.repetition,
                ),
                resumed=progress.holds(cell.key),
            ):
                break
        cells = records.cells
    measured = tuple(record for record in cells if record.achieved_depth is not None)
    if not measured:
        msg = (
            "the recursion-depth sweep measured no cells; every run is "
            "unavailable, and a report of those is not a curve"
        )
        logger.warning(
            EVALS_RECURSION_NO_CELLS,
            planned_cells=len(planned),
            recorded_cells=len(records),
        )
        raise RecursionDepthNoCellsMeasuredError(msg)
    dropped = sum(unit.unresolved_claims for cell in cells for unit in cell.units)
    if dropped:
        caveats.append(_UNRESOLVED_CLAIMS_CAVEAT.format(dropped=dropped))
    return RecursionDepthReport(
        provenance=provenance,
        cells=cells,
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
    # `safe_error_description` already leads with the exception type.
    reason = safe_error_description(exc)
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


async def _plan_with_retry(
    context: SweepContext, cell: SweepCell, root: Task, spend: PlanningSpend
) -> DecompositionResult:
    """Produce *cell*'s tree, re-asking once when the planner call fails.

    Planning is one call, it happens before anything else, and losing it loses
    the entire cell: a live run had three of its four cells discarded by this
    exact failure, on the same task, while a fourth cell planned the identical
    tree successfully. That asymmetry is what a flaky call looks like, and one
    of them must not cost a matrix position.

    Retried at the sweep's level rather than inside the planner, because the
    fact that makes a retry worth paying for is not visible there: a
    decomposition that fails inside the product is one task, and here it is a
    cell of the experiment. Bounded at two attempts, because a planner that
    cannot produce a tree twice is telling the operator something, and a
    third attempt buys the same answer more slowly.

    A wall-clock timeout is the one decomposition failure NOT retried, because
    the ceiling is unchanged on the next attempt and that ceiling is the whole
    price: retrying it pays for the same outcome twice over, and the ceilings a
    sweep arms are large enough that the second attempt is measured in hours.
    Everything else the planner raises is worth another roll.

    Args:
        context: Everything the sweep is driven with.
        cell: Which run this is.
        root: The objective being decomposed.
        spend: Where every attempt's spend accumulates, the failed ones
            included: a discarded attempt is money that left the account.

    Returns:
        The tree.
    """
    retry = GeneralRetryHandler(
        retryable=lambda exc: (
            isinstance(exc, DecompositionError)
            and not isinstance(exc, DecompositionTimeoutError)
        ),
        max_attempts=_PLAN_ATTEMPTS,
        base=_PLAN_RETRY_BASE_SECONDS,
        cap=_PLAN_RETRY_CAP_SECONDS,
        event=EVALS_RECURSION_PLAN_RETRIED,
    )
    return await retry.execute(
        lambda: context.planner.plan(
            task=root,
            depth_cap=cell.depth_cap,
            execution_id=f"{cell.key}-plan",
            spend=spend,
        ),
        cell=cell.key,
    )


@dataclass(frozen=True)
class _ContinuedCell:
    """What an earlier attempt at a cell hands the one continuing it.

    Attributes:
        root: The objective its tree hangs off, read back rather than re-minted
            because every ``parent_task_id`` in the tree names it by id.
        tree: The decomposition the earlier attempt was building against.
        produced: Each already-built unit id mapped to the tree it left on disk.
        delivered: Each already-built unit id mapped to whether it delivered.
    """

    root: Task
    tree: DecompositionResult
    produced: dict[str, CellWorkspace]
    delivered: dict[str, bool]


def _continue_cell(
    context: SweepContext, cell: SweepCell, resumed: CellProgress, units: CellUnits
) -> _ContinuedCell | None:
    """Take up an earlier attempt at *cell*, or say it must be run whole.

    Continuing needs BOTH halves and they can be lost separately: the tree, so
    the units on disk belong to something, and every one of those trees, so a
    merge that reads them assembles what was actually built. A tree without its
    plan is a set of directories nothing indexes; a plan without its trees is a
    walk that would hand a merge empty directories and record the assembly as
    delivered nothing. Either missing means the cell starts again, which costs
    what it costs and is the only answer that cannot report a lie.

    Args:
        context: Everything the sweep is driven with.
        cell: Which run this is.
        resumed: What the earlier attempt got through.
        units: Sink the replayed records go to. Mutated only once the whole
            attempt is known to be usable.

    Returns:
        What to continue from, or ``None`` to run the cell whole.
    """
    if resumed.plan is None:
        return None
    produced: dict[str, CellWorkspace] = {}
    delivered: dict[str, bool] = {}
    for unit in resumed.units:
        if unit.kind == PLAN:
            continue
        key = str(unit.unit_id)
        unit_key = leaf_unit_key(key) if unit.kind == LEAF else merge_unit_key(key)
        workspace = built_unit_workspace(
            cell_key=cell.key, unit_key=unit_key, work_root=context.work_root
        )
        if workspace is None:
            logger.warning(
                EVALS_RECURSION_CELL_RESTARTED,
                cell=cell.key,
                recorded_units=len(resumed.units),
                missing_unit=key,
            )
            return None
        produced[key] = workspace
        delivered[key] = unit.delivered
    for unit in resumed.units:
        units.replay(unit)
    logger.info(
        EVALS_RECURSION_CELL_CONTINUED,
        cell=cell.key,
        replayed_units=len(resumed.units),
        replayed_sessions=sum(unit.attempts for unit in resumed.units),
    )
    return _ContinuedCell(
        root=resumed.plan.root,
        tree=resumed.plan.result,
        produced=produced,
        delivered=delivered,
    )


def _plan_unit(
    context: SweepContext, cell: SweepCell, spend: PlanningSpend, *, detail: str = ""
) -> UnitRecord:
    """Record what *cell*'s planning ran and what it cost.

    Args:
        context: Everything the sweep is driven with.
        cell: Which run this is.
        spend: What the planning attempts booked.
        detail: Why planning produced no tree, empty when it produced one.

    Returns:
        The planning session's record.
    """
    return UnitRecord(
        unit_id=NotBlankStr(f"{cell.key}-plan"),
        title=NotBlankStr(f"Plan: {context.spec.title}"),
        kind=PLAN,
        depth=0,
        attempts=spend.sessions,
        cost=spend.cost,
        tokens=spend.tokens,
        detail=detail,
    )


async def _plan_cell(
    context: SweepContext, cell: SweepCell, units: CellUnits
) -> _ContinuedCell:
    """Mint *cell*'s objective and tree, journalling both before anything runs.

    The tree is written down with its objective rather than after the run,
    because it is what every unit on disk belongs to: a cell killed at hour six
    can only be continued by whoever holds the tree it was building against.

    The planning session is journalled whether or not it produced that tree,
    because the sessions it ran are real spend either way. Recorded only on
    success, a cell whose planning failed reported zero attempts, zero cost and
    zero tokens: two live cells spent an hour of provider time between them and
    the report said the run had been free.

    Args:
        context: Everything the sweep is driven with.
        cell: Which run this is.
        units: Sink the planning record goes to.

    Returns:
        The freshly planned tree, with nothing built yet.
    """
    root = objective_task(
        context.spec,
        project=EVAL_TASK_PROJECT,
        created_by=str(context.roster.lead.id),
    )
    spend = PlanningSpend()
    try:
        tree = await _plan_with_retry(context, cell, root, spend)
    except Exception as exc:
        # No tree, so no plan row: a journalled plan is what a resume takes the
        # cell up from, and this attempt left nothing to take up.
        # `safe_error_description` already leads with the exception type, so
        # naming it again would spend half the field's cap repeating itself.
        units.append(
            _plan_unit(context, cell, spend, detail=safe_error_description(exc))
        )
        _book_planning_budget(context, spend, failure=exc)
        raise
    units.append(
        _plan_unit(context, cell, spend),
        plan=PlannedTreeRecord(root=root, result=tree),
    )
    _book_planning_budget(context, spend, failure=None)
    return _ContinuedCell(root=root, tree=tree, produced={}, delivered={})


def _book_planning_budget(
    context: SweepContext, spend: PlanningSpend, *, failure: Exception | None
) -> None:
    """Book the planning sessions against the sweep's ceiling.

    On the failure path the ceiling error must not become what propagates.
    ``_run_and_record`` classifies by exception TYPE, and the two verdicts it
    reaches are opposites: a ceiling breach stops the sweep and still writes
    the report, while a systemic failure writes none at all. A planning failure
    that happens to be the booking which crosses the ceiling would be filed as
    the first, so an unreachable gateway or an unwritable journal would be
    reported to the operator as "raise max_sessions" and, for the journal case,
    answered by writing to the journal already known to be broken.

    Args:
        context: Everything the sweep is driven with.
        spend: What the planning attempts booked.
        failure: The planning failure already propagating, or ``None`` when
            planning produced a tree and the ceiling may stop the sweep here.

    Raises:
        RecursionDepthSessionCeilingError: The sweep has spent its budget, and
            no other failure is already on its way out.
    """
    try:
        context.budget.spend(spend.sessions)
    except RecursionDepthSessionCeilingError:
        if failure is None:
            raise
        # Logged rather than raised: the breach is real and the sweep will
        # reach it again on the next booking, but the failure already
        # propagating is the one the operator needs to read.
        logger.warning(
            EVALS_RECURSION_SESSION_CEILING,
            spent=context.budget.spent,
            ceiling=context.manifest.max_sessions,
            error_type=type(failure).__name__,
            error=safe_error_description(failure),
        )


async def _run_cell(
    context: SweepContext,
    cell: SweepCell,
    units: CellUnits,
    resumed: CellProgress,
) -> CellRecord:
    """Plan, build, assemble and grade one run.

    Args:
        context: Everything the sweep is driven with.
        cell: Which run this is.
        units: Sink the per-unit records are appended to, owned by the caller so
            a run that raises part-way still reports what it had already paid
            for, and journalling each one as it lands.
        resumed: What an earlier attempt at this cell got through.

    Returns:
        The measured cell.
    """
    started = _continue_cell(context, cell, resumed, units) or await _plan_cell(
        context, cell, units
    )
    assembled = await _build_tree_units(
        context,
        cell,
        started.root,
        started.tree,
        units,
        produced=started.produced,
        delivered=started.delivered,
    )
    merged = await run_oracle(
        build_sandbox=context.deps.build_sandbox,
        spec_dir=context.spec_dir,
        tree=assembled.project_dir,
    )
    record = CellRecord(
        depth_cap=cell.depth_cap,
        arm=cell.arm,
        repetition=cell.repetition,
        achieved_depth=achieved_levels(started.tree),
        units=units.records,
        merged_passing=tuple(sorted(merged.passed)),
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
    units: CellUnits,
    *,
    produced: dict[str, CellWorkspace],
    delivered: dict[str, bool],
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
        produced: Each already-built unit id mapped to its tree, empty on a
            fresh run and pre-filled by whatever an earlier attempt left.
            Mutated.
        delivered: The same, for whether each one delivered. Mutated.

    Returns:
        The workspace holding the root's assembled tree.
    """
    definitions = unit_definitions(tree)
    parents: dict[str, Task] = {str(root.id): root}
    for node in merge_nodes(tree):
        parents.update({str(task.id): task for task in node.created_tasks})
    reviewer = context.reviewer_for(cell.arm)
    for node in merge_nodes(tree):
        parent = parents[node.plan.parent_task_id]
        if str(parent.id) in produced:
            # An earlier attempt assembled this node, and the walk is
            # children-first, so everything under it is on disk too. Re-running
            # it would pay for the same assembly and then discard whichever
            # copy lost.
            continue
        pieces = await _leaf_pieces(
            context,
            cell,
            node,
            definitions=definitions,
            produced=produced,
            delivered=delivered,
            units=units,
        )
        outcome = await _run_one_merge(
            context,
            cell,
            node=node,
            parent=parent,
            pieces=pieces,
            reviewer=reviewer,
        )
        produced[str(parent.id)] = outcome.workspace
        delivered[str(parent.id)] = outcome.delivered
        units.append(_merge_record(parent, node, outcome))
        context.budget.spend(outcome.attempts)
    return produced[str(root.id)]


async def _leaf_pieces(
    context: SweepContext,
    cell: SweepCell,
    node: DecompositionResult,
    *,
    definitions: Mapping[str, SubtaskDefinition],
    produced: dict[str, CellWorkspace],
    delivered: dict[str, bool],
    units: CellUnits,
) -> tuple[MergePiece, ...]:
    """Build each of *node*'s children, and name what the merge assembles.

    A child already in *produced* is a node this walk assembled at a lower
    level, so it is taken as it stands rather than rebuilt: the tree is walked
    children-first and re-running one would pay for the same work twice and
    then discard whichever copy lost.

    Args:
        context: Everything the sweep is driven with.
        cell: Which run this is.
        node: The level whose children are being built.
        definitions: Every task id mapped to the planner definition it came
            from, which is where a unit's claims live.
        produced: Each built id mapped to its tree. Mutated.
        delivered: Each built id mapped to whether it delivered. Mutated.
        units: Sink the per-unit records are appended to. Mutated, so a run
            that raises partway still reports what it had already paid for.

    Returns:
        One piece per child, in the order the level declares them.
    """
    pieces: list[MergePiece] = []
    for index, task in enumerate(node.created_tasks):
        key = str(task.id)
        if key not in produced:
            leaf = await _run_one_leaf(
                context, cell, task=task, definition=definitions[key]
            )
            produced[key] = leaf.workspace
            delivered[key] = leaf.delivered
            units.append(_leaf_record(task, definitions[key], node, leaf, context.spec))
            context.budget.spend(leaf.attempts)
        pieces.append(
            MergePiece(
                title=str(task.title),
                slug=piece_slug(str(task.title), index=index),
                tree=produced[key].project_dir,
                delivered=delivered[key],
            )
        )
    return tuple(pieces)


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
        unit_key=leaf_unit_key(str(task.id)),
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
        unit_key=merge_unit_key(str(parent.id)),
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
    spec: SpecBrief,
) -> UnitRecord:
    """Record what one leaf did.

    The planner's ``satisfies`` carries criterion TEXT, and every consumer of
    this record wants the requirement id, so the translation happens here,
    once, rather than in each of them. What did NOT translate is counted onto
    the record rather than only warned about, because the survival metric is a
    ratio over what does.

    Returns:
        The unit record.
    """
    resolved = requirement_ids_of(
        definition.satisfies,
        known=spec.requirement_ids,
        unit=str(task.title),
    )
    return UnitRecord(
        unit_id=NotBlankStr(str(task.id)),
        title=NotBlankStr(str(task.title)),
        kind=LEAF,
        depth=node.depth,
        claimed=resolved.ids,
        unresolved_claims=resolved.unresolved,
        delivered=leaf.delivered,
        attempts=leaf.attempts,
        turns=leaf.turns,
        cost=leaf.cost,
        tokens=leaf.tokens,
        executor=leaf.executor,
        detail=leaf.detail,
        undeclared_paths=leaf.undeclared_paths,
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
        undeclared_paths=outcome.undeclared_paths,
    )


__all__ = [
    "SessionBudget",
    "SweepCell",
    "SweepContext",
    "planned_cells",
    "run_sweep",
]
