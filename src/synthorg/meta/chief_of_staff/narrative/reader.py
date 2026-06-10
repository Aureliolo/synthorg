# module-kind: adapter
"""Gathers the raw material for one run's narrative.

The reader is the only narrator component that touches the persistence
seams: it resolves the run's ``execution_id`` from the flight-recorder
aggregate, pages the frames newest-first under a bound, and pulls the
project-brain decisions and still-live items recorded against the brief.
It returns a :class:`RunNarrativeInputs` and performs no synthesis.
"""

import asyncio

from synthorg.budget.currency import DEFAULT_CURRENCY, CurrencyCode
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
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.chief_of_staff import (
    COS_NARRATIVE_DECISION_UNAVAILABLE,
    COS_NARRATIVE_FRAMES_TRUNCATED,
    COS_NARRATIVE_SOURCE_UNAVAILABLE,
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

logger = get_logger(__name__)

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

    __slots__ = ("_brain", "_currency", "_frames", "_task_repo")

    def __init__(
        self,
        *,
        frames: FlightRecorderFrameRepository,
        brain: ProjectBrainService,
        task_repo: TaskRepository,
        currency: CurrencyCode = DEFAULT_CURRENCY,
    ) -> None:
        self._frames = frames
        self._brain = brain
        self._task_repo = task_repo
        self._currency = currency

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
            # A task that just completed but is absent is a data-integrity
            # concern, not a routine skip: log it at WARNING so it stands
            # out from the benign no-frames case below.
            logger.warning(
                COS_NARRATIVE_SOURCE_UNAVAILABLE,
                task_id=task_id,
                project_id=project_id,
                reason="task_not_found",
            )
            msg = "task not found; nothing to narrate"
            raise NarrativeSourceUnavailableError(msg)
        aggregate = await self._frames.get_aggregate(
            FlightRecorderFrameFilterSpec(task_id=task_id)
        )
        execution_id = aggregate.latest_execution_id
        if execution_id is None:
            # A brief that ran but recorded no frames is an expected,
            # benign skip (e.g. it completed before any agent turn).
            logger.debug(
                COS_NARRATIVE_SOURCE_UNAVAILABLE,
                task_id=task_id,
                project_id=project_id,
                reason="no_frames_recorded",
            )
            msg = "no recorded frames; nothing to narrate"
            raise NarrativeSourceUnavailableError(msg)
        frames = await self._scan_frames(execution_id)
        if len(frames) >= MAX_FRAMES_SCANNED:
            # The scan bound was hit, so the roster and tallies reflect
            # only the newest frames; surface the truncation rather than
            # let an operator read a partial narrative as complete.
            logger.warning(
                COS_NARRATIVE_FRAMES_TRUNCATED,
                task_id=task_id,
                execution_id=execution_id,
                frames_scanned=len(frames),
                max_turn_index=aggregate.max_turn_index,
            )
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
            currency=self._currency,
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

        # The per-id fetches are independent; run them concurrently under a
        # TaskGroup so a many-decision brief does not pay N serial round-trips.
        # Each fetch is best-effort: narrative generation must not be sunk by
        # one unreadable decision (a transient backend error or a since-deleted
        # entry), so a failing fetch is logged and dropped rather than
        # propagated to cancel its siblings.
        async def _get_entry(entry_id: NotBlankStr) -> BrainEntry | None:
            try:
                return await self._brain.get_current(
                    project_id=project_id, entry_id=entry_id
                )
            except MemoryError, RecursionError:
                raise
            except Exception as exc:  # noqa: BLE001 -- best-effort fetch: log and drop
                logger.warning(
                    COS_NARRATIVE_DECISION_UNAVAILABLE,
                    project_id=project_id,
                    entry_id=entry_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                return None

        async with asyncio.TaskGroup() as group:
            tasks = [
                group.create_task(_get_entry(entry_id)) for entry_id in decision_ids
            ]
        decisions = tuple(
            entry for task in tasks if (entry := task.result()) is not None
        )
        open_items = tuple(s for s in summaries if s.status in _LIVE_STATUSES)[
            :MAX_OPEN_ITEMS
        ]
        return decisions, open_items


def _tally_agents(
    frames: tuple[FlightRecorderFrame, ...],
) -> tuple[AgentTurnTally, ...]:
    """Roll frames up per agent, ordered by contribution volume.

    Returns:
        Per-agent tallies sorted by descending turn count then agent id.
    """
    counts: dict[str, int] = {}
    costs: dict[str, float] = {}
    tools: dict[str, set[str]] = {}
    for frame in frames:
        agent = frame.agent_id
        counts[agent] = counts.get(agent, 0) + 1
        costs[agent] = costs.get(agent, 0.0) + frame.cost
        seen = tools.setdefault(agent, set())
        seen.update(tool for tool in frame.tool_calls if tool)
    tallies = tuple(
        AgentTurnTally(
            agent_id=NotBlankStr(agent),
            turn_count=counts[agent],
            cost=costs[agent],
            tools=tuple(NotBlankStr(tool) for tool in sorted(tools[agent])),
        )
        for agent in counts
    )
    return tuple(sorted(tallies, key=lambda t: (-t.turn_count, t.agent_id)))
