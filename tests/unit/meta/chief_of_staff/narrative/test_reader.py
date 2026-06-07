"""Unit tests for the run-narrative reader."""

from collections.abc import Sequence
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
import structlog

from synthorg.core.enums import Priority, TaskStatus, TaskType
from synthorg.core.task import Task
from synthorg.core.types import NotBlankStr
from synthorg.meta.chief_of_staff.narrative.errors import (
    NarrativeSourceUnavailableError,
)
from synthorg.meta.chief_of_staff.narrative.reader import NarrativeReader
from synthorg.observability.events.chief_of_staff import (
    COS_NARRATIVE_DECISION_UNAVAILABLE,
    COS_NARRATIVE_FRAMES_TRUNCATED,
)
from synthorg.persistence.flight_recorder_protocol import (
    FlightRecorderFrame,
    FlightRecorderFrameAggregate,
    FlightRecorderFrameRepository,
)
from synthorg.persistence.task_protocol import TaskRepository
from synthorg.project_brain.models import (
    BrainEntry,
    BrainEntryKind,
    BrainEntryStatus,
    BrainSummary,
    Citation,
    CitationKind,
    DecisionPayload,
)
from synthorg.project_brain.service import ProjectBrainService
from tests._shared import as_uuid, mock_of

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


def _task(task_id: str = "task-1", status: TaskStatus = TaskStatus.COMPLETED) -> Task:
    return Task(
        id=as_uuid(task_id),
        title=NotBlankStr("Ship checkout"),
        description=NotBlankStr("Build the checkout flow end to end."),
        type=TaskType.DEVELOPMENT,
        priority=Priority.MEDIUM,
        project=NotBlankStr("proj-1"),
        created_by=NotBlankStr("manager"),
        assigned_to=NotBlankStr("agent-a"),
        status=status,
    )


def _frame(
    *,
    agent_id: str,
    turn_index: int,
    cost: float = 0.5,
    tools: tuple[str, ...] = (),
) -> FlightRecorderFrame:
    return FlightRecorderFrame(
        execution_id=NotBlankStr("exec-1"),
        task_id=NotBlankStr("task-1"),
        agent_id=NotBlankStr(agent_id),
        turn_index=turn_index,
        timestamp=_NOW,
        tool_calls=tools,
        cost=cost,
        status=TaskStatus.COMPLETED,
    )


def _aggregate(*, execution_id: str | None = "exec-1") -> FlightRecorderFrameAggregate:
    return FlightRecorderFrameAggregate(
        total_cost=3.0,
        max_turn_index=4,
        latest_timestamp=_NOW,
        latest_execution_id=NotBlankStr(execution_id) if execution_id else None,
    )


def _decision_summary() -> BrainSummary:
    return BrainSummary(
        project_id=NotBlankStr("proj-1"),
        entry_id=NotBlankStr("dec-1"),
        revision=1,
        entry_kind=BrainEntryKind.DECISION,
        title=NotBlankStr("Adopt event-sourced ledger"),
        status=BrainEntryStatus.ACCEPTED,
        author=NotBlankStr("agent-a"),
        recorded_at=_NOW,
    )


def _decision_summary_b() -> BrainSummary:
    return BrainSummary(
        project_id=NotBlankStr("proj-1"),
        entry_id=NotBlankStr("dec-2"),
        revision=1,
        entry_kind=BrainEntryKind.DECISION,
        title=NotBlankStr("Use feature flags"),
        status=BrainEntryStatus.ACCEPTED,
        author=NotBlankStr("agent-a"),
        recorded_at=_NOW,
    )


def _open_question_summary() -> BrainSummary:
    return BrainSummary(
        project_id=NotBlankStr("proj-1"),
        entry_id=NotBlankStr("q-1"),
        revision=1,
        entry_kind=BrainEntryKind.OPEN_QUESTION,
        title=NotBlankStr("Which payment provider?"),
        status=BrainEntryStatus.OPEN,
        author=NotBlankStr("agent-b"),
        recorded_at=_NOW,
    )


def _resolved_question_summary() -> BrainSummary:
    return BrainSummary(
        project_id=NotBlankStr("proj-1"),
        entry_id=NotBlankStr("q-2"),
        revision=1,
        entry_kind=BrainEntryKind.OPEN_QUESTION,
        title=NotBlankStr("Settled question"),
        status=BrainEntryStatus.RESOLVED,
        author=NotBlankStr("agent-b"),
        recorded_at=_NOW,
    )


