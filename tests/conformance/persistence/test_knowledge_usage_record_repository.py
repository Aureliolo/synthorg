"""Conformance tests for ``KnowledgeUsageRecordRepository`` (SQLite + Postgres)."""

from datetime import UTC, datetime, timedelta

import pytest

from synthorg.core.persistence_errors import DuplicateRecordError
from synthorg.core.types import NotBlankStr
from synthorg.persistence.knowledge_usage_protocol import (
    KnowledgeUsageFilterSpec,
    KnowledgeUsageRecord,
)
from synthorg.persistence.protocol import PersistenceBackend

pytestmark = pytest.mark.integration


def _record(  # noqa: PLR0913 -- keyword-only test builder
    *,
    record_id: str = "kur-001",
    task_id: str = "task-001",
    execution_id: str = "exec-001",
    project_id: str = "proj-001",
    source_id: str = "src-001",
    chunk_id: str = "chunk-001",
    content_hash: str = "hash-001",
    recorded_at: datetime | None = None,
) -> KnowledgeUsageRecord:
    return KnowledgeUsageRecord(
        record_id=NotBlankStr(record_id),
        task_id=NotBlankStr(task_id),
        execution_id=NotBlankStr(execution_id),
        project_id=NotBlankStr(project_id),
        source_id=NotBlankStr(source_id),
        chunk_id=NotBlankStr(chunk_id),
        content_hash=NotBlankStr(content_hash),
        recorded_at=recorded_at or datetime.now(UTC),
    )


class TestKnowledgeUsageRecordRepository:
    async def test_append_and_query_by_execution(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.knowledge_usage_records.append(_record())

        page = await backend.knowledge_usage_records.query(
            KnowledgeUsageFilterSpec(execution_id=NotBlankStr("exec-001")),
        )
        assert len(page) == 1
        assert page[0].source_id == "src-001"
        assert page[0].content_hash == "hash-001"

    async def test_query_by_source(self, backend: PersistenceBackend) -> None:
        await backend.knowledge_usage_records.append(
            _record(record_id="a", source_id="src-a"),
        )
        await backend.knowledge_usage_records.append(
            _record(record_id="b", source_id="src-b"),
        )
        page = await backend.knowledge_usage_records.query(
            KnowledgeUsageFilterSpec(source_id=NotBlankStr("src-b")),
        )
        assert [r.record_id for r in page] == ["b"]

    async def test_append_duplicate_id_raises(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.knowledge_usage_records.append(_record(record_id="dup"))
        with pytest.raises(DuplicateRecordError):
            await backend.knowledge_usage_records.append(
                _record(record_id="dup", source_id="other"),
            )

    async def test_query_newest_first(self, backend: PersistenceBackend) -> None:
        old = datetime.now(UTC) - timedelta(hours=1)
        new = datetime.now(UTC)
        await backend.knowledge_usage_records.append(
            _record(record_id="old", recorded_at=old),
        )
        await backend.knowledge_usage_records.append(
            _record(record_id="new", recorded_at=new),
        )
        page = await backend.knowledge_usage_records.query(
            KnowledgeUsageFilterSpec(execution_id=NotBlankStr("exec-001")),
        )
        assert [r.record_id for r in page] == ["new", "old"]

    async def test_purge_before(self, backend: PersistenceBackend) -> None:
        old = datetime.now(UTC) - timedelta(days=2)
        await backend.knowledge_usage_records.append(
            _record(record_id="old", recorded_at=old),
        )
        await backend.knowledge_usage_records.append(_record(record_id="new"))
        removed = await backend.knowledge_usage_records.purge_before(
            datetime.now(UTC) - timedelta(days=1),
        )
        assert removed == 1
        page = await backend.knowledge_usage_records.query(KnowledgeUsageFilterSpec())
        assert [r.record_id for r in page] == ["new"]
