# module-kind: orchestrator
"""Drive every registered loop over every brief and assemble the scoreboard.

One cell is a ``(loop, tier, brief, repetition)``. Each cell gets a workspace
recreated from the brief's seed fixture, an engine built around that one loop,
and a fresh cost tracker so the run's spend is attributable to it alone. The
loop then does the work with its own tools, and the brief's checks grade
whatever it actually left on disk.

Dependencies arrive through :class:`LoopAbDeps` rather than being constructed
here, so the same orchestration runs against a real provider at record time and
against scripted doubles in tests. That is also what keeps the OpenHands leg
honest: when its runtime is not wired, the cell is recorded as unavailable with
the reason instead of being dropped from the comparison.
"""

import asyncio
import contextlib
import shutil
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import date
from functools import partial
from pathlib import Path
from typing import Final
from uuid import UUID

from evals.errors import (
    BriefExecutionError,
    EvalToolMissingError,
    LoopAbDockerUnavailableError,
    LoopAbGatewayUnavailableError,
    LoopAbProviderMissingError,
)
from evals.loop_ab.aggregate import (
    LoopRepetitionSummary,
    RepetitionOutcome,
    summarise_repetitions,
)
from evals.loop_ab.manifest import LoopAbManifest, TierEntry
from evals.loop_ab.models import (
    LoopBriefRow,
    Provenance,
    ProviderSpend,
    RubricWeights,
    Scoreboard,
)
from evals.loop_ab.promotion import recommend_promotion
from evals.loop_ab.rollup import rollup_by_complexity
from evals.loop_ab.rubric import LoopCellScore, score_cell
from evals.loop_ab.stall_watch import (
    DEFAULT_STALL_IDLE_SECONDS,
    ProgressTrackingLedger,
    StallWatch,
)
from evals.loop_ab.transcript import TranscriptRecorder
from evals.loop_ab.workspace import CellWorkspace, seed_workspace
from evals.models.brief import Brief
from evals.prompt_layers import bind_default_prompt_layers
from evals.runner.execution import expected_artifacts_of, run_brief
from evals.runner.interpreter import resolve_checks
from evals.scoring.executable import grade_executable
from synthorg.budget.cost_record import CostRecord
from synthorg.budget.tracker import CostTracker
from synthorg.budget.tracker_protocol import collect_all_records
from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.types import NotBlankStr
from synthorg.engine.agent_engine import AgentEngine
from synthorg.engine.artifacts.expected_artifact_check import (
    ArtifactPresence,
    missing_expected_artifacts,
    workspace_artifact_probe,
)
from synthorg.engine.loop_selector import build_execution_loop
from synthorg.engine.openhands.config import OpenHandsLoopConfig, OpenHandsLoopDeps
from synthorg.engine.recovery import FailAndReassignStrategy
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.evals import (
    EVALS_LOOP_AB_CELL_PARTIAL,
    EVALS_LOOP_AB_EVIDENCE_KEEP_FAILED,
    EVALS_LOOP_AB_LOOP_UNAVAILABLE,
    EVALS_LOOP_AB_RUN_RECORDED,
)
from synthorg.persistence.project_protocol import ProjectRepository
from synthorg.providers.protocol import CompletionProvider
from synthorg.tools.registry import ToolRegistry

logger = get_logger(__name__)

#: The one agent every cell runs as, fixed so runs stay comparable and no run
#: inherits another's per-agent state. Exported because the gateway-side bearer
#: has to name the same actor the engine does, and two literals kept level by a
#: comment is how cost attribution drifts apart.
AB_AGENT_ID: Final[UUID] = UUID("00000000-0000-4000-8000-00000000ab00")

#: The loop's execution is the unit under test, so the agent is a plain
#: developer with no role-specific prompt shaping to advantage one loop.
_AB_AGENT_ROLE: str = "Developer"
_AB_AGENT_DEPARTMENT: str = "Engineering"
_AB_HIRING_DATE: date = date(2026, 1, 1)

