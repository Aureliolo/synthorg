"""Conformance tests for ``EvaluationReportRepository`` (SQLite + Postgres)."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from synthorg.core.evaluation_verdict import CriterionOutcome, CriterionVerdict
from synthorg.core.persistence_errors import DuplicateRecordError, QueryError
from synthorg.core.plan import Plan, PlanItem
from synthorg.core.types import NotBlankStr
from synthorg.persistence.evaluation_report_protocol import (
    EvaluationReportFilterSpec,
    EvaluationReportRecord,
)
from synthorg.persistence.protocol import PersistenceBackend
from tests._shared import as_uuid, sid
from tests.unit.persistence.conftest import make_task

pytestmark = pytest.mark.integration

#: The objective task every plan here decomposes. ``plans.parent_task_id``
#: is a foreign key, so the parent has to exist before any plan naming it.
_PARENT_TASK_ID = "parent-1"


@pytest.fixture(autouse=True)
async def _parent_task(backend: PersistenceBackend) -> None:
    """Persist the objective task the plans in this module point at."""
    await backend.tasks.save(make_task(task_id=_PARENT_TASK_ID, title="Ship it"))


async def _seed_plans(backend: PersistenceBackend, *plan_ids: str) -> None:
    """Persist the plans a verdict row references.

    A verdict cascades with its plan, so every fixture row needs one; the
    alternative is orphaned judgements about initiatives nothing can read.
    """
    now = datetime.now(UTC)
    for plan_id in plan_ids:
        await backend.plans.save(
            Plan(
                id=as_uuid(plan_id),
                project=NotBlankStr("proj-001"),
                objective_id=NotBlankStr("obj-1"),
                objective_title=NotBlankStr("Ship it"),
                parent_task_id=NotBlankStr(sid(_PARENT_TASK_ID)),
                created_at=now,
                updated_at=now,
                items=(
                    PlanItem(
                        id=NotBlankStr("11111111-1111-5111-8111-111111111111"),
                        title=NotBlankStr("Build"),
                        description=NotBlankStr("Do the work"),
                        acceptance_criteria=(NotBlankStr("it is done"),),
                        expected_artifacts=(NotBlankStr("src/work.py"),),
                    ),
                ),
            ),
        )


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
        plan_id=NotBlankStr(sid(plan_id)),
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
        await _seed_plans(backend, "plan-001")
        await backend.evaluation_reports.append(_record())

        page = await backend.evaluation_reports.query(
            EvaluationReportFilterSpec(plan_id=NotBlankStr(sid("plan-001"))),
        )
        assert len(page) == 1
        record = page[0]
        assert record.attempt == 1
        assert record.objective_met is True
        assert record.verdicts[0].outcome is CriterionOutcome.MET
        assert record.verdicts[0].criterion == "Rotation follows SRS"

    async def test_unmet_verdicts_round_trip(self, backend: PersistenceBackend) -> None:
        await _seed_plans(backend, "plan-001")
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
        await _seed_plans(backend, "plan-001")
        earlier = datetime.now(UTC) - timedelta(hours=1)
        await backend.evaluation_reports.append(
            _record(
                record_id=as_uuid("attempt-1"),
                attempt=1,
                verdicts=(_verdict("A", CriterionOutcome.UNMET),),
                objective_met=False,
                evaluated_at=earlier,
            ),
        )
        await backend.evaluation_reports.append(
            _record(record_id=as_uuid("attempt-2"), attempt=2),
        )
        page = await backend.evaluation_reports.query(
            EvaluationReportFilterSpec(plan_id=NotBlankStr(sid("plan-001"))),
        )
        assert [r.attempt for r in page] == [2, 1]

    async def test_query_by_project(self, backend: PersistenceBackend) -> None:
        await _seed_plans(backend, "plan-a", "plan-b")
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
        assert [r.plan_id for r in page] == [sid("plan-b")]

    async def test_append_duplicate_id_raises(
        self, backend: PersistenceBackend
    ) -> None:
        await _seed_plans(backend, "plan-001", "plan-other")
        await backend.evaluation_reports.append(_record())
        with pytest.raises(DuplicateRecordError):
            await backend.evaluation_reports.append(_record(plan_id="plan-other"))

    async def test_append_duplicate_attempt_raises(
        self, backend: PersistenceBackend
    ) -> None:
        # A judgement is a historical fact: re-judging the same attempt must
        # not overwrite the evidence the replan points at.
        await _seed_plans(backend, "plan-001")
        await backend.evaluation_reports.append(_record(record_id=as_uuid("first")))
        with pytest.raises(DuplicateRecordError):
            await backend.evaluation_reports.append(
                _record(
                    record_id=as_uuid("second"),
                    verdicts=(_verdict("A", CriterionOutcome.UNMET),),
                    objective_met=False,
                ),
            )

    async def test_deleting_the_plan_takes_its_verdicts(
        self, backend: PersistenceBackend
    ) -> None:
        # A verdict about a plan that no longer exists is unreadable by
        # anything, so it goes with the plan rather than orphaning forever.
        await _seed_plans(backend, "plan-001")
        await backend.evaluation_reports.append(_record())

        assert await backend.plans.delete(NotBlankStr(sid("plan-001"))) is True

        page = await backend.evaluation_reports.query(EvaluationReportFilterSpec())
        assert page == ()

    async def test_purge_before(self, backend: PersistenceBackend) -> None:
        await _seed_plans(backend, "plan-001")
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