def _decision_entry() -> BrainEntry:
    return BrainEntry(
        entry_id=NotBlankStr("dec-1"),
        revision=1,
        project_id=NotBlankStr("proj-1"),
        entry_kind=BrainEntryKind.DECISION,
        title=NotBlankStr("Adopt event-sourced ledger"),
        rationale=NotBlankStr("Auditability outweighs write amplification."),
        status=BrainEntryStatus.ACCEPTED,
        author=NotBlankStr("agent-a"),
        recorded_at=_NOW,
        related_task_ids=(NotBlankStr("task-1"),),
        citations=(
            Citation(
                source_ref=NotBlankStr("task-1"),
                source_kind=CitationKind.TASK,
            ),
        ),
        payload=DecisionPayload(decision_outcome=NotBlankStr("Event-sourced ledger")),
    )


def _reader(  # noqa: PLR0913 -- test builder with keyword-only knobs
    *,
    task: Task | None,
    aggregate: FlightRecorderFrameAggregate,
    frames: tuple[FlightRecorderFrame, ...] = (),
    summaries: tuple[BrainSummary, ...] = (),
    entry: BrainEntry | None = None,
    entry_by_id: dict[str, BrainEntry | BaseException | None] | None = None,
    query_pages: Sequence[Sequence[FlightRecorderFrame]] | None = None,
    currency: str = "USD",
) -> NarrativeReader:
    task_repo = mock_of[TaskRepository](get=AsyncMock(return_value=task))
    pages = list(query_pages) if query_pages is not None else [frames, ()]
    frame_repo = mock_of[FlightRecorderFrameRepository](
        get_aggregate=AsyncMock(return_value=aggregate),
        query=AsyncMock(side_effect=pages),
    )
    if entry_by_id is not None:

        async def _get(*, project_id: str, entry_id: str) -> BrainEntry | None:
            del project_id
            result = entry_by_id.get(entry_id)
            if isinstance(result, BaseException):
                raise result
            return result

        get_current = AsyncMock(side_effect=_get)
    else:
        get_current = AsyncMock(return_value=entry)
    brain = mock_of[ProjectBrainService](
        list_current=AsyncMock(return_value=summaries),
        get_current=get_current,
    )
    return NarrativeReader(
        frames=frame_repo, brain=brain, task_repo=task_repo, currency=currency
    )


