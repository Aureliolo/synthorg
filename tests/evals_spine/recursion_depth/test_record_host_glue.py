# module-kind: tests
"""The recording script's host glue files a leaf and reads its verdict back.

Three smoke attempts stopped on this seam (a row refused at the entry hop, a
verdict archived under an execution id the harness never minted, a status
field read from the wrong row) while every suite stayed green, because
nothing exercised the closures that bind the harness to the host.
"""

from types import SimpleNamespace

import pytest
from scripts import record_recursion_depth as record_module
from structlog.testing import capture_logs

from synthorg.core.task import Task
from synthorg.core.task_enums import Priority, TaskStatus, TaskType
from synthorg.core.types import NotBlankStr
from synthorg.engine.completion_oracle.review_models import CompletionOracleVerdict
from synthorg.observability.events.evals import (
    EVALS_RECURSION_HOST_REVIEW_READ,
    EVALS_RECURSION_HOST_TASK_FILED,
)
from synthorg.persistence.completion_oracle_report_protocol import (
    CompletionOracleReportFilterSpec,
)
from tests._shared import as_uuid, make_app_state

pytestmark = pytest.mark.unit


def _task() -> Task:
    return Task(
        id=as_uuid("leaf-glue"),
        title=NotBlankStr("Reviewed leaf"),
        description=NotBlankStr("Build the unit"),
        type=TaskType.DEVELOPMENT,
        priority=Priority.MEDIUM,
        project=NotBlankStr("p1"),
        created_by=NotBlankStr("sweep"),
    )


class _FakeTaskEngine:
    def __init__(self, held: Task | None) -> None:
        self.filed: list[tuple[Task, ...]] = []
        self._held = held

    async def file_tasks(self, tasks: tuple[Task, ...]) -> None:
        self.filed.append(tasks)

    async def get_task(self, task_id: str) -> Task | None:
        assert task_id == str(as_uuid("leaf-glue"))
        return self._held


class _FakeArchive:
    def __init__(self, verdicts: tuple[CompletionOracleVerdict, ...]) -> None:
        self.queries: list[tuple[CompletionOracleReportFilterSpec, int]] = []
        self._verdicts = verdicts

    async def query(
        self, spec: CompletionOracleReportFilterSpec, *, limit: int
    ) -> list[SimpleNamespace]:
        self.queries.append((spec, limit))
        return [SimpleNamespace(verdict=v) for v in self._verdicts[:limit]]


def _bind(
    monkeypatch: pytest.MonkeyPatch, engine: _FakeTaskEngine, archive: _FakeArchive
) -> None:
    monkeypatch.setattr(record_module, "task_engine_of", lambda _state: engine)
    monkeypatch.setattr(
        record_module,
        "persistence_of",
        lambda _state: SimpleNamespace(completion_oracle_reports=archive),
    )


class TestTheTaskFiler:
    async def test_files_the_task_through_the_host_engine(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        engine = _FakeTaskEngine(held=None)
        _bind(monkeypatch, engine, _FakeArchive(()))
        task = _task()

        with capture_logs() as logs:
            await record_module._task_filer(make_app_state())(task)

        assert engine.filed == [(task,)]
        filed = [log for log in logs if log["event"] == EVALS_RECURSION_HOST_TASK_FILED]
        assert filed[0]["task_id"] == str(task.id)


class TestTheReviewReader:
    async def test_reads_the_row_and_the_newest_verdict(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        task = _task().model_copy(update={"status": TaskStatus.COMPLETED})
        archive = _FakeArchive(
            (CompletionOracleVerdict.APPROVE, CompletionOracleVerdict.REJECT)
        )
        _bind(monkeypatch, _FakeTaskEngine(held=task), archive)

        with capture_logs() as logs:
            review = await record_module._review_reader(make_app_state())(str(task.id))

        assert review.task_status is TaskStatus.COMPLETED
        assert review.verdict is CompletionOracleVerdict.APPROVE
        spec, limit = archive.queries[0]
        assert spec.task_id == str(task.id)
        assert limit == 1
        read = [log for log in logs if log["event"] == EVALS_RECURSION_HOST_REVIEW_READ]
        assert read[0]["archived_reports"] == 1

    async def test_no_row_and_no_report_read_as_unreviewed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _bind(monkeypatch, _FakeTaskEngine(held=None), _FakeArchive(()))

        review = await record_module._review_reader(make_app_state())(
            str(as_uuid("leaf-glue"))
        )

        assert review.task_status is None
        assert review.verdict is None