#: The one loop whose runtime the harness constructs per cell.
_OPENHANDS_LOOP: Final[str] = "openhands"

#: Names never copied into a cell's evidence bundle. ``.openhands`` is the
#: SDK's conversation state, written inside the workspace so a resumed run
#: re-attaches, and built from the run's gateway bearer.
_EVIDENCE_EXCLUDED: Final[tuple[str, ...]] = (".openhands",)

#: Failures that are true of the machine or the configuration rather than of
#: the loop under test, so no other cell would survive them either. Recording
#: them per cell would spend the rest of a matrix rediscovering one fact and
#: then attribute it to whichever loop happened to hit it.
_SYSTEMIC_FAILURES: Final[tuple[type[Exception], ...]] = (
    LoopAbProviderMissingError,
    LoopAbGatewayUnavailableError,
    LoopAbDockerUnavailableError,
    # A grading command absent from PATH is a property of the machine: every
    # remaining cell would run, spend, and then fail to be graded, and each
    # would report it as though the loop were the thing that was unavailable.
    # The preflight refuses this before anything is spent; this is the backstop
    # for a tool that disappears mid-matrix.
    EvalToolMissingError,
)


@dataclass(frozen=True)
class _CellCoordinates:
    """The ``(loop, tier, brief)`` a repetition runs at.

    Bundled so the per-cell coordinate cluster travels as one value rather than
    three parallel parameters threaded through every run helper.
    """

    loop_type: NotBlankStr
    tier: TierEntry
    brief: Brief


@dataclass(frozen=True)
class CellRun:
    """One repetition, as the collaborators that bind it need to see it.

    Everything a collaborator binds is per repetition, not per tier: the
    gateway bearer binds the run, and the sandbox binds the workspace this run
    was given, which the next repetition will have recreated.

    Attributes:
        loop_type: The loop under test in this cell.
        tier: The explicitly bound ``(provider, model_id)`` pair.
        brief: The brief being run, carrying the limits a bearer's ceiling and
            the loop's turn cap come from.
        repetition: Zero-based index within the cell.
        workspace: The recreated workspace this repetition runs against.
    """

    loop_type: NotBlankStr
    tier: TierEntry
    brief: Brief
    repetition: int
    workspace: CellWorkspace


ProviderFactory = Callable[[CellRun], Awaitable[CompletionProvider]]
ToolRegistryFactory = Callable[[CellWorkspace], ToolRegistry | None]
OpenHandsCellFactory = Callable[
    [CellRun], Awaitable[tuple[OpenHandsLoopConfig, OpenHandsLoopDeps]]
]
CellLedgerFactory = Callable[
    [CellRun], AbstractAsyncContextManager[ProgressTrackingLedger]
]
#: Releases whatever the tool registry holds open once a repetition is over.
#: A reusing sandbox lifecycle keeps its container until something releases it,
#: on a timer owned by the strategy object the repetition is about to discard.
ToolReleaseHook = Callable[[], Awaitable[None]]
#: Called with ``(cell_label, idle_seconds)`` when a cell is reported stalled.
StallReporter = Callable[[str, float], None]