class TestNarrativeReader:
    async def test_unknown_task_raises(self) -> None:
        reader = _reader(task=None, aggregate=_aggregate())
        with pytest.raises(NarrativeSourceUnavailableError):
            await reader.gather(
                task_id=NotBlankStr("task-1"), project_id=NotBlankStr("proj-1")
            )

    async def test_no_frames_raises(self) -> None:
        reader = _reader(task=_task(), aggregate=_aggregate(execution_id=None))
        with pytest.raises(NarrativeSourceUnavailableError):
            await reader.gather(
                task_id=NotBlankStr("task-1"), project_id=NotBlankStr("proj-1")
            )

    async def test_resolves_execution_and_totals(self) -> None:
        reader = _reader(
            task=_task(),
            aggregate=_aggregate(),
            frames=(
                _frame(agent_id="agent-a", turn_index=1, tools=("read",)),
                _frame(agent_id="agent-a", turn_index=2, tools=("write",)),
                _frame(agent_id="agent-b", turn_index=3),
            ),
        )
        inputs = await reader.gather(
            task_id=NotBlankStr("task-1"), project_id=NotBlankStr("proj-1")
        )
        assert inputs.execution_id == "exec-1"
        assert inputs.brief_title == "Ship checkout"
        assert inputs.final_status is TaskStatus.COMPLETED
        assert inputs.total_cost == pytest.approx(3.0)
        assert inputs.total_turns == 4
        assert inputs.frame_count == 3

    async def test_agent_tally_orders_by_volume(self) -> None:
        reader = _reader(
            task=_task(),
            aggregate=_aggregate(),
            frames=(
                _frame(agent_id="agent-a", turn_index=1, tools=("read", "read")),
                _frame(agent_id="agent-a", turn_index=2, tools=("write",)),
                _frame(agent_id="agent-b", turn_index=3),
            ),
        )
        inputs = await reader.gather(
            task_id=NotBlankStr("task-1"), project_id=NotBlankStr("proj-1")
        )
        assert inputs.agent_turns[0].agent_id == "agent-a"
        assert inputs.agent_turns[0].turn_count == 2
        assert inputs.agent_turns[0].tools == ("read", "write")
        assert inputs.agent_turns[1].agent_id == "agent-b"

    async def test_partitions_decisions_and_open_items(self) -> None:
        reader = _reader(
            task=_task(),
            aggregate=_aggregate(),
            frames=(_frame(agent_id="agent-a", turn_index=1),),
            summaries=(
                _decision_summary(),
                _open_question_summary(),
                _resolved_question_summary(),
            ),
            entry=_decision_entry(),
        )
        inputs = await reader.gather(
            task_id=NotBlankStr("task-1"), project_id=NotBlankStr("proj-1")
        )
        assert len(inputs.decisions) == 1
        assert inputs.decisions[0].entry_id == "dec-1"
        open_ids = {s.entry_id for s in inputs.open_items}
        assert open_ids == {"q-1"}

    async def test_gather_brain_skips_vanished_entry(self) -> None:
        # A summary whose full entry was superseded between list_current
        # and get_current (MVCC) is dropped, not raised on or null-filled.
        reader = _reader(
            task=_task(),
            aggregate=_aggregate(),
            frames=(_frame(agent_id="agent-a", turn_index=1),),
            summaries=(_decision_summary(), _decision_summary_b()),
            entry_by_id={"dec-1": _decision_entry(), "dec-2": None},
        )
        inputs = await reader.gather(
            task_id=NotBlankStr("task-1"), project_id=NotBlankStr("proj-1")
        )
        assert {d.entry_id for d in inputs.decisions} == {"dec-1"}

    async def test_gather_brain_tolerates_decision_fetch_error(self) -> None:
        # A transient backend error on one decision fetch is best-effort:
        # it is logged and dropped so the remaining decisions still load,
        # rather than cancelling the whole concurrent TaskGroup.
        reader = _reader(
            task=_task(),
            aggregate=_aggregate(),
            frames=(_frame(agent_id="agent-a", turn_index=1),),
            summaries=(_decision_summary(), _decision_summary_b()),
            entry_by_id={
                "dec-1": _decision_entry(),
                "dec-2": RuntimeError("backend unavailable"),
            },
        )
        with structlog.testing.capture_logs() as events:
            inputs = await reader.gather(
                task_id=NotBlankStr("task-1"), project_id=NotBlankStr("proj-1")
            )
        assert {d.entry_id for d in inputs.decisions} == {"dec-1"}
        assert any(
            e["event"] == COS_NARRATIVE_DECISION_UNAVAILABLE
            and e["entry_id"] == "dec-2"
            for e in events
        )

    async def test_pages_across_multiple_batches(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import synthorg.meta.chief_of_staff.narrative.reader as reader_mod

        monkeypatch.setattr(reader_mod, "FRAME_PAGE_SIZE", 2)
        monkeypatch.setattr(reader_mod, "MAX_FRAMES_SCANNED", 100)
        page1 = (
            _frame(agent_id="a", turn_index=1),
            _frame(agent_id="a", turn_index=2),
        )
        page2 = (_frame(agent_id="a", turn_index=3),)
        reader = _reader(
            task=_task(), aggregate=_aggregate(), query_pages=[page1, page2]
        )
        inputs = await reader.gather(
            task_id=NotBlankStr("task-1"), project_id=NotBlankStr("proj-1")
        )
        # The offset advanced past the first full page to collect the second.
        assert inputs.frame_count == 3

    async def test_frame_scan_capped_and_logged(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import synthorg.meta.chief_of_staff.narrative.reader as reader_mod

        monkeypatch.setattr(reader_mod, "FRAME_PAGE_SIZE", 2)
        monkeypatch.setattr(reader_mod, "MAX_FRAMES_SCANNED", 2)
        full = (
            _frame(agent_id="a", turn_index=1),
            _frame(agent_id="a", turn_index=2),
        )
        reader = _reader(task=_task(), aggregate=_aggregate(), query_pages=[full, full])
        with structlog.testing.capture_logs() as events:
            inputs = await reader.gather(
                task_id=NotBlankStr("task-1"), project_id=NotBlankStr("proj-1")
            )
        assert inputs.frame_count == 2
        assert any(e["event"] == COS_NARRATIVE_FRAMES_TRUNCATED for e in events)

    async def test_currency_stamped_from_constructor(self) -> None:
        reader = _reader(
            task=_task(),
            aggregate=_aggregate(),
            frames=(_frame(agent_id="agent-a", turn_index=1),),
            currency="EUR",
        )
        inputs = await reader.gather(
            task_id=NotBlankStr("task-1"), project_id=NotBlankStr("proj-1")
        )
        assert inputs.currency == "EUR"
