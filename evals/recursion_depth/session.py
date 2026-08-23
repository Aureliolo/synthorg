# module-kind: code
"""One unit of a decomposition tree, run as one bounded agent session.

Every unit of this experiment is the same thing to the engine: one identity,
one task, one workspace, one bounded session. A leaf built from nothing, a
merge assembling what sits below it, a repair attempt after a rejection and the
ungated arm's blind pass differ only in the brief they carry and in what is
already on disk when they start. Those are their callers' business; everything
else is here, so the arms cannot drift apart in how a session is set up.

Spend is read off the ledger rather than off the run's own accumulated cost.
With the recording gateway in front, the ledger is where the authoritative
figure lands, and a per-unit ledger is what makes a unit's spend attributable
to it: the gateway's hard cost kill keys on the execution id, so a shared
ledger would also let a later unit inherit an exhausted ceiling.
"""

import contextlib
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from functools import partial
from pathlib import Path

from evals.harness.binding import RunBinding
from evals.harness.stall_watch import (
    DEFAULT_STALL_IDLE_SECONDS,
    ProgressTrackingLedger,
    StallWatch,
)
from evals.harness.transcript import TranscriptRecorder
from evals.harness.workspace import CellWorkspace, existing_workspace, seed_workspace
from evals.prompt_layers import bind_default_prompt_layers
from evals.recursion_depth.grading import SandboxFactory, UnitGrader
from evals.recursion_depth.manifest import ModelPair
from synthorg.budget.call_category import LLMCallCategory
from synthorg.budget.cost_record import CostRecord
from synthorg.budget.tracker_protocol import collect_all_records
from synthorg.core.agent import AgentIdentity
from synthorg.core.task import Task
from synthorg.core.types import NotBlankStr
from synthorg.engine.agent_engine import AgentEngine
from synthorg.engine.artifacts.expected_artifact_check import (
    ArtifactPresence,
    missing_expected_artifacts,
    workspace_artifact_probe,
)
from synthorg.engine.recovery import FailAndReassignStrategy
from synthorg.observability import get_logger
from synthorg.observability.events.evals import (
    EVALS_RECURSION_SPEND_DEDUPED,
    EVALS_RECURSION_UNIT_EXECUTED,
    EVALS_RECURSION_UNIT_STARTED,
)
from synthorg.persistence.project_protocol import ProjectRepository
from synthorg.providers.protocol import CompletionProvider
from synthorg.settings.model_ref import ModelRef
from synthorg.tools.base import BaseTool
from synthorg.tools.registry import ToolRegistry

logger = get_logger(__name__)

ProviderFactory = Callable[[RunBinding], Awaitable[CompletionProvider]]
ToolRegistryFactory = Callable[[CellWorkspace], ToolRegistry | None]
#: Builds what grades a delivered tree, scoped to the workspace it sits in.
GraderFactory = Callable[[CellWorkspace], UnitGrader]
#: Releases whatever a unit's tools hold open. The deployment's sandbox
#: lifecycle keeps a warm container per owner on a grace timer the strategy
#: object owns, and every unit builds and discards its own, so a sweep of
#: hundreds of units would otherwise leave one container behind per unit.
ToolReleaseHook = Callable[[], Awaitable[None]]
LedgerFactory = Callable[[str], AbstractAsyncContextManager[ProgressTrackingLedger]]
#: Called with ``(unit_label, idle_seconds)`` when a unit goes quiet.
StallReporter = Callable[[str, float], None]


@dataclass(frozen=True)
class SessionLimits:
    """What one session may consume.

    Attributes:
        max_turns: The turn ceiling the loop is given.
        cost_ceiling: What the bearer authorises before the gateway kills the
            run server-side.
        token_ceiling: The same bound counted in tokens. Load-bearing rather
            than belt-and-braces: a flat-rate connection attributes 0.0 to
            every call, so ``cost_ceiling`` can never fire there and a unit
            would run to its turn cap with no spend bound at all. Tokens are
            counted on every provider.
    """

    max_turns: int
    cost_ceiling: float
    token_ceiling: int


