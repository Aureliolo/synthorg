"""Running a plan's dependency-ordered waves, one level at a time.

Kept apart from the workspace helpers it used to sit beside: those set up,
merge and tear down a group's worktrees, which is a different concern from
deciding what actually dispatches, and holding both made one module the
place every dispatcher change landed.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from synthorg.core.clock import Clock
from synthorg.core.critical_errors import reraise_critical
from synthorg.engine.coordination._wave_outcome import WaveVerdict, classify_wave
from synthorg.engine.coordination.assignment_writer import AssignmentWriter
from synthorg.engine.coordination.models import (
    CoordinationPhaseResult,
    CoordinationWave,
)
from synthorg.engine.parallel_models import ParallelExecutionGroup
from synthorg.engine.parallel_protocol import ParallelExecutorProtocol
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.coordination import (
    COORDINATION_PHASE_FAILED,
    COORDINATION_WAVE_AWAITING_HUMAN,
    COORDINATION_WAVE_COMPLETED,
    COORDINATION_WAVE_STARTED,
    COORDINATION_WAVES_ABANDONED,
)
from synthorg.observability.tracing.instrumentation import get_tracer

logger = get_logger(__name__)
_tracer = get_tracer(__name__)


@runtime_checkable
class WaveResources(Protocol):
    """Per-wave preparation a dispatcher cuts and settles one level at a time.

    Most dispatchers set their workspaces up once for the whole run and pass
    nothing here. One cuts them per wave, and folding its own copy of the loop
    back into this one is what keeps a gate or park rule from having to be
    written twice: the seam is the only thing that differed.

    Deliberately says ``prepare`` and ``settle`` rather than naming workspaces,
    so the wave loop stays a statement about dispatch order and the
    implementation owns what a wave actually holds.
    """

    async def prepare(
        self,
        wave_idx: int,
        group: ParallelExecutionGroup,
        *,
        phases: list[CoordinationPhaseResult],
    ) -> ParallelExecutionGroup | None:
        """Ready this wave, returning it rebuilt, or ``None`` when it cannot run.

        Returns:
            The group to dispatch, or ``None`` when preparation failed. The
            implementation records its own phase either way.
        """
        ...

    async def settle(
        self,
        wave_idx: int,
        *,
        verdict: WaveVerdict,
        phases: list[CoordinationPhaseResult],
    ) -> None:
        """Release what this wave held, whatever became of it."""
        ...


@dataclass(frozen=True, slots=True)
class _WaveRun:
    """Everything every wave of one dispatch shares.

    Bundled rather than threaded argument by argument: each wave needs the
    same nine things, and passing them individually made the per-wave
    helper's signature longer than its body was tall.

    Attributes:
        parallel_executor: Runs one wave's assignments.
        clock: Injectable time source.
        fail_fast: Whether a failed wave stops the run.
        assignment_writer: Gates and persists each wave before it runs.
        dependencies: Each subtask id mapped to the ids it depends on.
        waves: Accumulator, filled as each wave completes.
        phases: Accumulator, one entry per wave attempted.
        total_groups: How many waves the dispatch holds, for the
            remaining-count on a parked wave.
        resources: Per-wave preparation, or ``None`` when the dispatcher
            readied everything before the first wave.
    """

    parallel_executor: ParallelExecutorProtocol
    clock: Clock
    fail_fast: bool
    assignment_writer: AssignmentWriter
    dependencies: Mapping[str, tuple[str, ...]]
    waves: list[CoordinationWave]
    phases: list[CoordinationPhaseResult]
    total_groups: int
    resources: WaveResources | None


async def execute_waves(
    groups: tuple[ParallelExecutionGroup, ...],
    parallel_executor: ParallelExecutorProtocol,
    *,
    clock: Clock,
    fail_fast: bool,
    assignment_writer: AssignmentWriter,
    waves: list[CoordinationWave],
    dependencies: Mapping[str, tuple[str, ...]],
    resources: WaveResources | None = None,
) -> list[CoordinationPhaseResult]:
    """Execute wave groups sequentially, recording waves and phases.

    Each wave is gated on its subtasks' declared inputs and then persisted
    through *assignment_writer* immediately before it runs, so the central
    engine holds the assignment before any agent acts on it, and a wave
    whose assignment was refused fails visibly instead of running unsynced.

    Args:
        groups: The dependency-ordered wave groups.
        parallel_executor: Runs one wave's assignments.
        clock: Injectable time source.
        fail_fast: Whether a failed wave stops the run.
        assignment_writer: Gates and persists each wave before it runs.
        waves: Filled with every executed :class:`CoordinationWave`
            (including failed ones with no ``execution_result``) as each
            completes, so a caller unwinding on a cancellation can still
            see which waves ran and who parked in them.
        dependencies: Each subtask id mapped to the ids it declares it
            depends on, so a wave scheduled on work that died is parked
            rather than dispatched.
        resources: Per-wave preparation for a dispatcher that cuts its
            workspaces one level at a time. ``None`` for the dispatchers
            that ready everything before the first wave.

    Returns:
        The matching :class:`CoordinationPhaseResult` for each wave.
    """
    phases: list[CoordinationPhaseResult] = []
    run = _WaveRun(
        parallel_executor=parallel_executor,
        clock=clock,
        fail_fast=fail_fast,
        assignment_writer=assignment_writer,
        dependencies=dependencies,
        waves=waves,
        phases=phases,
        total_groups=len(groups),
        resources=resources,
    )

    for wave_idx, group in enumerate(groups):
        start = clock.monotonic()
        try:
            stop = await _run_one_wave(group, wave_idx=wave_idx, start=start, run=run)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            # lint-allow: swallow-ok -- best-effort side channel
            reraise_critical(exc)
            _record_wave_error(
                group,
                exc,
                wave_idx=wave_idx,
                elapsed=clock.monotonic() - start,
                run=run,
            )
            # This wave RAISED, so unlike one that ran it does not own its own
            # outcome: ``persist`` gives up on the first refused hop and the
            # release reverts only what it had already moved, leaving the rest
            # at CREATED. Not a status the gate treats as non-delivering, so
            # the next wave would dispatch against them. Unconditional because
            # those rows are stranded whether or not the run continues.
            await abandon_stranded(group, wave_idx=wave_idx, writer=assignment_writer)
            if fail_fast:
                await abandon_after(groups, wave_idx, writer=assignment_writer)
                break
        else:
            if stop:
                await abandon_after(groups, wave_idx, writer=assignment_writer)
                break

    return phases


def _phase_name(wave_idx: int) -> str:
    """Return the phase label a wave reports under.

    Returns:
        The ``execute_wave_<n>`` label.
    """
    return f"execute_wave_{wave_idx}"


async def gate_wave(
    group: ParallelExecutionGroup,
    *,
    wave_idx: int,
    assignment_writer: AssignmentWriter,
    dependencies: Mapping[str, tuple[str, ...]],
    clock: Clock,
    start: float,
    phases: list[CoordinationPhaseResult],
) -> ParallelExecutionGroup | None:
    """Narrow a wave to the subtasks whose declared inputs actually delivered.

    The one owner of "may this subtask run", shared by every dispatcher, so a
    second wave loop cannot quietly dispatch on work that died. Each subtask
    dropped here is parked BLOCKED naming what it waited on, by the writer.

    Args:
        group: The wave as the DAG scheduled it.
        wave_idx: Which wave this is, for the phase label and the log.
        assignment_writer: Applies the gate and parks what it drops.
        dependencies: Each subtask id mapped to the ids it depends on.
        clock: Injectable time source.
        start: When the wave began, for the phase duration.
        phases: Accumulator; gains a FAILED entry when nothing survives.

    Returns:
        The narrowed group, or ``None`` when every subtask parked. A wave
        that delivers nothing is recorded as a FAILED phase rather than
        skipped: the plan did not deliver this level, and a phase list that
        says otherwise is what lets a rollup read the run as still working.
    """
    gated = await assignment_writer.gate_on_dependencies(group, dependencies)
    logger.info(
        COORDINATION_WAVE_STARTED,
        wave_index=wave_idx,
        subtask_count=len(gated.assignments),
        gated_out=len(group.assignments) - len(gated.assignments),
    )
    if gated.assignments:
        return gated

    phases.append(
        CoordinationPhaseResult(
            phase=_phase_name(wave_idx),
            success=False,
            duration_seconds=clock.monotonic() - start,
            error=(
                f"Wave {wave_idx}: every subtask parked on work that did not deliver"
            ),
        )
    )
    return None


async def abandon_after(
    groups: tuple[ParallelExecutionGroup, ...],
    wave_idx: int,
    *,
    writer: AssignmentWriter,
) -> None:
    """Park every subtask of the waves this run stopped before reaching.

    The gate's other half, and the same single owner: gating covers the wave
    being dispatched, and this covers the ones after it, so no dispatcher can
    end a run leaving rows at CREATED with nothing watching them. A live run
    ended exactly there, and its plan sat at ``executing`` for ever because
    two subtasks of an unreached wave could never become terminal.

    Args:
        groups: Every wave of the dispatch, in order.
        wave_idx: The wave the run stopped at.
        writer: Applies the park.
    """
    abandoned = await writer.abandon_remaining(groups, stopped_at=wave_idx)
    if abandoned:
        logger.info(
            COORDINATION_WAVES_ABANDONED,
            stopped_at_wave=wave_idx,
            remaining_waves=len(groups) - wave_idx - 1,
            parked_subtasks=abandoned,
        )


async def abandon_stranded(
    group: ParallelExecutionGroup,
    *,
    wave_idx: int,
    writer: AssignmentWriter,
) -> None:
    """Park the rows of a wave that failed before dispatching them.

    The third face of the same single owner. :func:`gate_wave` covers the wave
    being dispatched and :func:`abandon_after` the ones after it; this covers
    the one that failed, whose own rows nothing else parks.

    Args:
        group: The wave that failed.
        wave_idx: Its index, for the reason and the log.
        writer: Applies the park.
    """
    stranded = await writer.abandon_stranded(group, stopped_at=wave_idx)
    if stranded:
        logger.info(
            COORDINATION_WAVES_ABANDONED,
            stopped_at_wave=wave_idx,
            remaining_waves=0,
            parked_subtasks=stranded,
        )


async def _run_one_wave(
    group: ParallelExecutionGroup,
    *,
    wave_idx: int,
    start: float,
    run: _WaveRun,
) -> bool:
    """Gate, dispatch and record one wave.

    Returns:
        ``True`` when the run must not proceed to the next wave.
    """
    gated = await gate_wave(
        group,
        wave_idx=wave_idx,
        assignment_writer=run.assignment_writer,
        dependencies=run.dependencies,
        clock=run.clock,
        start=start,
        phases=run.phases,
    )
    if gated is None:
        return run.fail_fast
    if run.resources is None:
        stop, _ = await _dispatch_gated_wave(
            gated, wave_idx=wave_idx, start=start, run=run
        )
        return stop

    # Failed until the wave earns otherwise. A cancellation is a
    # BaseException and skips every ``except Exception`` on the way out, so a
    # verdict assigned only on the success path is the one thing an unwind
    # cannot reach: starting here means an interrupted wave never reaches
    # ``settle`` claiming to have succeeded.
    verdict = WaveVerdict(failed=True, error=f"Wave {wave_idx}: did not finish")
    prepared = await run.resources.prepare(wave_idx, gated, phases=run.phases)
    if prepared is None:
        # The wave's own gated-in rows were never dispatched and nothing else
        # parks them. Unconditional: they are stranded whether or not the run
        # goes on to the next wave.
        await abandon_stranded(gated, wave_idx=wave_idx, writer=run.assignment_writer)
        return run.fail_fast
    try:
        stop, verdict = await _dispatch_gated_wave(
            prepared, wave_idx=wave_idx, start=start, run=run
        )
    finally:
        await run.resources.settle(wave_idx, verdict=verdict, phases=run.phases)
    return stop


async def _dispatch_gated_wave(
    gated: ParallelExecutionGroup,
    *,
    wave_idx: int,
    start: float,
    run: _WaveRun,
) -> tuple[bool, WaveVerdict]:
    """Persist, run and record one wave that survived the gate.

    Returns:
        Whether the run must stop, and the wave's verdict. The verdict is
        returned rather than only acted on because a caller settling per-wave
        resources has to know what became of the work before it merges any
        of it.
    """
    subtask_ids = tuple(str(a.task.id) for a in gated.assignments)

    with _tracer.start_as_current_span(
        "coordination.wave",
        attributes={
            "coordination.wave_index": wave_idx,
            "coordination.subtask_count": len(subtask_ids),
        },
        record_exception=False,
        set_status_on_exception=False,
    ):
        assigned = await run.assignment_writer.persist(gated)
        exec_result = await run.parallel_executor.execute_group(assigned)
    elapsed = run.clock.monotonic() - start

    run.waves.append(
        CoordinationWave(
            wave_index=wave_idx,
            subtask_ids=subtask_ids,
            execution_result=exec_result,
        )
    )

    verdict = classify_wave(wave_idx, exec_result)
    run.phases.append(
        CoordinationPhaseResult(
            phase=_phase_name(wave_idx),
            success=verdict.success,
            duration_seconds=elapsed,
            error=verdict.error,
        )
    )

    log = logger.info if verdict.success else logger.warning
    log(
        COORDINATION_WAVE_COMPLETED,
        wave_index=wave_idx,
        succeeded=exec_result.agents_succeeded,
        failed=exec_result.agents_failed,
        awaiting_human=exec_result.agents_awaiting_human,
        duration_seconds=elapsed,
    )

    if verdict.failed and run.fail_fast:
        return True, verdict
    # Not subject to fail_fast: a park is not a failure to push through, it
    # is a prerequisite that has not finished. The waves after this one were
    # scheduled on the promise that it had.
    if verdict.parked_task_ids:
        logger.info(
            COORDINATION_WAVE_AWAITING_HUMAN,
            wave_index=wave_idx,
            parked_tasks=len(verdict.parked_task_ids),
            remaining_waves=run.total_groups - wave_idx - 1,
        )
        return True, verdict
    return False, verdict


def _record_wave_error(
    group: ParallelExecutionGroup,
    exc: Exception,
    *,
    wave_idx: int,
    elapsed: float,
    run: _WaveRun,
) -> None:
    """Record a wave that threw before it could report its own outcome."""
    logger.warning(
        COORDINATION_PHASE_FAILED,
        phase=_phase_name(wave_idx),
        wave_index=wave_idx,
        error_type=type(exc).__name__,
        error=safe_error_description(exc),
    )
    # Read off the pre-gate group: the throw may have come from the gate
    # itself, and the wave record has to name every subtask this level was
    # meant to cover, not the subset that survived a step that did not
    # finish.
    run.waves.append(
        CoordinationWave(
            wave_index=wave_idx,
            subtask_ids=tuple(str(a.task.id) for a in group.assignments),
        )
    )
    run.phases.append(
        CoordinationPhaseResult(
            phase=_phase_name(wave_idx),
            success=False,
            duration_seconds=elapsed,
            # Scrubbed at the source so URL/form-body credentials in
            # HTTPStatusError-style messages never reach a consumer.
            error=safe_error_description(exc),
        )
    )


__all__ = ["abandon_after", "abandon_stranded", "execute_waves", "gate_wave"]