@dataclass(frozen=True)
class LoopAbDeps:
    """Runtime collaborators the matrix is driven with.

    Attributes:
        build_provider: Builds the completion provider for one repetition. At
            record time this returns a driver pointed at the LLM gateway and
            carrying that run's bearer, so every loop's spend lands in the same
            authoritative ledger rather than being re-derived per loop.
        build_tool_registry: Builds the tool registry scoped to a run's
            workspace, giving the native loops their file and shell tools.
        release_tools: Releases what that registry holds open, run after every
            repetition whether it finished or raised. The deployment's sandbox
            lifecycle reuses one container per owner and destroys it on a grace
            timer the strategy object owns, so a repetition that discards the
            strategy without releasing leaves the container to nobody.
        build_openhands_cell: Builds the OpenHands loop's config and runtime
            deps for one repetition. ``None`` records that loop as unavailable
            rather than skipping it.
        open_cell_ledger: Installs the authoritative cost sink for one
            repetition and yields it. ``None`` means no gateway is hosted, so
            the engine's own tracker is the ledger; that is the offline path
            the regression suite drives.
        project_repo: Where the engine looks the benchmark project up. A brief
            that expects artifacts is a work task, and the engine refuses to run
            one against a project it cannot validate, so this is required for a
            workspace-graded matrix rather than optional decoration. It is the
            same repository the recording host serves from, seeded with
            :func:`~evals.runner.execution.eval_project`.
        stall_idle_seconds: Idle time after which a cell is reported as
            stalled. A report, never a stop: see
            :mod:`evals.loop_ab.stall_watch`.
        on_stall: Second channel for a stall report, alongside the warning the
            watch always logs. A real recording runs for hours in a terminal,
            and the operator watching it is who the report is for.

    The optional factories are independent, not a paired mode: the suite
    exercises each one set while the others are ``None``, because what they
    answer (can this loop's runtime be built, and whose ledger is authoritative)
    are separate questions. Folding them into one flag would assert a
    correlation nothing here has.
    """

    build_provider: ProviderFactory
    build_tool_registry: ToolRegistryFactory
    release_tools: ToolReleaseHook | None = None
    transcripts: TranscriptRecorder | None = None
    build_openhands_cell: OpenHandsCellFactory | None = None
    open_cell_ledger: CellLedgerFactory | None = None
    project_repo: ProjectRepository | None = None
    stall_idle_seconds: float = DEFAULT_STALL_IDLE_SECONDS
    on_stall: StallReporter | None = None


def _identity(tier: TierEntry) -> AgentIdentity:
    """Build the A/B agent bound to *tier*'s explicit provider and model.

    Returns:
        The agent identity for this tier.
    """
    return AgentIdentity(
        id=AB_AGENT_ID,
        name="Loop A/B Agent",
        role=_AB_AGENT_ROLE,
        department=_AB_AGENT_DEPARTMENT,
        model=ModelConfig(provider=tier.provider, model_id=tier.model_id),
        hiring_date=_AB_HIRING_DATE,
    )


def _spend_from_records(records: tuple[CostRecord, ...]) -> tuple[ProviderSpend, ...]:
    """Fold a run's cost records into per-``(provider, model)`` spend.

    Returns:
        One :class:`ProviderSpend` per distinct provider and model.
    """
    totals: dict[tuple[str, str, str], tuple[int, int, float]] = {}
    for record in records:
        key = (record.provider, record.model, str(record.currency))
        seen_in, seen_out, seen_cost = totals.get(key, (0, 0, 0.0))
        totals[key] = (
            seen_in + record.input_tokens,
            seen_out + record.output_tokens,
            seen_cost + record.cost,
        )
    return tuple(
        ProviderSpend(
            provider=NotBlankStr(provider),
            model_id=NotBlankStr(model),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost=cost,
            # The ledger already validated this as a CurrencyCode; pass it
            # through rather than downcasting to a bare string and discarding
            # the ISO-4217 guarantee.
            currency=currency,
        )
        for (provider, model, currency), (
            input_tokens,
            output_tokens,
            cost,
        ) in sorted(totals.items())
    )


def _artifact_state(brief: Brief, workspace: CellWorkspace) -> ArtifactPresence:
    """What the graded tree currently says about the brief's declarations.

    Returns:
        The presence answer, digests included, for comparison across a run.
    """
    return missing_expected_artifacts(
        expected_artifacts_of(brief), workspace=workspace.project_dir
    )


def _artifacts_produced(
    brief: Brief, workspace: CellWorkspace, as_seeded: ArtifactPresence
) -> bool:
    """Whether the run left the brief's declared files different from its seed.

    Read off disk rather than from the loop's own account of itself. A loop
    reports the tools it called, which is what the NO_OP rule watches; whether
    those calls changed the declared file is a different question, and the
    workspace is the only place that answers it.

    Existence alone would answer it wrongly for most of this suite. A bugfix
    brief declares the file it asks to have fixed, and the seed contains it, so
    "the declared path exists" is true before the agent starts and every run
    would report as having produced it.

    A brief declaring no artifacts is vacuously satisfied: there was nothing to
    produce, so reporting it as a failure to produce would put a rate on a
    question nobody asked.

    Returns:
        Whether any declared artifact was created, changed or removed.
    """
    return not _artifact_state(brief, workspace).delivered_nothing_since(as_seeded)