@dataclass(frozen=True)
class SweepDeps:
    """Runtime collaborators every unit of a sweep is driven with.

    Attributes:
        build_provider: Builds the completion driver one unit dispatches
            through. At record time it is routed at the hosted gateway and
            carries that unit's own bearer.
        build_tool_registry: Builds the file and shell tools scoped to a
            unit's workspace.
        build_grader: Builds what runs a delivered tree to decide whether it
            delivered. A separate seam from the tool registry because the two
            answer to different owners: the registry is the AGENT's, and this
            one is the harness's judgement of what the agent produced. Both put
            the execution in a container; only this one is trusted to report
            what happened.
        build_sandbox: Builds a container rooted at an arbitrary directory,
            which is what the held-out oracle grades in: it needs the tree and
            the oracle side by side, and no cell workspace may ever hold both.
        release_tools: Releases what that registry holds open, run after every
            unit whether it finished or raised.
        transcripts: Records every request and response crossing the hosted
            gateway. Without it a sweep answers only WHAT each cell scored,
            and the whole question is why a merge was rejected or what a
            reviewer actually said, which is unrecoverable afterwards.
        transcript_root: Directory the per-session transcripts are written
            under; ``None`` alongside a recorder writes none.
        open_run_ledger: Installs the authoritative cost sink for one unit and
            yields it. ``None`` means no gateway is hosted, so the engine's own
            tracker is the ledger; that is the offline path the suite drives.
        project_repo: Where the engine looks the benchmark project up. Every
            unit declares an artifact, which makes it a work task, and the
            engine refuses a work task whose project it cannot validate.
        stall_idle_seconds: Idle time after which a unit is reported stalled.
        on_stall: Second channel for that report, alongside the warning the
            watch always logs. A real sweep runs for hours in a terminal.
        declared_pairs: The manifest's own pairs, which is where a model FAMILY
            is written down. A live identity carries no such field, so without
            these every unit records ``family: null`` and the cross-family
            claim a gated result rests on is evidenced nowhere. Looked up by
            exact ``provider/model_id``, never derived from the provider: one
            connection serves many families.
    """

    build_provider: ProviderFactory
    build_tool_registry: ToolRegistryFactory
    build_grader: GraderFactory
    build_sandbox: SandboxFactory
    release_tools: ToolReleaseHook | None = None
    transcripts: TranscriptRecorder | None = None
    transcript_root: Path | None = None
    open_run_ledger: LedgerFactory | None = None
    project_repo: ProjectRepository | None = None
    stall_idle_seconds: float = DEFAULT_STALL_IDLE_SECONDS
    on_stall: StallReporter | None = None
    declared_pairs: tuple[ModelPair, ...] = ()


@dataclass(frozen=True)
class SessionSpend:
    """What a session's ledger adds up to.

    Attributes:
        cost: Summed cost of every record.
        tokens: Summed input plus output tokens across the same records.
    """

    cost: float
    tokens: int


@dataclass(frozen=True)
class SessionOutcome:
    """What one session did.

    Attributes:
        cost: What it spent, read off the ledger.
        tokens: Input plus output tokens across the same records. Reported
            beside cost because the two arms do different amounts of work per
            merge, and a survival gap bought with spend rather than with
            judgement is not the finding.
        turns: How many turns it took.
        termination: Why the loop stopped, for a human reading a failure.
    """

    cost: float
    tokens: int
    turns: int
    termination: str


