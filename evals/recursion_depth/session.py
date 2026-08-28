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
from typing import Protocol, runtime_checkable

from evals.harness.binding import RunBinding
from evals.harness.stall_watch import (
    DEFAULT_STALL_IDLE_SECONDS,
    ProgressTrackingLedger,
    StallWatch,
)
from evals.harness.transcript import TranscriptRecorder
from evals.harness.workspace import CellWorkspace
from evals.prompt_layers import bind_default_prompt_layers
from evals.recursion_depth.grading import (
    SandboxFactory,
    SandboxReleaseHook,
    UnitGrader,
)
from evals.recursion_depth.manifest import ModelPair
from synthorg.budget.call_category import LLMCallCategory
from synthorg.budget.cost_record import CostRecord
from synthorg.budget.tracker_protocol import collect_all_records
from synthorg.core.agent import AgentIdentity
from synthorg.core.task import Task
from synthorg.core.types import NotBlankStr
from synthorg.engine.agent_engine import AgentEngine
from synthorg.engine.artifacts.baseline_scope import workspace_run_probe
from synthorg.engine.recovery import FailAndReassignStrategy
from synthorg.observability import get_logger
from synthorg.observability.events.evals import (
    EVALS_RECURSION_SPEND_ALL_DROPPED,
    EVALS_RECURSION_SPEND_DEDUPED,
    EVALS_RECURSION_SPEND_EMPTY,
    EVALS_RECURSION_UNIT_EXECUTED,
    EVALS_RECURSION_UNIT_FAILED_SPEND,
    EVALS_RECURSION_UNIT_STARTED,
)
from synthorg.persistence.checkpoint_protocol import (
    CheckpointRepository,
    HeartbeatRepository,
)
from synthorg.persistence.project_protocol import ProjectRepository
from synthorg.providers.protocol import CompletionProvider
from synthorg.settings.model_ref import ModelRef
from synthorg.tools.base import BaseTool
from synthorg.tools.registry import ToolRegistry

logger = get_logger(__name__)

ProviderFactory = Callable[[RunBinding], Awaitable[CompletionProvider]]


@runtime_checkable
class ToolRegistryFactory(Protocol):
    """Builds a unit's tools, scoped to its workspace."""

    def __call__(
        self, workspace: CellWorkspace, /, *, owner: str
    ) -> ToolRegistry | None:
        """Return the registry, filing what it holds open under *owner*.

        The owner is the only key that may later release it. Units run
        concurrently and a sandbox teardown latches, so a release naming no
        owner takes a running sibling's shell and it never comes back.
        """
        ...


@runtime_checkable
class GraderFactory(Protocol):
    """Builds what grades a delivered tree."""

    def __call__(self, workspace: CellWorkspace, /, *, owner: str) -> UnitGrader:
        """Return the grader, filing what it holds open under *owner*.

        Grading runs after the session that produced the tree has closed, so it
        takes an owner of its own: under the session's key it would be released
        before it had run the suite it exists to run.
        """
        ...


