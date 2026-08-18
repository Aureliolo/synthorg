"""Whether a subtask runs, and what becomes of the ones that will not.

The plan's DAG decides WHEN a subtask runs; this module owns the separate
question of whether it SHOULD, given that the work it declared as input may
have died. It owns all three faces of that question together, because a
subtask left at CREATED has no exit and keeps its plan unfinished, so every
route out of a dispatch has to reach one of them: the wave being dispatched
(:func:`gate_wave`), the waves a stop never reached (:func:`abandon_after`),
and the wave that raised before dispatching its own rows
(:func:`abandon_stranded`). Cover two and the third leaks rows.

Kept apart from the loop that calls them so the loop reads as a statement
about dispatch order, and so the rule has one home rather than being spelled
out again wherever a wave is run.
"""

from collections.abc import Mapping

from synthorg.core.clock import Clock
from synthorg.engine.coordination._wave_outcome import phase_name
from synthorg.engine.coordination.assignment_writer import AssignmentWriter
from synthorg.engine.coordination.models import CoordinationPhaseResult
from synthorg.engine.parallel_models import ParallelExecutionGroup
from synthorg.observability import get_logger
from synthorg.observability.events.coordination import (
    COORDINATION_WAVE_STARTED,
    COORDINATION_WAVES_ABANDONED,
)

logger = get_logger(__name__)


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
            phase=phase_name(wave_idx),
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
    being dispatched, and this covers the ones after it. A wave the run never
    reached leaves its subtasks at CREATED, which no dispatcher will run and
    no gate will park, and a plan holding one subtask that cannot become
    terminal never concludes.

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


__all__ = ["abandon_after", "abandon_stranded", "gate_wave"]