@dataclass(frozen=True)
class OpenSession:
    """A session's engine and the ledger its spend lands in.

    Handed out rather than kept private because the review path does not
    dispatch its own task: it hands the engine to the production reviewer
    runner and lets the gate drive it, so the gate under measurement stays the
    shipped one and only the plumbing around it is the harness's.

    Attributes:
        engine: The engine, scoped to this unit's workspace.
        ledger: Where this unit's spend is recorded.
        label: What the stall watch and the log lines call this session.
    """

    engine: AgentEngine
    ledger: ProgressTrackingLedger
    label: str
    gateway_hosted: bool

    async def spend(self) -> SessionSpend:
        """Read what this session has cost so far, in money and in tokens.

        Drains first: the cost chokepoint submits each record on a background
        task so the provider response returns immediately, and reading without
        draining loses whatever is still in flight, silently.

        Both figures off ONE drained read. Two reads would drain twice and the
        second could see records the first did not, so a unit's cost and its
        tokens would describe different sets of calls.

        Returns:
            What this session's calls add up to, each counted once.
        """
        await self.ledger.drain_pending_records()
        records = await collect_all_records(self.ledger)
        return session_spend(
            records, gateway_hosted=self.gateway_hosted, label=self.label
        )


def session_spend(
    records: Sequence[CostRecord], *, gateway_hosted: bool, label: str
) -> SessionSpend:
    """Add up one session's records, counting each CALL exactly once.

    The single owner of that arithmetic, because two readers of one ledger is
    how a figure comes to mean different things in the cost panel and in the
    spend ceiling. A live run journalled a planning unit at twice what it
    spent.

    A hosted gateway is the recorder of RECORD: every call a sweep session
    makes crosses it, and it stamps ``PRODUCTIVE``, so anything else on this
    ledger is a second account of a call already counted rather than a call of
    its own. Whether one is hosted is passed in rather than inferred from the
    records, because the harness knows it as a fact and inferring it would mean
    guessing which of two equal sums is the duplicate.

    With no gateway there is nothing to prefer: every record is the only
    account of its call, and the offline suite drives exactly that path.

    Args:
        records: What the session's ledger holds, already drained.
        gateway_hosted: Whether this run's calls crossed a hosted gateway.
        label: Names the session in the log line below.

    Returns:
        The session's cost and tokens.
    """
    counted = records
    if gateway_hosted:
        counted = tuple(
            record
            for record in records
            if record.call_category is LLMCallCategory.PRODUCTIVE
        )
        # Never silent. A category dropped here is either the duplicate this
        # exists to remove or a call that did not cross the gateway, and only
        # the log distinguishes them afterwards.
        dropped = tuple(record for record in records if record not in counted)
        if dropped:
            logger.info(
                EVALS_RECURSION_SPEND_DEDUPED,
                label=label,
                counted_records=len(counted),
                dropped_records=len(dropped),
                dropped_categories=sorted(
                    {
                        record.call_category.value
                        if record.call_category is not None
                        else "uncategorised"
                        for record in dropped
                    }
                ),
                dropped_tokens=sum(
                    record.input_tokens + record.output_tokens for record in dropped
                ),
            )
    return SessionSpend(
        cost=sum(record.cost for record in counted),
        tokens=sum(record.input_tokens + record.output_tokens for record in counted),
    )


def leaf_unit_key(task_id: str) -> str:
    """What a leaf's tree is called under its cell.

    The single owner of the format, because a resume reaches for a tree a
    previous attempt built by rebuilding this string: a second spelling would
    look in an empty directory and re-run work already paid for.

    Returns:
        The key.
    """
    return f"leaf-{task_id}"


def merge_unit_key(task_id: str) -> str:
    """What an assembly's tree is called under its cell.

    Returns:
        The key.
    """
    return f"merge-{task_id}"


def unit_workspace(
    *, cell_key: str, unit_key: str, spec_dir: Path, work_root: Path
) -> CellWorkspace:
    """Recreate one unit's workspace from the specification's committed seed.

    Args:
        cell_key: Names the run this unit belongs to.
        unit_key: Names the unit within that run.
        spec_dir: The specification directory, which holds the seed.
        work_root: Directory per-unit trees are created under.

    Returns:
        The provisioned workspace.
    """
    return seed_workspace(
        cell_key=f"{cell_key}/{unit_key}",
        seed_dir="seed",
        suite_root=spec_dir,
        work_root=work_root,
    )