#: Releases whatever ONE owner holds open. The deployment's sandbox lifecycle
#: keeps a warm container per owner on a grace timer the strategy object owns,
#: and every unit builds and discards its own, so a sweep of hundreds of units
#: would otherwise leave one container behind per unit. Owner-scoped because
#: units run concurrently: a release naming no owner takes a running sibling's
#: sandbox, which latches shut rather than reopening.
ToolReleaseHook = SandboxReleaseHook
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
        open_run_ledger: Installs the authoritative cost sink and yields it.
            ``None`` means no gateway is hosted, so the engine's own tracker is
            the ledger; that is the offline path the suite drives. Called once
            per CELL rather than once per session: it swaps a process-wide
            field, and sessions within a cell run concurrently.
        cell_ledger: The sink this cell's sessions share, set by the runner for
            the length of one cell. Present means the swap has already
            happened at the cell boundary, where nothing is concurrent, and a
            session filters its own records out of it by task id.
        project_repo: Where the engine looks the benchmark project up. Every
            unit declares an artifact, which makes it a work task, and the
            engine refuses a work task whose project it cannot validate.
        checkpoint_repo: Where a session's conversation is persisted, every
            turn. Without it a provider failure that outlasts the retry ladder
            discards the whole session: the loop returns a terminal ERROR and
            nothing can re-enter a conversation nobody wrote down. A sweep unit
            is hours of work, so the state goes on disk and a retry RESUMES.
        heartbeat_repo: The liveness half of the same mechanism. Required
            together with ``checkpoint_repo``: the engine refuses one without
            the other, because a checkpoint nothing declares stale is a
            resume point that can be handed to two runners at once.
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
    cell_ledger: ProgressTrackingLedger | None = None
    project_repo: ProjectRepository | None = None
    checkpoint_repo: CheckpointRepository | None = None
    heartbeat_repo: HeartbeatRepository | None = None
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
        ledger: Where this unit's spend is recorded. Shared with the other
            sessions of this cell, which is why ``task_id`` below is what
            separates them.
        label: What the stall watch and the log lines call this session.
        task_id: Whose records on that shared ledger are this session's.
    """

    engine: AgentEngine
    ledger: ProgressTrackingLedger
    label: str
    gateway_hosted: bool
    task_id: str
    already: int

    async def spend(self, *, turns: int | None = None) -> SessionSpend:
        """Read what this session has cost so far, in money and in tokens.

        Drains first: the cost chokepoint submits each record on a background
        task so the provider response returns immediately, and reading without
        draining loses whatever is still in flight, silently.

        Both figures off ONE drained read. Two reads would drain twice and the
        second could see records the first did not, so a unit's cost and its
        tokens would describe different sets of calls.

        Filtered by task id rather than isolated by a per-session ledger. A
        ledger is installed as a process-wide field, so a per-session one has
        to be SWAPPED, and with several leaves in flight the swaps interleave:
        the last one installed collects everyone's records and the rest collect
        none. Measured at concurrency 4, that left 59 of 183 leaf sessions
        journalling zero, the worst of them after 56 turns. One ledger per cell
        and a filter per session has no window to lose, at any concurrency.

        The id alone is not the filter, because a task id does not name one
        session. A merge node runs its assembly and its review under the SAME
        task, several times over, so an id-only read returns everything that
        node has spent so far while ``run_merge`` adds each read as a delta:
        the second read already holds the first, and the third holds both.
        What each session added is what stands past the count taken when it
        opened, and the sessions sharing an id are sequential, so nothing else
        can append between the two reads.

        Args:
            turns: How many turns the session took, or ``None`` when it ended
                in a way that never reported. A session that took NO turn
                spent nothing and is not the loss this guard watches for: the
                resume path opens one whose first call failed every retry, and
                escalating there teaches a reader to discount the line.

        Returns:
            What this session's calls add up to, each counted once.
        """
        await self.ledger.drain_pending_records()
        records = [
            record
            for record in await collect_all_records(self.ledger)
            if record.task_id == self.task_id
        ][self.already :]
        if not records and turns != 0:
            # The sibling guard in `session_spend` covers a ledger whose
            # accounts were all DROPPED. Nothing covered a session that
            # collected none, which reaches the same zero and went unreported
            # for an entire run. These rows are the only spend ledger there is.
            logger.error(
                EVALS_RECURSION_SPEND_EMPTY,
                label=self.label,
                task_id=self.task_id,
                turns=turns,
            )
        return session_spend(
            records, gateway_hosted=self.gateway_hosted, label=self.label
        )


def _split_by_category(
    records: Sequence[CostRecord],
) -> tuple[tuple[CostRecord, ...], tuple[CostRecord, ...]]:
    """Split *records* into the accounts to count and the ones to drop.

    Returns:
        ``(counted, dropped)``, together holding every record exactly once.
    """
    counted: list[CostRecord] = []
    dropped: list[CostRecord] = []
    for record in records:
        target = (
            counted if record.call_category is LLMCallCategory.PRODUCTIVE else dropped
        )
        target.append(record)
    return tuple(counted), tuple(dropped)


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
        # Split on the predicate in one pass rather than by testing membership
        # of the kept tuple: the predicate is what decides the split, and
        # asking the tuple instead rests the answer on model equality, which
        # is only unique here because an unrelated field defaults to a fresh
        # uuid4 per record.
        counted, dropped = _split_by_category(records)
        # Never silent. A category dropped here is either the duplicate this
        # exists to remove or a call that did not cross the gateway, and only
        # the log distinguishes them afterwards.
        if dropped:
            # An empty remainder is not a deduplication. Preferring one account
            # of a call presumes a second survives, and this session would be
            # carried forward as free having spent whatever the dropped set
            # cost, in the rows that are the only record of it.
            all_dropped = not counted
            emit = logger.error if all_dropped else logger.info
            event = (
                EVALS_RECURSION_SPEND_ALL_DROPPED
                if all_dropped
                else EVALS_RECURSION_SPEND_DEDUPED
            )
            emit(
                event,
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
            if all_dropped:
                # Count them all rather than nothing. The preference exists to
                # drop a SECOND account of a call, and with no first account
                # left there is nothing to prefer: these records are the only
                # account of what the session spent, exactly as they are with
                # no gateway hosted. Reporting zero would be as wrong as the
                # double-count, and wrong in the direction nothing later
                # corrects, since the money is spent either way.
                counted = records
    return SessionSpend(
        cost=sum(record.cost for record in counted),
        tokens=sum(record.input_tokens + record.output_tokens for record in counted),
    )


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
            task_id=binding.task_id,
            already=await _standing(ledger, binding.task_id),
        )


async def _standing(ledger: ProgressTrackingLedger, task_id: str) -> int:
    """How many records the ledger already holds against *task_id*.

    Args:
        ledger: The ledger this session will write into.
        task_id: The id its calls will carry.

    Returns:
        The count standing before the session takes a turn.
    """
    await ledger.drain_pending_records()
    return sum(
        1 for record in await collect_all_records(ledger) if record.task_id == task_id
    )


def watching(
    deps: SweepDeps, session: OpenSession
) -> AbstractAsyncContextManager[None]:
    """Report *session* for as long as it stays quiet.

    The ledger it reads is the CELL's, shared with every sibling leaf in
    flight, so the watch is pointed at this session's own task. Read whole, a
    session that stopped answering is invisible for as long as any sibling is
    working, and one quiet cell reports once per concurrent watch under a
    different label each time.

    A merge and the reviews that follow it run under one task id, so they share
    a reading. That is what they are: one assembly, taken in sequence.

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
        task_id=NotBlankStr(session.task_id),
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
    resume: bool = False,
) -> SessionOutcome:
    """Run *task* as *identity* against *workspace* and report what it cost.

    Args:
        deps: The sweep's injected collaborators.
        identity: Who runs it, carrying the explicit pair it dispatches on.
        task: What it is asked to do.
        workspace: The tree it works in.
        execution_id: What the gateway ledger keys this session's spend on.
        limits: The turn and spend bounds this session gets.
        resume: Continue the conversation this ``execution_id`` already
            checkpointed rather than starting a fresh one. The id is derived
            from the cell and the task, so it is the same string on every
            attempt, which is what makes a resume addressable at all. Ignored
            with no checkpoint on disk: the engine replays what it finds, and
            an attempt that failed before its first checkpoint has nothing to
            continue from and simply starts over.

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
    turns: int | None = None
    async with open_session(deps, binding=binding, workspace=workspace) as session:
        try:
            async with watching(deps, session):
                result = await session.engine.run(
                    identity=identity,
                    task=bounded,
                    max_turns=limits.max_turns,
                    resume_execution_id=execution_id if resume else None,
                )
            turns = result.total_turns
        except BaseException:
            # A raising session builds no `SessionOutcome`, so this log line is
            # the only place its spend is written down: no cell record, no
            # journal row and no report ever sees it. The calls were billed
            # whether or not the session reached an ending.
            failed = await session.spend(turns=turns)
            logger.warning(
                EVALS_RECURSION_UNIT_FAILED_SPEND,
                execution_id=execution_id,
                task_id=str(task.id),
                agent_id=str(identity.id),
                turns=turns,
                cost=failed.cost,
                tokens=failed.tokens,
            )
            raise
        # Read however the session ended. A provider call that recorded cost
        # and then raised has still been paid for, and a unit that reports the
        # failure without the spend under-reports the sweep.
        spend = await session.spend(turns=turns)
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
    base = deps.build_tool_registry(workspace, owner=binding.execution_id)
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
        run_probe=workspace_run_probe(workspace.root),
        recovery_strategy=FailAndReassignStrategy(),
        # Both or neither, which the engine enforces. With them a session's
        # conversation is on disk turn by turn, so a failure that outlasts the
        # retry ladder costs the turns still in flight rather than all of them.
        checkpoint_repo=deps.checkpoint_repo,
        heartbeat_repo=deps.heartbeat_repo,
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

    Binds under THIS session's label. Sessions run concurrently, so a recorder
    holding one current path records whichever session bound last and drops the
    rest: a live cell produced no transcript at all for three of its eight
    leaves, and wrote four units' requests into one file.

    Args:
        deps: The sweep's injected collaborators.
        label: This session's execution id, which names its transcript.

    Yields:
        Nothing; the unbind runs on the way out.
    """
    try:
        if deps.transcripts is not None and deps.transcript_root is not None:
            deps.transcripts.bind(label, deps.transcript_root / f"{label}.jsonl")
        yield
    finally:
        if deps.transcripts is not None:
            deps.transcripts.unbind(label)