def _resolved(brief: Brief) -> Brief:
    """Return *brief* with its check commands' interpreter token resolved.

    Returns:
        The brief ready for :func:`grade_executable`.

    Raises:
        BriefExecutionError: The brief declares no checks. The A/B grades a
            workspace by running commands against it, so a brief without them
            cannot be scored; failing here beats grading every loop at zero and
            publishing that as a measurement.
    """
    if brief.checks is None:
        msg = (
            f"brief {brief.brief_id!r} declares no checks block; the loop A/B "
            "grades workspaces by running an executable brief's checks"
        )
        raise BriefExecutionError(msg)
    return brief.model_copy(update={"checks": resolve_checks(brief.checks)})


async def _build_engine(
    *,
    cell: CellRun,
    deps: LoopAbDeps,
    cost_tracker: CostTracker,
) -> AgentEngine:
    """Build an engine running exactly *cell*'s loop against its workspace.

    Returns:
        The configured :class:`AgentEngine`.

    Raises:
        LoopAbOpenHandsUnwiredError: The loop is openhands and its runtime deps
            are not wired.
    """
    openhands_config, openhands_deps = await _openhands_cell(cell, deps)
    execution_loop = build_execution_loop(
        cell.loop_type,
        openhands_loop_config=openhands_config,
        openhands_loop_deps=openhands_deps,
    )
    # No API lifespan runs here, so the ambient prompt layers the product binds
    # at boot have to be bound explicitly or the A/B compares a prompt the
    # product never sends.
    bind_default_prompt_layers()
    return AgentEngine(
        provider=await deps.build_provider(cell),
        execution_loop=execution_loop,
        tool_registry=deps.build_tool_registry(cell.workspace),
        cost_tracker=cost_tracker,
        # The brief's expected artifacts make every cell a work task, and a work
        # task naming a project the engine cannot look up is refused before the
        # loop runs. Passing the repository is what makes the A/B run its tasks
        # under the same preconditions production does, which is the whole basis
        # for reading a promotion decision off the result.
        project_repo=deps.project_repo,
        # The same post-execution check the deployment runs. Both guards ahead
        # of it ask whether the run called *any* tool, so a loop that calls one
        # and then answers in prose passes them having delivered nothing; only
        # this one asks the workspace. Unwired, ``task_sync`` cannot ask, and
        # such a run is recorded as a clean ``completed``: the A/B would then be
        # measuring loops under weaker checks than the deployment it advises.
        # Bound to the cell root, which the probe resolves the project subtree
        # beneath exactly as both sandboxes do.
        artifact_probe=workspace_artifact_probe(cell.workspace.root),
        recovery_strategy=FailAndReassignStrategy(),
    )


async def _openhands_cell(
    cell: CellRun, deps: LoopAbDeps
) -> tuple[OpenHandsLoopConfig | None, OpenHandsLoopDeps | None]:
    """Build the OpenHands loop's config and deps, for the cell that needs them.

    The config travels with the deps rather than being left to default: it
    carries the live bearer TTL, and a loop minting against the frozen default
    could outlive its token on a long cell.

    Returns:
        The ``(config, deps)`` pair, both ``None`` off the OpenHands leg.
    """
    if cell.loop_type != _OPENHANDS_LOOP or deps.build_openhands_cell is None:
        return None, None
    return await deps.build_openhands_cell(cell)


def _cell_label(cell: CellRun) -> str:
    """Name one repetition, for a report an operator has to act on.

    Returns:
        A label identifying the cell and repetition.
    """
    return f"{cell.loop_type}/{cell.tier.tier}/{cell.brief.brief_id}#{cell.repetition}"