def built_unit_workspace(
    *, cell_key: str, unit_key: str, work_root: Path
) -> CellWorkspace | None:
    """The tree a previous attempt left for one unit, if it is still there.

    Args:
        cell_key: Names the run this unit belongs to.
        unit_key: Names the unit within that run.
        work_root: Directory per-unit trees live under.

    Returns:
        The workspace, or ``None`` when nothing was built there.
    """
    return existing_workspace(cell_key=f"{cell_key}/{unit_key}", work_root=work_root)


def probe_artifacts(task: Task, workspace: CellWorkspace) -> ArtifactPresence:
    """Ask *workspace* what it holds against *task*'s declared paths.

    Read off disk rather than from the session's account of itself: a run
    reports the tools it called, and whether those calls left the declared file
    behind is a different question that only the tree answers.

    Taken once BEFORE a session and once after, because the question delivery
    turns on is what this run produced rather than what the workspace happens
    to contain: the seed is recreated per unit and a declaration satisfied by
    the seed is not work.

    Args:
        task: The unit's task, carrying its declared artifacts.
        workspace: The tree it ran against.

    Returns:
        What each probeable declaration says right now.
    """
    return missing_expected_artifacts(
        task.artifacts_expected, workspace=workspace.project_dir
    )


def produced_nothing(
    task: Task, workspace: CellWorkspace, baseline: ArtifactPresence | None
) -> bool:
    """Whether this run left every declaration exactly as it found it.

    The product's own rule, through the product's own helper, and deliberately
    not a stricter one of this harness's own. ``ArtifactPresence`` states the
    rule where it is defined ("the 'none, not some' rule is this module's to
    state once"), and a harness measuring the product must not judge delivery
    harder than the product does.

    Asking instead whether ANY declared path was missing makes delivery turn on
    the planner's declaration rather than on the agent's work. That declaration
    is written per node at whatever granularity the planner chose, so one output
    is a delivery under a parent's two-entry list and a non-delivery under the
    leaf's four-entry one: a live run booked 598,585 tokens as no delivery over
    an absent empty ``tests/__init__.py``, on a unit that had written its
    module, a 31-test suite, and run it.

    What a unit declared is still recorded, because a planner over-declaring is
    worth seeing. It just does not decide.

    Args:
        task: The unit's task, carrying its declared artifacts.
        workspace: The tree it ran against.
        baseline: What the same probe said before the session ran.

    Returns:
        Whether nothing declared appeared, changed or was removed.
    """
    return probe_artifacts(task, workspace).delivered_nothing_since(baseline)


def run_binding(
    *, identity: AgentIdentity, task: Task, execution_id: str, limits: SessionLimits
) -> RunBinding:
    """Describe one session as the facts its gateway bearer carries.

    Args:
        identity: Who runs it, carrying the explicit pair it dispatches on.
        task: What it is asked to do.
        execution_id: What the gateway ledger keys this session's spend and its
            hard cost kill on. Unique per session, repair attempts included, or
            a later attempt inherits an exhausted ceiling.
        limits: The turn and spend bounds this session gets.

    Returns:
        The binding.
    """
    return RunBinding(
        execution_id=execution_id,
        agent_id=str(identity.id),
        task_id=str(task.id),
        ref=ModelRef(
            provider=identity.model.provider, model_id=identity.model.model_id
        ),
        cost_ceiling=limits.cost_ceiling,
        label=execution_id,
    )


@contextlib.asynccontextmanager
async def open_session(
    deps: SweepDeps,
    *,
    binding: RunBinding,
    workspace: CellWorkspace,
    extra_tools: tuple[BaseTool, ...] = (),
) -> AsyncIterator[OpenSession]:
    """Stand one session's engine and ledger up, and take them down after.

    Args:
        deps: The sweep's injected collaborators.
        binding: What this session is authorised to spend, and against which
            pair.
        workspace: The tree it works in.
        extra_tools: Tools beyond the workspace set, which is how a reviewer
            session receives its verdict tool.

    Yields:
        The open session.
    """
    fallback = ProgressTrackingLedger()
    async with (
        # Keyed on the execution id, which is what the ledger keys its spend
        # to, so a transcript and the cost it produced name the same session.
        _released_tools(deps, binding.execution_id),
        ledger_scope(deps, binding.execution_id, fallback) as ledger,
    ):
        engine = await _build_engine(
            deps,
            binding=binding,
            workspace=workspace,
            cost_tracker=fallback,
            extra_tools=extra_tools,
        )
        yield OpenSession(
            engine=engine,
            ledger=ledger,
            label=binding.execution_id,
            gateway_hosted=deps.open_run_ledger is not None,
        )


