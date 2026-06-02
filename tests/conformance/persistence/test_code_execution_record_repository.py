"""Conformance tests for ``CodeExecutionRecordRepository`` (SQLite + Postgres)."""

from datetime import UTC, datetime, timedelta

import pytest

from synthorg.core.persistence_errors import DuplicateRecordError
from synthorg.core.types import NotBlankStr
from synthorg.persistence.code_execution_protocol import (
    CodeExecutionFilterSpec,
    CodeExecutionPurpose,
    CodeExecutionRecord,
)
from synthorg.persistence.protocol import PersistenceBackend

pytestmark = pytest.mark.integration


def _record(  # noqa: PLR0913 -- keyword-only test builder
    *,
    record_id: str = "cer-001",
    task_id: str = "task-001",
    execution_id: str = "exec-001",
    project_id: str = "proj-001",
    purpose: CodeExecutionPurpose = CodeExecutionPurpose.TESTS,
    command: str = "python -m pytest",
    returncode: int = 0,
    passed: bool = True,
    timed_out: bool = False,
    stdout_tail: str | None = "5 passed",
    executed_at: datetime | None = None,
) -> CodeExecutionRecord:
    return CodeExecutionRecord(
        record_id=NotBlankStr(record_id),
        task_id=NotBlankStr(task_id),
        execution_id=NotBlankStr(execution_id),
        project_id=NotBlankStr(project_id),
        purpose=purpose,
        command=NotBlankStr(command),
        returncode=returncode,
        passed=passed,
        timed_out=timed_out,
        stdout_tail=stdout_tail,
        stderr_tail=None,
        executed_at=executed_at or datetime.now(UTC),
    )


class TestCodeExecutionRecordRepository:
    async def test_append_and_query_round_trip(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.code_execution_records.append(_record())

        page = await backend.code_execution_records.query(
            CodeExecutionFilterSpec(execution_id=NotBlankStr("exec-001")),
        )
        assert len(page) == 1
        rec = page[0]
        assert rec.passed is True
        assert rec.timed_out is False
        assert rec.returncode == 0
        assert rec.purpose is CodeExecutionPurpose.TESTS
        assert rec.stdout_tail == "5 passed"

    async def test_failed_run_round_trip(self, backend: PersistenceBackend) -> None:
        await backend.code_execution_records.append(
            _record(record_id="f", returncode=1, passed=False),
        )
        page = await backend.code_execution_records.query(
            CodeExecutionFilterSpec(execution_id=NotBlankStr("exec-001")),
        )
        assert page[0].passed is False
        assert page[0].returncode == 1

    async def test_query_by_purpose(self, backend: PersistenceBackend) -> None:
        await backend.code_execution_records.append(
            _record(record_id="t", purpose=CodeExecutionPurpose.TESTS),
        )
        await backend.code_execution_records.append(
            _record(record_id="g", purpose=CodeExecutionPurpose.GENERAL),
        )
        page = await backend.code_execution_records.query(
            CodeExecutionFilterSpec(purpose=CodeExecutionPurpose.TESTS),
        )
        assert [r.record_id for r in page] == ["t"]

    async def test_query_by_task_and_project(self, backend: PersistenceBackend) -> None:
        await backend.code_execution_records.append(
            _record(record_id="a", task_id="task-a", project_id="proj-a"),
        )
        await backend.code_execution_records.append(
            _record(record_id="b", task_id="task-b", project_id="proj-b"),
        )
        by_task = await backend.code_execution_records.query(
            CodeExecutionFilterSpec(task_id=NotBlankStr("task-a")),
        )
        assert [r.record_id for r in by_task] == ["a"]
        by_project = await backend.code_execution_records.query(
            CodeExecutionFilterSpec(project_id=NotBlankStr("proj-b")),
        )
        assert [r.record_id for r in by_project] == ["b"]

    async def test_append_duplicate_id_raises(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.code_execution_records.append(_record(record_id="dup"))
        with pytest.raises(DuplicateRecordError):
            await backend.code_execution_records.append(_record(record_id="dup"))

    async def test_purge_before(self, backend: PersistenceBackend) -> None:
        old = datetime.now(UTC) - timedelta(days=2)
        await backend.code_execution_records.append(
            _record(record_id="old", executed_at=old),
        )
        await backend.code_execution_records.append(_record(record_id="new"))
        removed = await backend.code_execution_records.purge_before(
            datetime.now(UTC) - timedelta(days=1),
        )
        assert removed == 1
        page = await backend.code_execution_records.query(CodeExecutionFilterSpec())
        assert [r.record_id for r in page] == ["new"]
