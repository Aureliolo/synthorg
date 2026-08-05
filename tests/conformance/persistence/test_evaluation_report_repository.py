"""Conformance tests for ``EvaluationReportRepository`` (SQLite + Postgres)."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from synthorg.core.evaluation_verdict import CriterionOutcome, CriterionVerdict
from synthorg.core.persistence_errors import DuplicateRecordError, QueryError
from synthorg.core.types import NotBlankStr
from synthorg.persistence.evaluation_report_protocol import (
    EvaluationReportFilterSpec,
    EvaluationReportRecord,
)
from synthorg.persistence.protocol import PersistenceBackend
from tests._shared import as_uuid

pytestmark = pytest.mark.integration


def _verdict(
    criterion: str = "Rotation follows SRS",
    outcome: CriterionOutcome = CriterionOutcome.MET,
) -> CriterionVerdict:
    return CriterionVerdict(
        criterion=NotBlankStr(criterion),
        outcome=outcome,
        evidence=NotBlankStr("The suite covering wall kicks passes."),
    )


def _record(
    *,
    record_id: UUID | None = None,
    plan_id: str = "plan-001",
    project_id: str = "proj-001",
    attempt: int = 1,
    summary: str = "Read the workspace and checked each criterion.",
    verdicts: tuple[CriterionVerdict, ...] = (),
    objective_met: bool = True,
    evaluated_at: datetime | None = None,
) -> EvaluationReportRecord:
    return EvaluationReportRecord(
        record_id=record_id or as_uuid("evaluation-report"),
        plan_id=NotBlankStr(plan_id),
        project_id=NotBlankStr(project_id),
        attempt=attempt,
        summary=NotBlankStr(summary),
        verdicts=verdicts or (_verdict(),),
        objective_met=objective_met,
        evaluated_at=evaluated_at or datetime.now(UTC),
    )


class TestEvaluationReportRepository:
    async def test_append_and_query_round_trip(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.evaluation_reports.append(_record())

        page = await backend.evaluation_reports.query(
            EvaluationReportFilterSpec(plan_id=NotBlankStr("plan-001")),
        )
        assert len(page) == 1
        record = page[0]
        assert record.attempt == 1
        assert record.objective_met is True
        assert record.verdicts[0].outcome is CriterionOutcome.MET
        assert record.verdicts[0].criterion == "Rotation follows SRS"

    async def test_unmet_verdicts_round_trip(self, backend: PersistenceBackend) -> None:
        await backend.evaluation_reports.append(
            _record(
                verdicts=(
                    _verdict("A", CriterionOutcome.MET),
                    _verdict("B", CriterionOutcome.PARTIAL),
                    _verdict("C", CriterionOutcome.UNMET),
                ),
                objective_met=False,
            ),
        )
        page = await backend.evaluation_reports.query(EvaluationReportFilterSpec())
        assert page[0].objective_met is False
        assert [v.outcome for v in page[0].verdicts] == [
            CriterionOutcome.MET,
            CriterionOutcome.PARTIAL,
            CriterionOutcome.UNMET,
        ]

    async def test_query_returns_newest_attempt_first(
        self, backend: PersistenceBackend
    ) -> None:
        earlier = datetime.now(UTC) - timedelta(hours=1)
        await backend.evaluation_reports.append(
            _record(
                record_id=as_uuid("attempt-1"),
                attempt=1,
                objective_met=False,
                evaluated_at=earlier,
            ),
        )
        await backend.evaluation_reports.append(
            _record(record_id=as_uuid("attempt-2"), attempt=2),
        )
        page = await backend.evaluation_reports.query(
            EvaluationReportFilterSpec(plan_id=NotBlankStr("plan-001")),
        )
        assert [r.attempt for r in page] == [2, 1]

    async def test_query_by_project(self, backend: PersistenceBackend) -> None:
        await backend.evaluation_reports.append(
            _record(
                record_id=as_uuid("a"),
                plan_id="plan-a",
                project_id="proj-a",
            ),
        )
        await backend.evaluation_reports.append(
            _record(
                record_id=as_uuid("b"),
                plan_id="plan-b",
                project_id="proj-b",
            ),
        )
        page = await backend.evaluation_reports.query(
            EvaluationReportFilterSpec(project_id=NotBlankStr("proj-b")),
        )
        assert [r.plan_id for r in page] == ["plan-b"]

    async def test_append_duplicate_id_raises(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.evaluation_reports.append(_record())
        with pytest.raises(DuplicateRecordError):
            await backend.evaluation_reports.append(_record(plan_id="plan-other"))

    async def test_append_duplicate_attempt_raises(
        self, backend: PersistenceBackend
    ) -> None:
        # A judgement is a historical fact: re-judging the same attempt must
        # not overwrite the evidence the replan points at.
        await backend.evaluation_reports.append(_record(record_id=as_uuid("first")))
        with pytest.raises(DuplicateRecordError):
            await backend.evaluation_reports.append(
                _record(record_id=as_uuid("second"), objective_met=False),
            )

    async def test_purge_before(self, backend: PersistenceBackend) -> None:
        old = datetime.now(UTC) - timedelta(days=2)
        await backend.evaluation_reports.append(
            _record(record_id=as_uuid("old"), attempt=1, evaluated_at=old),
        )
        await backend.evaluation_reports.append(
            _record(record_id=as_uuid("new"), attempt=2),
        )
        removed = await backend.evaluation_reports.purge_before(
            datetime.now(UTC) - timedelta(days=1),
        )
        assert removed == 1
        page = await backend.evaluation_reports.query(EvaluationReportFilterSpec())
        assert [r.attempt for r in page] == [2]

    async def test_purge_before_rejects_naive_threshold(
        self, backend: PersistenceBackend
    ) -> None:
        with pytest.raises(QueryError):
            await backend.evaluation_reports.purge_before(
                datetime(2026, 1, 1),  # noqa: DTZ001 -- naive on purpose
            )
