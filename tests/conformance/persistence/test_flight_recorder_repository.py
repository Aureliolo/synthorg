"""Conformance tests for ``FlightRecorderFrameRepository`` (SQLite + Postgres)."""

from datetime import UTC, datetime, timedelta

import pytest

from synthorg.core.enums import InterventionKind, TaskStatus
from synthorg.core.persistence_errors import DuplicateRecordError
from synthorg.core.types import NotBlankStr
from synthorg.persistence.flight_recorder_protocol import (
    FlightRecorderFrame,
    FlightRecorderFrameFilterSpec,
)
from synthorg.persistence.protocol import PersistenceBackend

pytestmark = pytest.mark.integration


def _frame(  # noqa: PLR0913 -- test fixture builder with keyword-only overrides
    *,
    frame_id: str = "frm-001",
    execution_id: str = "exec-001",
    task_id: str | None = "task-001",
    agent_id: str = "agent-001",
    turn_index: int = 1,
    status: TaskStatus = TaskStatus.IN_PROGRESS,
    intervention_kind: InterventionKind | None = None,
    timestamp: datetime | None = None,
) -> FlightRecorderFrame:
    return FlightRecorderFrame(
        id=NotBlankStr(frame_id),
        execution_id=NotBlankStr(execution_id),
        task_id=NotBlankStr(task_id) if task_id is not None else None,
        agent_id=NotBlankStr(agent_id),
        turn_index=turn_index,
        timestamp=timestamp or datetime.now(UTC),
        prompt_summary="redacted prompt",
        response_summary="redacted response",
        decision="tool_call",
        tool_calls=("search", "write_file"),
        input_tokens=120,
        output_tokens=45,
        cost=0.0021,
        status=status,
        intervention_kind=intervention_kind,
    )


class TestFlightRecorderFrameRepository:
    async def test_append_and_query(self, backend: PersistenceBackend) -> None:
        await backend.flight_recorder_frames.append(_frame())

        page = await backend.flight_recorder_frames.query(
            FlightRecorderFrameFilterSpec(execution_id=NotBlankStr("exec-001")),
        )
        assert len(page) == 1
        frame = page[0]
        assert frame.id == "frm-001"
        assert frame.tool_calls == ("search", "write_file")
        assert frame.cost == pytest.approx(0.0021)
        assert frame.status is TaskStatus.IN_PROGRESS

    async def test_append_duplicate_id_raises(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.flight_recorder_frames.append(_frame(frame_id="dup"))
        with pytest.raises(DuplicateRecordError):
            await backend.flight_recorder_frames.append(
                _frame(frame_id="dup", turn_index=2),
            )

    async def test_query_newest_first_by_turn(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.flight_recorder_frames.append(_frame(frame_id="a", turn_index=1))
        await backend.flight_recorder_frames.append(_frame(frame_id="c", turn_index=3))
        await backend.flight_recorder_frames.append(_frame(frame_id="b", turn_index=2))

        page = await backend.flight_recorder_frames.query(
            FlightRecorderFrameFilterSpec(execution_id=NotBlankStr("exec-001")),
        )
        assert [f.turn_index for f in page] == [3, 2, 1]

    async def test_query_paginates(self, backend: PersistenceBackend) -> None:
        for turn in range(1, 6):
            await backend.flight_recorder_frames.append(
                _frame(frame_id=f"f-{turn}", turn_index=turn),
            )
        page = await backend.flight_recorder_frames.query(
            FlightRecorderFrameFilterSpec(execution_id=NotBlankStr("exec-001")),
            limit=2,
            offset=0,
        )
        assert [f.turn_index for f in page] == [5, 4]

    async def test_query_filters_by_turn_range(
        self, backend: PersistenceBackend
    ) -> None:
        for turn in range(1, 6):
            await backend.flight_recorder_frames.append(
                _frame(frame_id=f"f-{turn}", turn_index=turn),
            )
        page = await backend.flight_recorder_frames.query(
            FlightRecorderFrameFilterSpec(
                execution_id=NotBlankStr("exec-001"),
                turn_index_min=2,
                turn_index_max=4,
            ),
        )
        assert [f.turn_index for f in page] == [4, 3, 2]

    async def test_query_filters_by_agent(self, backend: PersistenceBackend) -> None:
        await backend.flight_recorder_frames.append(
            _frame(frame_id="a", agent_id="alice", turn_index=1),
        )
        await backend.flight_recorder_frames.append(
            _frame(frame_id="b", agent_id="bob", turn_index=2),
        )
        page = await backend.flight_recorder_frames.query(
            FlightRecorderFrameFilterSpec(agent_id=NotBlankStr("bob")),
        )
        assert len(page) == 1
        assert page[0].agent_id == "bob"

    async def test_intervention_kind_round_trips(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.flight_recorder_frames.append(
            _frame(frame_id="hint", intervention_kind=InterventionKind.HINT),
        )
        page = await backend.flight_recorder_frames.query(
            FlightRecorderFrameFilterSpec(execution_id=NotBlankStr("exec-001")),
        )
        assert page[0].intervention_kind is InterventionKind.HINT

    async def test_null_task_id_round_trips(self, backend: PersistenceBackend) -> None:
        await backend.flight_recorder_frames.append(
            _frame(frame_id="no-task", task_id=None),
        )
        page = await backend.flight_recorder_frames.query(
            FlightRecorderFrameFilterSpec(execution_id=NotBlankStr("exec-001")),
        )
        assert page[0].task_id is None

    async def test_purge_before_removes_old_frames(
        self, backend: PersistenceBackend
    ) -> None:
        now = datetime.now(UTC)
        await backend.flight_recorder_frames.append(
            _frame(frame_id="old", turn_index=1, timestamp=now - timedelta(days=30)),
        )
        await backend.flight_recorder_frames.append(
            _frame(frame_id="new", turn_index=2, timestamp=now),
        )
        removed = await backend.flight_recorder_frames.purge_before(
            now - timedelta(days=1),
        )
        assert removed == 1
        page = await backend.flight_recorder_frames.query(
            FlightRecorderFrameFilterSpec(execution_id=NotBlankStr("exec-001")),
        )
        assert [f.id for f in page] == ["new"]
