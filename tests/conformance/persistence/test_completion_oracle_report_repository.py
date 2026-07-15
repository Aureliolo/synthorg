"""Conformance tests for ``CompletionOracleReportArchiveRepository``.

Runs against both backends (SQLite + Postgres) via the ``backend`` fixture.
"""

from datetime import UTC, datetime, timedelta

import pytest

from synthorg.core.persistence_errors import DuplicateRecordError, QueryError
from synthorg.core.types import NotBlankStr
from synthorg.engine.completion_oracle.review_models import (
    CompletionOracleReport,
    CompletionOracleReportRecord,
    CompletionOracleVerdict,
)
from synthorg.persistence.completion_oracle_report_protocol import (
    CompletionOracleReportFilterSpec,
)
from synthorg.persistence.protocol import PersistenceBackend

pytestmark = pytest.mark.integration


def _record(  # noqa: PLR0913 -- test fixture builder with keyword-only overrides
    *,
    execution_id: str = "exec-001",
    task_id: str = "task-001",
    reviewer_agent_id: str = "completion-reviewer",
    executor_agent_id: str = "executor-1",
    verdict: CompletionOracleVerdict = CompletionOracleVerdict.REJECT,
    summary: str = "Independent review complete.",
    recorded_at: datetime | None = None,
) -> CompletionOracleReportRecord:
    report = CompletionOracleReport(
        execution_id=NotBlankStr(execution_id),
        task_id=NotBlankStr(task_id),
        reviewer_agent_id=NotBlankStr(reviewer_agent_id),
        executor_agent_id=NotBlankStr(executor_agent_id),
        verdict=verdict,
        summary=NotBlankStr(summary),
    )
    return CompletionOracleReportRecord(
        execution_id=NotBlankStr(execution_id),
        task_id=NotBlankStr(task_id),
        verdict=verdict,
        report=report,
        recorded_at=recorded_at or datetime.now(UTC),
    )


class TestCompletionOracleReportArchiveRepository:
    async def test_append_and_query_by_execution(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.completion_oracle_reports.append(_record())

        page = await backend.completion_oracle_reports.query(
            CompletionOracleReportFilterSpec(execution_id=NotBlankStr("exec-001")),
        )
        assert len(page) == 1
        record = page[0]
        assert record.execution_id == "exec-001"
        assert record.verdict is CompletionOracleVerdict.REJECT
        # The reviewer / executor identities round-trip through report_json.
        assert record.report.reviewer_agent_id == "completion-reviewer"
        assert record.report.executor_agent_id == "executor-1"

    async def test_append_duplicate_execution_raises(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.completion_oracle_reports.append(_record(execution_id="dup"))
        with pytest.raises(DuplicateRecordError):
            await backend.completion_oracle_reports.append(
                _record(execution_id="dup", verdict=CompletionOracleVerdict.APPROVE),
            )

    async def test_query_newest_first_by_recorded_at(
        self, backend: PersistenceBackend
    ) -> None:
        base = datetime.now(UTC)
        await backend.completion_oracle_reports.append(
            _record(execution_id="old", recorded_at=base - timedelta(hours=1)),
        )
        await backend.completion_oracle_reports.append(
            _record(execution_id="new", recorded_at=base),
        )
        # Two records sharing a timestamp exercise the execution_id DESC
        # tie-breaker required by the ORDER BY contract.
        await backend.completion_oracle_reports.append(
            _record(execution_id="tie-a", recorded_at=base + timedelta(hours=1)),
        )
        await backend.completion_oracle_reports.append(
            _record(execution_id="tie-b", recorded_at=base + timedelta(hours=1)),
        )
        page = await backend.completion_oracle_reports.query(
            CompletionOracleReportFilterSpec()
        )
        assert [r.execution_id for r in page] == ["tie-b", "tie-a", "new", "old"]

    async def test_query_filters_by_task_and_verdict(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.completion_oracle_reports.append(
            _record(
                execution_id="e1", task_id="t1", verdict=CompletionOracleVerdict.REJECT
            ),
        )
        await backend.completion_oracle_reports.append(
            _record(
                execution_id="e2",
                task_id="t2",
                verdict=CompletionOracleVerdict.APPROVE,
                summary="Approved.",
            ),
        )
        by_task = await backend.completion_oracle_reports.query(
            CompletionOracleReportFilterSpec(task_id=NotBlankStr("t1")),
        )
        assert {r.execution_id for r in by_task} == {"e1"}
        rejected = await backend.completion_oracle_reports.query(
            CompletionOracleReportFilterSpec(verdict=CompletionOracleVerdict.REJECT),
        )
        assert {r.execution_id for r in rejected} == {"e1"}

    async def test_purge_before(self, backend: PersistenceBackend) -> None:
        base = datetime.now(UTC)
        await backend.completion_oracle_reports.append(
            _record(execution_id="stale", recorded_at=base - timedelta(days=2)),
        )
        await backend.completion_oracle_reports.append(
            _record(execution_id="fresh", recorded_at=base),
        )
        removed = await backend.completion_oracle_reports.purge_before(
            base - timedelta(days=1)
        )
        assert removed == 1
        remaining = await backend.completion_oracle_reports.query(
            CompletionOracleReportFilterSpec()
        )
        assert {r.execution_id for r in remaining} == {"fresh"}

    async def test_purge_before_rejects_naive(
        self, backend: PersistenceBackend
    ) -> None:
        with pytest.raises(QueryError):
            await backend.completion_oracle_reports.purge_before(
                datetime(2025, 1, 1),  # noqa: DTZ001 -- naive on purpose
            )
