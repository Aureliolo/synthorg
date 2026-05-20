"""Unit tests for ``InMemoryRedTeamReportRepository``."""

import pytest

from synthorg.security.redteam.errors import (
    RedTeamReportAlreadyExistsError,
    RedTeamReportNotFoundError,
)
from synthorg.security.redteam.models import RedTeamReport
from synthorg.security.redteam.report_repo import InMemoryRedTeamReportRepository


def _report(execution_id: str = "exec-1", task_id: str = "task-1") -> RedTeamReport:
    return RedTeamReport(
        execution_id=execution_id,
        task_id=task_id,
        summary="clean",
    )


@pytest.mark.unit
class TestPutGet:
    """Round-trip a single report under its execution_id."""

    @pytest.mark.asyncio
    async def test_put_then_get_returns_same_report(self) -> None:
        repo = InMemoryRedTeamReportRepository()
        report = _report()
        await repo.put(execution_id="exec-1", report=report)
        retrieved = await repo.get(execution_id="exec-1")
        assert retrieved is report

    @pytest.mark.asyncio
    async def test_get_missing_raises(self) -> None:
        repo = InMemoryRedTeamReportRepository()
        with pytest.raises(RedTeamReportNotFoundError) as exc_info:
            await repo.get(execution_id="nope")
        assert exc_info.value.execution_id == "nope"


@pytest.mark.unit
class TestSingleShot:
    """A second ``put`` for the same execution_id raises."""

    @pytest.mark.asyncio
    async def test_duplicate_put_rejected(self) -> None:
        repo = InMemoryRedTeamReportRepository()
        await repo.put(execution_id="exec-1", report=_report())
        with pytest.raises(RedTeamReportAlreadyExistsError) as exc_info:
            await repo.put(execution_id="exec-1", report=_report())
        assert exc_info.value.execution_id == "exec-1"

    @pytest.mark.asyncio
    async def test_different_execution_ids_are_independent(self) -> None:
        repo = InMemoryRedTeamReportRepository()
        await repo.put(execution_id="exec-1", report=_report("exec-1"))
        await repo.put(execution_id="exec-2", report=_report("exec-2"))
        r1 = await repo.get(execution_id="exec-1")
        r2 = await repo.get(execution_id="exec-2")
        assert r1.execution_id == "exec-1"
        assert r2.execution_id == "exec-2"
