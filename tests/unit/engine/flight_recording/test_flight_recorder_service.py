"""Unit tests for the flight-recorder query + seek service."""

from datetime import UTC, datetime

import pytest

from synthorg.core.enums import TaskStatus
from synthorg.core.types import NotBlankStr
from synthorg.engine.flight_recording import FlightRecorderService
from synthorg.persistence.flight_recorder_protocol import FlightRecorderFrame
from synthorg.security.redteam.models import (
    RedTeamReport,
    RedTeamReportRecord,
    RedTeamVerdict,
)
from tests.unit.api.fakes import (
    FakeFlightRecorderFrameRepository,
    FakeRedTeamReportArchiveRepository,
)

pytestmark = pytest.mark.unit


def _red_team_record(execution_id: str = "exec-1") -> RedTeamReportRecord:
    return RedTeamReportRecord(
        execution_id=NotBlankStr(execution_id),
        task_id=NotBlankStr("task-1"),
        verdict=RedTeamVerdict.BLOCK,
        report=RedTeamReport(
            execution_id=NotBlankStr(execution_id),
            task_id=NotBlankStr("task-1"),
            summary="Blocked: one HIGH finding.",
        ),
        recorded_at=datetime.now(UTC),
    )


def _frame(
    turn: int, *, execution_id: str = "exec-1", cost: float = 0.5
) -> FlightRecorderFrame:
    return FlightRecorderFrame(
        id=NotBlankStr(f"{execution_id}-{turn}"),
        execution_id=NotBlankStr(execution_id),
        task_id=NotBlankStr("task-1"),
        agent_id=NotBlankStr("agent-1"),
        turn_index=turn,
        timestamp=datetime.now(UTC),
        response_summary=f"reply {turn}",
        decision="completed",
        cost=cost,
        status=TaskStatus.IN_PROGRESS,
    )


async def _seed(repo: FakeFlightRecorderFrameRepository, turns: int) -> None:
    for turn in range(1, turns + 1):
        await repo.append(_frame(turn))


class TestFlightRecorderService:
    async def test_get_frames_newest_first(self) -> None:
        repo = FakeFlightRecorderFrameRepository()
        await _seed(repo, 3)
        service = FlightRecorderService(repo)

        frames = await service.get_frames("exec-1")
        assert [f.turn_index for f in frames] == [3, 2, 1]

    async def test_seek_returns_ascending_prefix(self) -> None:
        repo = FakeFlightRecorderFrameRepository()
        await _seed(repo, 5)
        service = FlightRecorderService(repo)

        view = await service.seek("exec-1", 3)
        assert [f.turn_index for f in view.frames] == [1, 2, 3]
        assert view.current_frame is not None
        assert view.current_frame.turn_index == 3
        assert view.cumulative_cost == pytest.approx(1.5)

    async def test_seek_missing_turn_has_no_current(self) -> None:
        repo = FakeFlightRecorderFrameRepository()
        await _seed(repo, 2)
        service = FlightRecorderService(repo)

        view = await service.seek("exec-1", 5)
        assert view.current_frame is None
        assert [f.turn_index for f in view.frames] == [1, 2]

    async def test_get_red_team_report_returns_archived_record(self) -> None:
        archive = FakeRedTeamReportArchiveRepository()
        await archive.append(_red_team_record())
        service = FlightRecorderService(
            FakeFlightRecorderFrameRepository(), red_team_reports=archive
        )

        record = await service.get_red_team_report("exec-1")
        assert record is not None
        assert record.verdict is RedTeamVerdict.BLOCK
        assert record.execution_id == "exec-1"

    async def test_get_red_team_report_none_when_unrecorded(self) -> None:
        service = FlightRecorderService(
            FakeFlightRecorderFrameRepository(),
            red_team_reports=FakeRedTeamReportArchiveRepository(),
        )
        assert await service.get_red_team_report("absent") is None

    async def test_get_red_team_report_none_when_archive_unwired(self) -> None:
        service = FlightRecorderService(FakeFlightRecorderFrameRepository())
        assert await service.get_red_team_report("exec-1") is None
