# module-kind: adapter
"""Gathers the raw material for one run's narrative.

The reader is the only narrator component that touches the persistence
seams: it resolves the run's ``execution_id`` from the flight-recorder
aggregate, pages the frames newest-first under a bound, and pulls the
project-brain decisions and still-live items recorded against the brief.
It returns a :class:`RunNarrativeInputs` and performs no synthesis.
"""

from synthorg.core.types import NotBlankStr
from synthorg.meta.chief_of_staff.narrative.constants import (
    BRAIN_LIST_LIMIT,
    FRAME_PAGE_SIZE,
    MAX_DECISIONS,
    MAX_FRAMES_SCANNED,
    MAX_OPEN_ITEMS,
)
from synthorg.meta.chief_of_staff.narrative.errors import (
    NarrativeSourceUnavailableError,
)
from synthorg.meta.chief_of_staff.narrative.models import (
    AgentTurnTally,
    RunNarrativeInputs,
)
from synthorg.persistence.flight_recorder_protocol import (
    FlightRecorderFrame,
    FlightRecorderFrameFilterSpec,
    FlightRecorderFrameRepository,
)
from synthorg.persistence.task_protocol import TaskRepository
from synthorg.project_brain.models import (
    BrainEntry,
    BrainEntryKind,
    BrainEntryStatus,
    BrainSummary,
)
from synthorg.project_brain.service import ProjectBrainService

# Statuses that mean an item is still live at run end (an open question,
# a standing blocker, an active risk, an unresolved dependency).
_LIVE_STATUSES: frozenset[BrainEntryStatus] = frozenset(
    {
        BrainEntryStatus.OPEN,
        BrainEntryStatus.BLOCKED,
        BrainEntryStatus.ACTIVE,
    }
)


class NarrativeReader:
    """Reads flight-recorder frames and brain entries for one run."""

    __slots__ = ("_brain", "_frames", "_task_repo")

    def __init__(
        self,
        *,
        frames: FlightRecorderFrameRepository,
        brain: ProjectBrainService,
        task_repo: TaskRepository,
    ) -> None:
        self._frames = frames
        self._brain = brain
        self._task_repo = task_repo

    async def gather(
        self,
        *,
        task_id: NotBlankStr,
        project_id: NotBlankStr,
    ) -> RunNarrativeInputs:
        """Gather the raw inputs for the brief's narrative.

        Args:
            task_id: The brief / root task id.
            project_id: The owning project.

        Returns:
            The assembled :class:`RunNarrativeInputs`.

        Raises:
            NarrativeSourceUnavailableError: When the task is unknown or
                the run recorded no flight-recorder frames.
        """
        task = await self._task_repo.get(task_id)
        if task is None:
            msg = f"task {task_id!r} not found; nothing to narrate"
            raise NarrativeSourceUnavailableError(msg)
        aggregate = await self._frames.get_aggregate(
            FlightRecorderFrameFilterSpec(task_id=task_id)
        )
        execution_id = aggregate.latest_execution_id
        if execution_id is None:
            msg = f"task {task_id!r} has no recorded frames; nothing to narrate"
            raise NarrativeSourceUnavailableError(msg)
        frames = await self._scan_frames(execution_id)
        decisions, open_items = await self._gather_brain(
            project_id=project_id, task_id=task_id
        )
        return RunNarrativeInputs(
            project_id=project_id,
            task_id=task_id,
            execution_id=execution_id,
            brief_title=task.title,
            final_status=task.status,
            total_cost=aggregate.total_cost,
            total_turns=aggregate.max_turn_index,
            frame_count=len(frames),
            decisions=decisions,
            open_items=open_items,
            agent_turns=_tally_agents(frames),
        )

    async def _scan_frames(
        self, execution_id: NotBlankStr
    ) -> tuple[FlightRecorderFrame, ...]:
        """Page the run's frames newest-first up to the scan bound.

        Returns:
            The collected frames (at most :data:`MAX_FRAMES_SCANNED`).
        """
        spec = FlightRecorderFrameFilterSpec(execution_id=execution_id)
        collected: list[FlightRecorderFrame] = []
        offset = 0
        # lint-allow: long-running-loop-kill-switch -- bounded by MAX_FRAMES_SCANNED
        while len(collected) < MAX_FRAMES_SCANNED:
            page_limit = min(FRAME_PAGE_SIZE, MAX_FRAMES_SCANNED - len(collected))
            page = await self._frames.query(spec, limit=page_limit, offset=offset)
            if not page:
                break
            collected.extend(page)
            if len(page) < page_limit:
                break
            offset += len(page)
        return tuple(collected)

    async def _gather_brain(
        self,
        *,
        project_id: NotBlankStr,
        task_id: NotBlankStr,
    ) -> tuple[tuple[BrainEntry, ...], tuple[BrainSummary, ...]]:
        """Partition the run's brain entries into decisions and open items.

        Returns:
            A pair of (full decision entries, lightweight open-item
            summaries), each bounded.
        """
        summaries = await self._brain.list_current(
            project_id=project_id,
            related_task_id=task_id,
            limit=BRAIN_LIST_LIMIT,
        )
        decision_ids = tuple(
            s.entry_id for s in summaries if s.entry_kind is BrainEntryKind.DECISION
        )[:MAX_DECISIONS]
        decisions: list[BrainEntry] = []
        for entry_id in decision_ids:
            entry = await self._brain.get_current(
                project_id=project_id, entry_id=entry_id
            )
            if entry is not None:
                decisions.append(entry)
        open_items = tuple(s for s in summaries if s.status in _LIVE_STATUSES)[
            :MAX_OPEN_ITEMS
        ]
        return tuple(decisions), open_items


def _tally_agents(
    frames: tuple[FlightRecorderFrame, ...],
) -> tuple[AgentTurnTally, ...]:
    """Roll frames up per agent, ordered by contribution volume.

    Returns:
        Per-agent tallies sorted by descending turn count then agent id.
    """
    counts: dict[str, int] = {}
    costs: dict[str, float] = {}
    tools: dict[str, list[str]] = {}
    for frame in frames:
        agent = frame.agent_id
        counts[agent] = counts.get(agent, 0) + 1
        costs[agent] = costs.get(agent, 0.0) + frame.cost
        seen = tools.setdefault(agent, [])
        for tool in frame.tool_calls:
            if tool not in seen:
                seen.append(tool)
    tallies = tuple(
        AgentTurnTally(
            agent_id=NotBlankStr(agent),
            turn_count=counts[agent],
            cost=costs[agent],
            tools=tuple(tools[agent]),
        )
        for agent in counts
    )
    return tuple(sorted(tallies, key=lambda t: (-t.turn_count, t.agent_id)))