def _forward_stall(
    reporter: StallReporter | None, cell_label: str, idle_seconds: float
) -> None:
    """Hand a stall to the caller's reporter, if it wants one.

    The watch always writes its warning, which is the durable record. This is
    the second channel: a matrix runs for hours in a terminal, and a line in a
    structured log nobody is tailing is not a notification.
    """
    if reporter is not None:
        reporter(cell_label, idle_seconds)


def _cell_ledger(
    cell: CellRun, deps: LoopAbDeps, fallback: ProgressTrackingLedger
) -> AbstractAsyncContextManager[ProgressTrackingLedger]:
    """Open the cost sink whose records are this run's authoritative spend.

    With a hosted gateway the ledger is the gateway's, not the engine's: it is
    the only place the OpenHands leg's spend is recorded at all (its calls
    happen inside the container), and reading it rather than the engine's own
    tracker is also what stops a native leg being counted twice, once by its
    driver and once by the gateway it dialled.

    Returns:
        A context manager yielding the tracker to collect records from.
    """
    if deps.open_cell_ledger is None:
        return contextlib.nullcontext(fallback)
    return deps.open_cell_ledger(cell)


def cell_evidence_dir(work_root: Path, cell: CellRun) -> Path:
    """Where one repetition's produced tree and transcript are kept.

    Keyed by repetition because the seeded workspace is not: ``seed_workspace``
    names the tree after the brief and removes it before each run, so the three
    repetitions of a cell share one directory and only the last survives.
    Comparing what each run produced needs each tree to still exist. The tier
    and the loop are already segments of *work_root*, so they are not repeated.

    Numbered from zero, matching every ``repetition`` field the run logs, so an
    operator reading a warning for ``repetition=0`` finds ``rep0`` rather than
    a directory one along.

    Returns:
        The per-repetition evidence directory.
    """
    return work_root / "evidence" / cell.brief.brief_id / f"rep{cell.repetition}"


def _keep_produced_tree(cell: CellRun, work_root: Path) -> None:
    """Copy what this repetition produced somewhere the next one cannot erase.

    Best-effort: a missing tree means the run produced nothing, which the
    artifact rate already records, and a copy failure must not fail a cell that
    was otherwise measured.

    The harness's own conversation state is left behind. The OpenHands SDK
    persists it inside the workspace so a resumed run re-attaches, and it was
    constructed with the run's gateway bearer; what that state serialises is
    the SDK's business, while what becomes a shareable evidence bundle is this
    function's, and evidence has no use for it either way.
    """
    source = cell.workspace.project_dir
    if not source.is_dir():
        return
    destination = cell_evidence_dir(work_root, cell) / "workspace"
    try:
        shutil.copytree(
            source,
            destination,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns(*_EVIDENCE_EXCLUDED),
        )
    except OSError as exc:
        logger.warning(
            EVALS_LOOP_AB_EVIDENCE_KEEP_FAILED,
            brief_id=cell.brief.brief_id,
            tier=cell.tier.tier,
            loop_type=cell.loop_type,
            repetition=cell.repetition,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )


@contextlib.asynccontextmanager
async def _released_tools(
    deps: LoopAbDeps, transcript_path: Path
) -> AsyncIterator[None]:
    """Release what this repetition's tools hold open, however it ends.

    The deployment's sandbox lifecycle reuses one container per owner and
    destroys it on a grace timer the strategy object owns. Every repetition
    builds a fresh registry and discards it, so without this the container is
    left to a timer whose owner is unreachable, once per run.

    The transcript is bound here rather than by the caller so the bind and the
    unbind are one construct: bound outside, anything that raised between the
    two would leave the recorder writing this repetition's path for the next
    one. The unbind and the release are then nested rather than sequential,
    because sharing one ``finally`` lets an unbind failure strand every
    container the release was there to reclaim.

    Args:
        deps: The recorder's injected collaborators.
        transcript_path: Where this repetition's transcript is written.

    Yields:
        Nothing; the release runs on the way out.
    """
    try:
        # Inside the guard, not ahead of it: binding creates the transcript's
        # parent directory, so it can fail, and a failure before the try would
        # leave this repetition's containers to the grace timer the release
        # exists to pre-empt.
        if deps.transcripts is not None:
            deps.transcripts.bind(transcript_path)
        yield
    finally:
        try:
            if deps.transcripts is not None:
                deps.transcripts.unbind()
        finally:
            if deps.release_tools is not None:
                await deps.release_tools()