def watching(
    deps: SweepDeps, session: OpenSession
) -> AbstractAsyncContextManager[None]:
    """Report *session* for as long as it stays quiet.

    Args:
        deps: The sweep's collaborators, for the idle threshold and channel.
        session: The open session to watch.

    Returns:
        The watch's context manager.
    """
    watch = StallWatch(
        ledger=session.ledger,
        cell=NotBlankStr(session.label),
        idle_seconds=deps.stall_idle_seconds,
        notify=partial(_forward_stall, deps.on_stall, session.label),
    )
    return watch.watching()


def _token_bounded(task: Task, limits: SessionLimits) -> Task:
    """Arm the in-loop token kill this session's limits declare.

    The engine's budget checker reads ``Task.hard_token_ceiling``, never the
    sweep's own limits, so a ceiling that is not written onto the task binds
    nothing. That is not a spare belt here: every connection these sweeps run
    against is flat-rate, so ``cost_ceiling`` is measured against a cost the
    provider reports as ``0.0`` on every call and can never fire, leaving a
    runaway unit bounded only by its turn budget.

    Re-validated rather than ``model_copy``-ed, because a copy runs no
    validator and the field is constrained ``ge=0``.

    Returns:
        The task, carrying the session's token ceiling.
    """
    return Task.model_validate(
        task.model_dump() | {"hard_token_ceiling": limits.token_ceiling}
    )


async def run_session(
    deps: SweepDeps,
    *,
    identity: AgentIdentity,
    task: Task,
    workspace: CellWorkspace,
    execution_id: str,
    limits: SessionLimits,
) -> SessionOutcome:
    """Run *task* as *identity* against *workspace* and report what it cost.

    Args:
        deps: The sweep's injected collaborators.
        identity: Who runs it, carrying the explicit pair it dispatches on.
        task: What it is asked to do.
        workspace: The tree it works in.
        execution_id: What the gateway ledger keys this session's spend on.
        limits: The turn and spend bounds this session gets.

    Returns:
        The session's outcome.
    """
    logger.debug(
        EVALS_RECURSION_UNIT_STARTED,
        execution_id=execution_id,
        task_id=str(task.id),
        agent_id=str(identity.id),
        max_turns=limits.max_turns,
    )
    bounded = _token_bounded(task, limits)
    binding = run_binding(
        identity=identity, task=task, execution_id=execution_id, limits=limits
    )
    async with open_session(deps, binding=binding, workspace=workspace) as session:
        try:
            async with watching(deps, session):
                result = await session.engine.run(
                    identity=identity, task=bounded, max_turns=limits.max_turns
                )
        finally:
            # Read however the session ended. A provider call that recorded
            # cost and then raised has still been paid for, and a unit that
            # reports the failure without the spend under-reports the sweep.
            spend = await session.spend()
    outcome = SessionOutcome(
        cost=spend.cost,
        tokens=spend.tokens,
        turns=result.total_turns,
        termination=result.termination_reason.value,
    )
    logger.info(
        EVALS_RECURSION_UNIT_EXECUTED,
        execution_id=execution_id,
        task_id=str(task.id),
        agent_id=str(identity.id),
        turns=outcome.turns,
        cost=outcome.cost,
        termination_reason=outcome.termination,
    )
    return outcome


