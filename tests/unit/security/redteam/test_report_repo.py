"""Unit tests for ``InMemoryRedTeamReportRepository``."""

import pytest
from structlog.testing import capture_logs

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

    async def test_put_then_get_returns_same_report(self) -> None:
        repo = InMemoryRedTeamReportRepository()
        report = _report()
        await repo.put(execution_id="exec-1", report=report)
        retrieved = await repo.get(execution_id="exec-1")
        assert retrieved is report

    async def test_get_missing_raises(self) -> None:
        repo = InMemoryRedTeamReportRepository()
        with pytest.raises(RedTeamReportNotFoundError) as exc_info:
            await repo.get(execution_id="nope")
        assert exc_info.value.execution_id == "nope"

    async def test_get_missing_stays_log_free(self) -> None:
        # The repo is a pure store: the not-found log is owned by the
        # callers (fail-open gate / degraded receipt), not the repo.
        repo = InMemoryRedTeamReportRepository()
        with capture_logs() as logs, pytest.raises(RedTeamReportNotFoundError):
            await repo.get(execution_id="nope")
        assert logs == []

    async def test_duplicate_put_stays_log_free(self) -> None:
        # The duplicate-submission audit log is owned by the
        # SubmitRedTeamReportTool caller, not the repo.
        repo = InMemoryRedTeamReportRepository()
        await repo.put(execution_id="exec-1", report=_report())
        with capture_logs() as logs, pytest.raises(RedTeamReportAlreadyExistsError):
            await repo.put(execution_id="exec-1", report=_report())
        assert logs == []


@pytest.mark.unit
class TestSingleShot:
    """A second ``put`` for the same execution_id raises."""

    async def test_duplicate_put_rejected(self) -> None:
        repo = InMemoryRedTeamReportRepository()
        await repo.put(execution_id="exec-1", report=_report())
        with pytest.raises(RedTeamReportAlreadyExistsError) as exc_info:
            await repo.put(execution_id="exec-1", report=_report())
        assert exc_info.value.execution_id == "exec-1"

    async def test_different_execution_ids_are_independent(self) -> None:
        repo = InMemoryRedTeamReportRepository()
        await repo.put(execution_id="exec-1", report=_report("exec-1"))
        await repo.put(execution_id="exec-2", report=_report("exec-2"))
        r1 = await repo.get(execution_id="exec-1")
        r2 = await repo.get(execution_id="exec-2")
        assert r1.execution_id == "exec-1"
        assert r2.execution_id == "exec-2"

    async def test_concurrent_puts_same_execution_id_serialize(self) -> None:
        """asyncio.Lock guarantees single-shot under concurrent writers."""
        import asyncio

        repo = InMemoryRedTeamReportRepository()

        async def attempt_put(i: int) -> bool:
            try:
                await repo.put(
                    execution_id="exec-race",
                    report=_report("exec-race", task_id=f"task-{i}"),
                )
            except RedTeamReportAlreadyExistsError:
                return False
            else:
                return True

        results = await asyncio.gather(*[attempt_put(i) for i in range(5)])
        assert sum(results) == 1, f"Expected exactly 1 success, got {sum(results)}"