async def _run_repetition(
    *,
    coord: _CellCoordinates,
    repetition: int,
    suite_root: Path,
    work_root: Path,
    deps: LoopAbDeps,
    booked: list[ProviderSpend],
) -> RepetitionOutcome:
    """Run one loop once over one brief and grade what it produced.

    Args:
        coord: The cell's loop, tier and brief.
        repetition: The 0-based repetition index.
        suite_root: Root of the brief suite.
        work_root: Root of this recording's working tree.
        deps: The recorder's injected collaborators.
        booked: Sink the run's spend is appended to the moment it is known,
            before anything that can fail. The money is already gone by then:
            grading, evidence-keeping or tool release raising afterwards
            makes the repetition unmeasured, never unpaid, and a row that
            forgot it would under-report the matrix total.

    Returns:
        The graded outcome for this repetition.
    """
    # Provisioning removes and re-copies a whole tree, which is long enough to
    # stall the accept loop of the gateway this same process is serving.
    workspace = await asyncio.to_thread(
        partial(
            seed_workspace,
            brief=coord.brief,
            suite_root=suite_root,
            work_root=work_root,
        )
    )
    cell = CellRun(
        loop_type=NotBlankStr(coord.loop_type),
        tier=coord.tier,
        brief=coord.brief,
        repetition=repetition,
        workspace=workspace,
    )
    # Read before the loop runs, because three of the five briefs declare a file
    # their seed already contains: asking only afterwards reports every run as
    # having produced it, whatever the run did.
    as_seeded = await asyncio.to_thread(_artifact_state, coord.brief, workspace)
    # One tracker per run: ``run_brief`` derives a deterministic task id from the
    # brief alone, so records would otherwise pool across every loop and tier
    # measuring that brief and become unattributable.
    cost_tracker = ProgressTrackingLedger()
    transcript_path = cell_evidence_dir(work_root, cell) / "transcript.jsonl"
    async with (
        _released_tools(deps, transcript_path),
        _cell_ledger(cell, deps, cost_tracker) as ledger,
    ):
        engine = await _build_engine(cell=cell, deps=deps, cost_tracker=cost_tracker)
        watch = StallWatch(
            ledger=ledger,
            cell=NotBlankStr(_cell_label(cell)),
            idle_seconds=deps.stall_idle_seconds,
            notify=partial(_forward_stall, deps.on_stall, _cell_label(cell)),
        )
        try:
            async with watch.watching():
                outcome = await run_brief(
                    engine, coord.brief, identity=_identity(coord.tier)
                )
        finally:
            # Booked however the run ended. A provider call that recorded cost
            # and then raised has still been paid for, and a row that reports
            # the failure without the spend under-reports the matrix total.
            #
            # The cost chokepoint submits each record on a background task so
            # the provider response returns immediately, so reading the ledger
            # straight after the run races them. Losing that race under-reports
            # the cell's spend by however many records were still in flight,
            # silently and without a failure anywhere: exactly the wrong shape
            # of wrong number for a figure a promotion decision is read off.
            await ledger.drain_pending_records()
            booked.extend(_spend_from_records(await collect_all_records(ledger)))
    # Grading shells out to the brief's check commands, which stalls the accept
    # loop of the gateway this same process serves for as long as they run.
    grade = await asyncio.to_thread(
        grade_executable, _resolved(coord.brief), cell.workspace.project_dir
    )
    await asyncio.to_thread(_keep_produced_tree, cell, work_root)
    produced = await asyncio.to_thread(
        _artifacts_produced, coord.brief, cell.workspace, as_seeded
    )
    metrics = outcome.metrics

    logger.info(
        EVALS_LOOP_AB_RUN_RECORDED,
        loop_type=coord.loop_type,
        tier=coord.tier.tier,
        brief_id=coord.brief.brief_id,
        grade=grade.score,
        total_tokens=metrics.total_tokens,
        duration_seconds=metrics.duration_seconds,
        turns=metrics.total_turns,
        termination_reason=outcome.termination_reason,
        artifacts_produced=produced,
        governance_events=tuple(sorted(outcome.tracked_events)),
    )
    return RepetitionOutcome(
        correctness=grade.score,
        passed=grade.is_clean,
        termination_reason=outcome.termination_reason,
        artifacts_produced=produced,
        governance_events=dict(outcome.tracked_events),
        metrics=metrics,
    )