@contextlib.asynccontextmanager
async def _released_tools(deps: SweepDeps, label: str) -> AsyncIterator[None]:
    """Record this session's exchanges and release what its tools hold open.

    Both live here because both must run however the session ends, and the
    bind is inside the guard rather than ahead of it: binding creates the
    transcript's parent directory, so it can fail, and a failure before the
    try would leave this session's containers to the grace timer the release
    exists to pre-empt.

    The release names THIS session, so a sibling still running keeps the shell
    it is working in. Unscoped, the first unit of a concurrent wave to finish
    tore down every sandbox open at that moment, and the flag that teardown
    sets never clears: the siblings spent the rest of their budget retrying a
    command that could not succeed and were recorded as having built nothing.

    Args:
        deps: The sweep's injected collaborators.
        label: This session's execution id, which owns both the transcript and
            whatever its tools hold open.

    Yields:
        Nothing; the unbind and the release run on the way out.
    """
    try:
        async with transcript_scope(deps, label):
            yield
    finally:
        if deps.release_tools is not None:
            await deps.release_tools(label)


@contextlib.asynccontextmanager
async def graded(
    deps: SweepDeps, workspace: CellWorkspace, *, owner: str
) -> AsyncIterator[UnitGrader]:
    """Open a grader for *workspace* and release its container afterwards.

    Grading opens a sandbox of its own and runs after the session that produced
    the tree has closed, so it can be neither released by that session nor left
    to the run's end: one container per graded unit, held for the length of a
    matrix, is the leak the per-session release exists to prevent.

    Args:
        deps: The sweep's injected collaborators.
        workspace: The tree to grade.
        owner: The key this grading's container is filed under. Distinct from
            any session's, so nothing releases it out from under the suite.

    Yields:
        The grader, live for the length of the block.
    """
    try:
        yield deps.build_grader(workspace, owner=owner)
    finally:
        if deps.release_tools is not None:
            await deps.release_tools(owner)


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
    if deps.cell_ledger is not None:
        # Already installed, once, at the cell boundary. Opening another here
        # would swap a process-wide field while this cell's sibling sessions
        # are mid-flight, which is exactly the race the cell-scoped ledger
        # exists to remove.
        return contextlib.nullcontext(deps.cell_ledger)
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
    "graded",
    "ledger_scope",
    "open_session",
    "run_binding",
    "run_session",
    "session_spend",
    "transcript_scope",
    "watching",
]
