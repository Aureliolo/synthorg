"""Whether a subtask runs, and what becomes of the ones that will not.

The plan's DAG decides WHEN a subtask runs; this module owns the separate
question of whether it SHOULD, given that the work it declared as input may
have died. It owns all four faces of that question together, because a
subtask left at CREATED has no exit and keeps its plan unfinished, so every
route out of a dispatch has to reach one of them: the wave being dispatched
(:func:`gate_wave`), the waves a stop never reached (:func:`abandon_after`),
the wave that raised before dispatching its own rows
(:func:`abandon_stranded`), and the work no wave was ever built for
(:func:`abandon_unreachable`). Cover three and the fourth leaks rows.

Kept apart from the loop that calls them so the loop reads as a statement
about dispatch order, and so the rule has one home rather than being spelled
out again wherever a wave is run.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

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


@dataclass(frozen=True, slots=True)
class GatedWave:
    """What a wave has left to dispatch, and why it has nothing when it does.

    A wave can empty out two ways that must not be confused. Every subtask
    parked on inputs that died means the plan did not deliver this level, and
    the run has to say so. Every subtask already carrying an outcome means the
    level is DONE, which only a resumed run sees, and calling that a failure
    would fail a plan for having made progress.

    Attributes:
        group: What to dispatch, or ``None`` when nothing is left.
        settled: How many subtasks were dropped for already having an
            outcome.
        delivered: Whether every subtask of the wave already had an outcome,
            so there was nothing left for the dependency gate to refuse. The
            caller cannot derive this from ``settled``, because a wave can
            drop some subtasks for being settled and park the rest.
    """

    group: ParallelExecutionGroup | None
    settled: int
    delivered: bool


async def gate_wave(
    group: ParallelExecutionGroup,
    *,
    wave_idx: int,
    assignment_writer: AssignmentWriter,
    dependencies: Mapping[str, tuple[str, ...]],
    clock: Clock,
    start: float,
    phases: list[CoordinationPhaseResult],
) -> GatedWave:
    """Narrow a wave to the subtasks that should actually run.

    The one owner of "may this subtask run", shared by every dispatcher, so a
    second wave loop cannot quietly dispatch on work that died. Two grounds,
    kept together because they answer one question and a caller holding half
    of it would dispatch on the other half: a subtask whose declared inputs
    did not arrive is parked BLOCKED naming what it waited on, and a subtask
    that already has an outcome is simply not proposed again.

    Args:
        group: The wave as the DAG scheduled it.
        wave_idx: Which wave this is, for the phase label and the log.
        assignment_writer: Applies the gate and parks what it drops.
        dependencies: Each subtask id mapped to the ids it depends on.
        clock: Injectable time source.
        start: When the wave began, for the phase duration.
        phases: Accumulator; gains an entry when nothing is left to run.

    Returns:
        The narrowed wave. A wave left with nothing to dispatch because its
        inputs died is recorded as a FAILED phase rather than skipped: the
        plan did not deliver this level, and a phase list that says otherwise
        is what lets a rollup read the run as still working. A wave left with
        nothing because every subtask already delivered records a successful
        phase, because the level IS delivered.
    """
    unsettled, settled = await assignment_writer.narrow_to_awaiting_dispatch(group)
    gated = await assignment_writer.gate_on_dependencies(unsettled, dependencies)
    logger.info(
        COORDINATION_WAVE_STARTED,
        wave_index=wave_idx,
        subtask_count=len(gated.assignments),
        gated_out=len(unsettled.assignments) - len(gated.assignments),
        already_settled=settled,
    )
    delivered = not unsettled.assignments
    if gated.assignments:
        return GatedWave(group=gated, settled=settled, delivered=delivered)

    phases.append(
        CoordinationPhaseResult(
            phase=phase_name(wave_idx),
            success=delivered,
            duration_seconds=clock.monotonic() - start,
            error=(
                None
                if delivered
                else (
                    f"Wave {wave_idx}: every subtask parked on work that "
                    "did not deliver"
                )
            ),
        )
    )
    return GatedWave(group=None, settled=settled, delivered=delivered)


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


async def abandon_unreachable(
    groups: tuple[ParallelExecutionGroup, ...],
    *,
    subtask_ids: Iterable[str],
    writer: AssignmentWriter,
) -> None:
    """Park every subtask of the plan that no wave was built for.

    The fourth face, and the only one the loop cannot see for itself: the
    other three reason about waves that exist. The wave BUILDER drops a
    subtask it cannot place with an agent, and then drops everything
    transitively standing on it, into a set local to the build. No group
    carries those rows, so no gate narrows them, no stop abandons them and no
    raise strands them. They stay at CREATED: nothing dispatches them, the
    rollup reads no CREATED row, and the plan stays executing while a recovery
    sweep re-drives it every cadence and changes nothing.

    Derived rather than reported, so the builder keeps its signature and no
    second list can disagree with what was actually built: the ids the plan
    declares, minus the ids the groups carry.

    Args:
        groups: Every wave the builder produced.
        subtask_ids: Every subtask id the plan declares.
        writer: Applies the park.
    """
    scheduled = {
        str(assignment.task.id) for group in groups for assignment in group.assignments
    }
    unreachable = [task_id for task_id in subtask_ids if task_id not in scheduled]
    if not unreachable:
        return
    parked = await writer.abandon_unreachable(unreachable)
    if parked:
        # No wave index and no remaining count: there is no wave to name.
        logger.info(
            COORDINATION_WAVES_ABANDONED,
            parked_subtasks=parked,
            note=(
                "no wave was built for these subtasks; their prerequisites "
                "could not be placed with any agent"
            ),
        )


__all__ = [
    "GatedWave",
    "abandon_after",
    "abandon_stranded",
    "abandon_unreachable",
    "gate_wave",
]