def _unavailable_row(
    coord: _CellCoordinates,
    exc: Exception,
    spend: tuple[ProviderSpend, ...] = (),
) -> LoopBriefRow:
    """Build the unavailable row for a cell that could not be measured.

    Any *spend* already booked is carried onto the row, including the failed
    repetition's own if it got as far as running: a failure must not erase the
    money the cell has already consumed from ``total_cost`` and the
    per-provider breakdown.

    Returns:
        A :class:`LoopBriefRow` carrying the redacted failure reason and the
        spend collected before the failure.
    """
    return LoopBriefRow(
        loop_type=NotBlankStr(coord.loop_type),
        brief_id=coord.brief.brief_id,
        tier=coord.tier.tier,
        model_id=coord.tier.model_id,
        unavailable_reason=f"{type(exc).__name__}: {safe_error_description(exc)}",
        spend=spend,
    )


async def _run_cell(
    *,
    coord: _CellCoordinates,
    manifest: LoopAbManifest,
    suite_root: Path,
    work_root: Path,
    deps: LoopAbDeps,
) -> LoopBriefRow:
    """Run every repetition for one ``(loop, tier, brief)`` and build its row.

    A cell that cannot be measured (its loop's runtime is unwired, its provider
    exhausts retries, or any other failure of that cell) yields an unavailable
    row carrying the reason, never a missing row and never a fabricated zero.
    This is what keeps a transient failure on one cell of a long real-spend
    matrix from discarding every other already-measured, already-paid-for cell:
    the whole scoreboard is always assembled and written.

    The same reasoning applies inside a cell. A failure on the last of several
    repetitions leaves the earlier ones measured and paid for, and a summary
    over fewer repetitions is a weaker measurement, not an absent one, so the
    cell reports what it managed rather than discarding it. Only a cell that
    never completed one repetition has nothing to report.

    Failures of the machine or the configuration are not caught here at all.
    They are true of every remaining cell, so absorbing them would spend the
    rest of the matrix rediscovering one fact, each time after a full retry
    budget, and report it as a property of each loop in turn.

    Raises:
        LoopAbProviderMissingError: A tier names an absent provider, which no
            other cell can survive either.
        LoopAbGatewayUnavailableError: The hosted gateway is gone.
        LoopAbDockerUnavailableError: The Docker daemon is gone.
        EvalToolMissingError: A grading command is absent from PATH, so no
            other cell could be graded either.
    """
    outcomes: list[RepetitionOutcome] = []
    spend: list[ProviderSpend] = []
    for repetition in range(manifest.repetitions):
        try:
            outcome = await _run_repetition(
                coord=coord,
                repetition=repetition,
                suite_root=suite_root,
                work_root=work_root,
                deps=deps,
                booked=spend,
            )
        except MemoryError, RecursionError:
            raise
        except _SYSTEMIC_FAILURES:
            raise
        except Exception as exc:  # noqa: BLE001 -- failed cell recorded as unavailable
            logger.warning(
                EVALS_LOOP_AB_LOOP_UNAVAILABLE,
                loop_type=coord.loop_type,
                tier=coord.tier.tier,
                brief_id=coord.brief.brief_id,
                repetition=repetition,
                completed_repetitions=len(outcomes),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            if not outcomes:
                return _unavailable_row(coord, exc, tuple(spend))
            logger.warning(
                EVALS_LOOP_AB_CELL_PARTIAL,
                loop_type=coord.loop_type,
                tier=coord.tier.tier,
                brief_id=coord.brief.brief_id,
                completed_repetitions=len(outcomes),
                planned_repetitions=manifest.repetitions,
            )
            break
        outcomes.append(outcome)

    summary: LoopRepetitionSummary = summarise_repetitions(
        loop_type=coord.loop_type,
        outcomes=tuple(outcomes),
        planned=manifest.repetitions,
    )
    return LoopBriefRow(
        loop_type=NotBlankStr(coord.loop_type),
        brief_id=coord.brief.brief_id,
        tier=coord.tier.tier,
        model_id=coord.tier.model_id,
        measurement=summary,
        score=None,
        spend=tuple(spend),
    )


def _score_rows(rows: tuple[LoopBriefRow, ...]) -> tuple[LoopBriefRow, ...]:
    """Score each ``(brief, tier)`` cell and attach the scores to their rows.

    Scoring is per cell because the efficiency dimensions are relative: a loop's
    token or latency score only means something against the other loops that
    ran the same brief on the same model.

    Returns:
        The rows with scores attached where a measurement exists.
    """
    cells: dict[tuple[str, str], list[int]] = {}
    for index, row in enumerate(rows):
        if row.measurement is not None:
            cells.setdefault((row.brief_id, row.tier), []).append(index)

    scored_by_index: dict[int, LoopCellScore] = {}
    for indices in cells.values():
        members = [rows[index] for index in indices]
        scores = score_cell(tuple(row.measurement.aggregate for row in members))  # type: ignore[union-attr]
        scored_by_index.update(zip(indices, scores, strict=True))

    return tuple(
        row.model_copy(update={"score": scored_by_index[index]})
        if index in scored_by_index
        else row
        for index, row in enumerate(rows)
    )


async def run_matrix(
    *,
    manifest: LoopAbManifest,
    briefs: tuple[Brief, ...],
    suite_root: Path,
    work_root: Path,
    deps: LoopAbDeps,
    provenance: Provenance,
) -> Scoreboard:
    """Run the whole matrix and assemble the scoreboard.

    Args:
        manifest: The recording matrix.
        briefs: The loaded brief suite.
        suite_root: Directory brief seed fixtures resolve against.
        work_root: Directory per-run workspaces are created under.
        deps: Runtime collaborators.
        provenance: What this recording is measured against.

    Returns:
        The assembled :class:`Scoreboard`, including its promotion
        recommendation.
    """
    rows = [
        await _run_cell(
            coord=_CellCoordinates(loop_type=loop_type, tier=tier, brief=brief),
            manifest=manifest,
            suite_root=suite_root,
            work_root=work_root / tier.tier / loop_type,
            deps=deps,
        )
        for tier in manifest.tiers
        for brief in briefs
        for loop_type in manifest.loops
    ]

    scored = _score_rows(tuple(rows))
    estimates = {brief.brief_id: brief.estimated_complexity for brief in briefs}
    cells: dict[tuple[str, str], list[LoopCellScore]] = {}
    for row in scored:
        if row.score is not None:
            cells.setdefault((row.brief_id, row.tier), []).append(row.score)
    buckets = rollup_by_complexity(
        tuple(
            (estimates[brief_id], tuple(scores))
            for (brief_id, _tier), scores in sorted(cells.items())
        )
    )
    return Scoreboard(
        provenance=provenance,
        weights=RubricWeights.current(),
        rows=scored,
        recommendation=recommend_promotion(buckets),
    )


__all__ = [
    "CellLedgerFactory",
    "CellRun",
    "LoopAbDeps",
    "OpenHandsCellFactory",
    "ProviderFactory",
    "StallReporter",
    "ToolRegistryFactory",
    "ToolReleaseHook",
    "run_matrix",
]