async def _build_engine(
    deps: SweepDeps,
    *,
    binding: RunBinding,
    workspace: CellWorkspace,
    cost_tracker: ProgressTrackingLedger,
    extra_tools: tuple[BaseTool, ...],
) -> AgentEngine:
    """Build the engine one session runs on.

    Returns:
        The configured engine.
    """
    # No API lifespan runs here, so the ambient prompt layers the product binds
    # at boot have to be bound explicitly or the sweep measures a prompt the
    # product never sends.
    bind_default_prompt_layers()
    base = deps.build_tool_registry(workspace)
    tools = _with_extra_tools(base, extra_tools)
    return AgentEngine(
        provider=await deps.build_provider(binding),
        tool_registry=tools,
        cost_tracker=cost_tracker,
        project_repo=deps.project_repo,
        # The same post-execution check the deployment runs, and the reason
        # every unit here declares an artifact: a session that answered in
        # prose having written nothing terminates NO_OP rather than reading as
        # a clean success, which would put undelivered work in the survival
        # denominator.
        artifact_probe=workspace_artifact_probe(workspace.root),
        recovery_strategy=FailAndReassignStrategy(),
    )


def _with_extra_tools(
    base: ToolRegistry | None, extra: tuple[BaseTool, ...]
) -> ToolRegistry | None:
    """Fold *extra* into *base*.

    Returns:
        A registry carrying both, or *base* when there is nothing to add.
    """
    if not extra:
        return base
    existing = base.all_tools() if base is not None else ()
    return ToolRegistry([*existing, *extra])


def _forward_stall(
    report: StallReporter | None, label: str, idle_seconds: float
) -> None:
    """Pass a stall report on to the caller's channel, when it wants one."""
    if report is not None:
        report(label, idle_seconds)


@contextlib.asynccontextmanager
async def transcript_scope(deps: SweepDeps, label: str) -> AsyncIterator[None]:
    """Record every exchange *label*'s session makes, however it ends.

    Shared by the planning and execution paths rather than written at each:
    the planner opens no session of its own, so a bind living only in
    ``open_session`` would silently record the building and skip the planning,
    which is the half the experiment is about.

    Yields:
        Nothing; the unbind runs on the way out.
    """
    try:
        if deps.transcripts is not None and deps.transcript_root is not None:
            deps.transcripts.bind(deps.transcript_root / f"{label}.jsonl")
        yield
    finally:
        if deps.transcripts is not None:
            deps.transcripts.unbind()


@contextlib.asynccontextmanager
async def _released_tools(deps: SweepDeps, label: str) -> AsyncIterator[None]:
    """Record this session's exchanges and release what its tools hold open.

    Both live here because both must run however the session ends, and the
    bind is inside the guard rather than ahead of it: binding creates the
    transcript's parent directory, so it can fail, and a failure before the
    try would leave this session's containers to the grace timer the release
    exists to pre-empt.

    Yields:
        Nothing; the unbind and the release run on the way out.
    """
    try:
        async with transcript_scope(deps, label):
            yield
    finally:
        if deps.release_tools is not None:
            await deps.release_tools()


def ledger_scope(
    deps: SweepDeps, execution_id: str, fallback: ProgressTrackingLedger
) -> AbstractAsyncContextManager[ProgressTrackingLedger]:
    """Open the cost sink whose records are one session's authoritative spend.

    With the recording gateway in front the ledger is the gateway's, not the
    engine's: it is where the authoritative figure lands, and installing a
    fresh one per session is what keeps a session's spend attributable to it
    and its hard cost kill keyed to its own execution id.

    Args:
        deps: The sweep's injected collaborators.
        execution_id: What the gateway keys this session's ledger on.
        fallback: The tracker to use when no gateway is hosted, which is the
            offline path the suite drives.

    Returns:
        A context manager yielding the tracker to collect records from.
    """
    if deps.open_run_ledger is None:
        return contextlib.nullcontext(fallback)
    return deps.open_run_ledger(execution_id)


__all__ = [
    "LedgerFactory",
    "OpenSession",
    "ProviderFactory",
    "SessionLimits",
    "SessionOutcome",
    "StallReporter",
    "SweepDeps",
    "ToolRegistryFactory",
    "ToolReleaseHook",
    "ledger_scope",
    "open_session",
    "probe_artifacts",
    "produced_nothing",
    "run_binding",
    "run_session",
    "session_spend",
    "transcript_scope",
    "unit_workspace",
    "watching",
]
