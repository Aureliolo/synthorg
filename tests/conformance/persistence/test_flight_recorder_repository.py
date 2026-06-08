"""Conformance tests for ``FlightRecorderFrameRepository`` (SQLite + Postgres)."""

from datetime import UTC, datetime, timedelta

import pytest

from synthorg.core.persistence_errors import DuplicateRecordError, QueryError
from synthorg.core.task_enums import TaskStatus
from synthorg.core.types import NotBlankStr
from synthorg.engine.intervention.enums import InterventionKind
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

    async def test_purge_before_rejects_naive(
        self, backend: PersistenceBackend
    ) -> None:
        with pytest.raises(QueryError):
            await backend.flight_recorder_frames.purge_before(
                datetime(2025, 1, 1),  # noqa: DTZ001 -- naive on purpose
            )

    async def test_append_many_batches_atomically(
        self, backend: PersistenceBackend
    ) -> None:
        frames = (
            _frame(frame_id="b-1", turn_index=1),
            _frame(frame_id="b-2", turn_index=2),
            _frame(frame_id="b-3", turn_index=3),
        )
        await backend.flight_recorder_frames.append_many(frames)
        page = await backend.flight_recorder_frames.query(
            FlightRecorderFrameFilterSpec(execution_id=NotBlankStr("exec-001")),
        )
        assert {f.id for f in page} == {"b-1", "b-2", "b-3"}

    async def test_append_many_empty_is_noop(self, backend: PersistenceBackend) -> None:
        await backend.flight_recorder_frames.append_many(())
        page = await backend.flight_recorder_frames.query(
            FlightRecorderFrameFilterSpec(execution_id=NotBlankStr("exec-001")),
        )
        assert page == ()

    async def test_append_many_duplicate_rolls_back(
        self, backend: PersistenceBackend
    ) -> None:
        # Pre-seed one frame, then attempt a batch that collides on id.
        await backend.flight_recorder_frames.append(
            _frame(frame_id="seed", turn_index=1),
        )
        with pytest.raises(DuplicateRecordError):
            await backend.flight_recorder_frames.append_many(
                (
                    _frame(frame_id="new-1", turn_index=2),
                    _frame(frame_id="seed", turn_index=3),  # duplicate id
                ),
            )
        # Rollback: neither ``new-1`` nor ``seed``'s replacement made it in.
        page = await backend.flight_recorder_frames.query(
            FlightRecorderFrameFilterSpec(execution_id=NotBlankStr("exec-001")),
        )
        assert {f.id for f in page} == {"seed"}

    async def test_unique_execution_turn_blocks_duplicate_turn(
        self, backend: PersistenceBackend
    ) -> None:
        # Two frames with different ids but the same (execution_id, turn_index)
        # must be rejected; the UNIQUE index guarantees a deterministic
        # ``seek(turn N)`` reconstruction.
        await backend.flight_recorder_frames.append(_frame(frame_id="t1", turn_index=5))
        with pytest.raises(DuplicateRecordError):
            await backend.flight_recorder_frames.append(
                _frame(frame_id="t1-dup", turn_index=5),
            )

    async def test_get_aggregate_sums_cost_and_picks_latest(
        self, backend: PersistenceBackend
    ) -> None:
        now = datetime.now(UTC)
        for turn, cost in ((1, 0.5), (2, 1.0), (3, 1.5)):
            await backend.flight_recorder_frames.append(
                _frame(
                    frame_id=f"a-{turn}",
                    turn_index=turn,
                    timestamp=now + timedelta(seconds=turn),
                ).model_copy(update={"cost": cost}),
            )
        aggregate = await backend.flight_recorder_frames.get_aggregate(
            FlightRecorderFrameFilterSpec(task_id=NotBlankStr("task-001")),
        )
        assert aggregate.total_cost == pytest.approx(3.0)
        assert aggregate.max_turn_index == 3
        assert aggregate.latest_execution_id == "exec-001"
        assert aggregate.latest_timestamp is not None

    async def test_get_aggregate_empty_set_returns_zeros(
        self, backend: PersistenceBackend
    ) -> None:
        aggregate = await backend.flight_recorder_frames.get_aggregate(
            FlightRecorderFrameFilterSpec(task_id=NotBlankStr("does-not-exist")),
        )
        assert aggregate.total_cost == 0.0
        assert aggregate.max_turn_index == 0
        assert aggregate.latest_timestamp is None
        assert aggregate.latest_execution_id is None
