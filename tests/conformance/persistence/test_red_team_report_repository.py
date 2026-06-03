"""Conformance tests for ``RedTeamReportArchiveRepository`` (SQLite + Postgres)."""

from datetime import UTC, datetime, timedelta

import pytest

from synthorg.core.persistence_errors import DuplicateRecordError
from synthorg.core.types import NotBlankStr
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.persistence.red_team_report_protocol import RedTeamReportFilterSpec
from synthorg.security.redteam.models import (
    RedTeamAttackSurface,
    RedTeamFinding,
    RedTeamReport,
    RedTeamReportRecord,
    RedTeamSeverity,
    RedTeamVerdict,
)

pytestmark = pytest.mark.integration


def _record(  # noqa: PLR0913 -- test fixture builder with keyword-only overrides
    *,
    execution_id: str = "exec-001",
    task_id: str = "task-001",
    verdict: RedTeamVerdict = RedTeamVerdict.BLOCK,
    findings: tuple[RedTeamFinding, ...] | None = None,
    summary: str = "Adversarial review complete.",
    recorded_at: datetime | None = None,
) -> RedTeamReportRecord:
    default_findings = (
        RedTeamFinding(
            attack_surface=RedTeamAttackSurface.SECURITY,
            severity=RedTeamSeverity.HIGH,
            description="hardcoded credential in source",
            evidence=("api_key = 'sk-live-123'",),
            suggested_fix="Load the credential from a secret backend.",
        ),
    )
    report = RedTeamReport(
        execution_id=NotBlankStr(execution_id),
        task_id=NotBlankStr(task_id),
        findings=default_findings if findings is None else findings,
        summary=NotBlankStr(summary),
    )
    return RedTeamReportRecord(
        execution_id=NotBlankStr(execution_id),
        task_id=NotBlankStr(task_id),
        verdict=verdict,
        report=report,
        recorded_at=recorded_at or datetime.now(UTC),
    )


class TestRedTeamReportArchiveRepository:
    async def test_append_and_query_by_execution(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.red_team_reports.append(_record())

        page = await backend.red_team_reports.query(
            RedTeamReportFilterSpec(execution_id=NotBlankStr("exec-001")),
        )
        assert len(page) == 1
        record = page[0]
        assert record.execution_id == "exec-001"
        assert record.task_id == "task-001"
        assert record.verdict is RedTeamVerdict.BLOCK
        # The full merged report round-trips through ``report_json``.
        assert len(record.report.findings) == 1
        finding = record.report.findings[0]
        assert finding.attack_surface is RedTeamAttackSurface.SECURITY
        assert finding.severity is RedTeamSeverity.HIGH
        assert finding.evidence == ("api_key = 'sk-live-123'",)

    async def test_append_duplicate_execution_raises(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.red_team_reports.append(_record(execution_id="dup"))
        with pytest.raises(DuplicateRecordError):
            await backend.red_team_reports.append(
                _record(execution_id="dup", verdict=RedTeamVerdict.PASS),
            )

    async def test_query_newest_first_by_recorded_at(
        self, backend: PersistenceBackend
    ) -> None:
        base = datetime.now(UTC)
        await backend.red_team_reports.append(
            _record(execution_id="old", recorded_at=base - timedelta(hours=1)),
        )
        await backend.red_team_reports.append(
            _record(execution_id="new", recorded_at=base),
        )
        page = await backend.red_team_reports.query(RedTeamReportFilterSpec())
        assert [r.execution_id for r in page] == ["new", "old"]

    async def test_query_filters_by_task_and_verdict(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.red_team_reports.append(
            _record(execution_id="e1", task_id="t1", verdict=RedTeamVerdict.BLOCK),
        )
        await backend.red_team_reports.append(
            _record(
                execution_id="e2",
                task_id="t2",
                verdict=RedTeamVerdict.PASS,
                findings=(),
                summary="No findings.",
            ),
        )
        by_task = await backend.red_team_reports.query(
            RedTeamReportFilterSpec(task_id=NotBlankStr("t1")),
        )
        assert {r.execution_id for r in by_task} == {"e1"}
        blocked = await backend.red_team_reports.query(
            RedTeamReportFilterSpec(verdict=RedTeamVerdict.BLOCK),
        )
        assert {r.execution_id for r in blocked} == {"e1"}

    async def test_query_pagination(self, backend: PersistenceBackend) -> None:
        base = datetime.now(UTC)
        for index in range(5):
            await backend.red_team_reports.append(
                _record(
                    execution_id=f"e{index}",
                    recorded_at=base - timedelta(minutes=index),
                ),
            )
        first = await backend.red_team_reports.query(
            RedTeamReportFilterSpec(), limit=2, offset=0
        )
        second = await backend.red_team_reports.query(
            RedTeamReportFilterSpec(), limit=2, offset=2
        )
        assert [r.execution_id for r in first] == ["e0", "e1"]
        assert [r.execution_id for r in second] == ["e2", "e3"]

    async def test_purge_before(self, backend: PersistenceBackend) -> None:
        base = datetime.now(UTC)
        await backend.red_team_reports.append(
            _record(execution_id="stale", recorded_at=base - timedelta(days=2)),
        )
        await backend.red_team_reports.append(
            _record(execution_id="fresh", recorded_at=base),
        )
        removed = await backend.red_team_reports.purge_before(base - timedelta(days=1))
        assert removed == 1
        remaining = await backend.red_team_reports.query(RedTeamReportFilterSpec())
        assert {r.execution_id for r in remaining} == {"fresh"}

    async def test_query_empty_when_no_match(self, backend: PersistenceBackend) -> None:
        page = await backend.red_team_reports.query(
            RedTeamReportFilterSpec(execution_id=NotBlankStr("absent")),
        )
        assert page == ()
