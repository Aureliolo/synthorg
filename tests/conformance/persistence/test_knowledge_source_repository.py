"""Conformance tests for ``KnowledgeSourceRepository`` (SQLite + Postgres).

Asserts the shared contract: id get/save/upsert, recency-first list
ordering, project + global scope filtering, type / status / stale-only
filters, count parity with query, and FK cascade from ``projects`` for
project-scoped sources (global sources survive).
"""

from datetime import UTC, datetime, timedelta

import pytest

from synthorg.core.project import Project
from synthorg.core.types import NotBlankStr
from synthorg.knowledge.enums import SourceStatus, SourceType
from synthorg.knowledge.models import KnowledgeSource
from synthorg.persistence.knowledge_protocol import KnowledgeSourceFilter
from synthorg.persistence.protocol import PersistenceBackend
from tests._shared import as_uuid, sid

pytestmark = pytest.mark.integration

_HASH = "a" * 64


def _project(project_id: str = "proj-1") -> Project:
    return Project(id=as_uuid(project_id), name=NotBlankStr("Demo"))


def _source(  # noqa: PLR0913 -- test helper takes one kwarg per field
    *,
    source_id: str = "src-1",
    source_type: SourceType = SourceType.PDF,
    project_id: str | None = "proj-1",
    uri: str = "corpus/spec.pdf",
    title: str = "Spec",
    status: SourceStatus = SourceStatus.INDEXED,
    chunk_count: int = 3,
    ts: datetime | None = None,
) -> KnowledgeSource:
    timestamp = ts if ts is not None else datetime(2026, 5, 21, tzinfo=UTC)
    return KnowledgeSource(
        source_id=NotBlankStr(source_id),
        source_type=source_type,
        project_id=NotBlankStr(sid(project_id)) if project_id is not None else None,
        uri=NotBlankStr(uri),
        title=title,
        content_hash=_HASH,
        status=status,
        chunk_count=chunk_count,
        created_at=timestamp,
        updated_at=timestamp,
        last_indexed_at=timestamp if status is SourceStatus.INDEXED else None,
    )


class TestKnowledgeSourceRepository:
    async def test_save_and_get(self, backend: PersistenceBackend) -> None:
        await backend.projects.save(_project())
        await backend.knowledge_sources.save(_source())
        fetched = await backend.knowledge_sources.get(NotBlankStr("src-1"))
        assert fetched is not None
        assert fetched.source_type is SourceType.PDF
        assert fetched.chunk_count == 3
        assert fetched.is_global is False

    async def test_get_missing_returns_none(self, backend: PersistenceBackend) -> None:
        assert await backend.knowledge_sources.get(NotBlankStr("ghost")) is None

    async def test_get_many_returns_existing_only(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.projects.save(_project())
        await backend.knowledge_sources.save(_source(source_id="src-1"))
        await backend.knowledge_sources.save(_source(source_id="src-2"))
        rows = await backend.knowledge_sources.get_many(
            (NotBlankStr("src-1"), NotBlankStr("ghost"), NotBlankStr("src-2")),
        )
        assert {r.source_id for r in rows} == {"src-1", "src-2"}

    async def test_get_many_empty_input_returns_empty(
        self, backend: PersistenceBackend
    ) -> None:
        assert await backend.knowledge_sources.get_many(()) == ()

    async def test_global_source_round_trip(self, backend: PersistenceBackend) -> None:
        await backend.knowledge_sources.save(_source(source_id="glob", project_id=None))
        fetched = await backend.knowledge_sources.get(NotBlankStr("glob"))
        assert fetched is not None
        assert fetched.project_id is None
        assert fetched.is_global is True

    async def test_save_upsert_replaces(self, backend: PersistenceBackend) -> None:
        await backend.projects.save(_project())
        await backend.knowledge_sources.save(_source(status=SourceStatus.PENDING))
        await backend.knowledge_sources.save(
            _source(status=SourceStatus.INDEXED, chunk_count=9)
        )
        fetched = await backend.knowledge_sources.get(NotBlankStr("src-1"))
        assert fetched is not None
        assert fetched.status is SourceStatus.INDEXED
        assert fetched.chunk_count == 9

    async def test_list_items_recency_first(self, backend: PersistenceBackend) -> None:
        await backend.projects.save(_project())
        await backend.knowledge_sources.save(
            _source(source_id="oldest", ts=datetime(2026, 5, 1, tzinfo=UTC))
        )
        await backend.knowledge_sources.save(
            _source(source_id="newest", ts=datetime(2026, 5, 20, tzinfo=UTC))
        )
        rows = await backend.knowledge_sources.list_items()
        ids = [r.source_id for r in rows if r.source_id in {"oldest", "newest"}]
        assert ids == ["newest", "oldest"]

    async def test_query_project_plus_global(self, backend: PersistenceBackend) -> None:
        await backend.projects.save(_project())
        await backend.projects.save(_project("proj-2"))
        await backend.knowledge_sources.save(_source(source_id="scoped"))
        await backend.knowledge_sources.save(_source(source_id="glob", project_id=None))
        await backend.knowledge_sources.save(
            _source(source_id="other", project_id="proj-2")
        )
        rows = await backend.knowledge_sources.query(
            KnowledgeSourceFilter(
                project_id=NotBlankStr(sid("proj-1")), include_global=True
            )
        )
        assert {r.source_id for r in rows} == {"scoped", "glob"}

    async def test_query_global_only(self, backend: PersistenceBackend) -> None:
        await backend.projects.save(_project())
        await backend.knowledge_sources.save(_source(source_id="scoped"))
        await backend.knowledge_sources.save(_source(source_id="glob", project_id=None))
        rows = await backend.knowledge_sources.query(
            KnowledgeSourceFilter(include_global=True)
        )
        assert {r.source_id for r in rows} == {"glob"}

    async def test_query_by_type_and_status(self, backend: PersistenceBackend) -> None:
        await backend.projects.save(_project())
        await backend.knowledge_sources.save(
            _source(source_id="pdf", source_type=SourceType.PDF)
        )
        await backend.knowledge_sources.save(
            _source(source_id="web", source_type=SourceType.WEB)
        )
        rows = await backend.knowledge_sources.query(
            KnowledgeSourceFilter(
                project_id=NotBlankStr(sid("proj-1")), source_type=SourceType.WEB
            )
        )
        assert {r.source_id for r in rows} == {"web"}

    async def test_query_stale_only(self, backend: PersistenceBackend) -> None:
        await backend.projects.save(_project())
        await backend.knowledge_sources.save(
            _source(source_id="fresh", status=SourceStatus.INDEXED)
        )
        await backend.knowledge_sources.save(
            _source(source_id="stale", status=SourceStatus.STALE)
        )
        rows = await backend.knowledge_sources.query(
            KnowledgeSourceFilter(
                project_id=NotBlankStr(sid("proj-1")), stale_only=True
            )
        )
        assert {r.source_id for r in rows} == {"stale"}

    async def test_count_matches_query(self, backend: PersistenceBackend) -> None:
        await backend.projects.save(_project())
        for i in range(3):
            await backend.knowledge_sources.save(
                _source(
                    source_id=f"src-{i}",
                    ts=datetime(2026, 5, 21, tzinfo=UTC) + timedelta(seconds=i),
                )
            )
        spec = KnowledgeSourceFilter(project_id=NotBlankStr(sid("proj-1")))
        assert await backend.knowledge_sources.count(spec) == 3
        assert len(await backend.knowledge_sources.query(spec)) == 3

    async def test_delete_existing(self, backend: PersistenceBackend) -> None:
        await backend.projects.save(_project())
        await backend.knowledge_sources.save(_source())
        assert await backend.knowledge_sources.delete(NotBlankStr("src-1")) is True
        assert await backend.knowledge_sources.get(NotBlankStr("src-1")) is None

    async def test_delete_missing(self, backend: PersistenceBackend) -> None:
        assert await backend.knowledge_sources.delete(NotBlankStr("ghost")) is False

    async def test_project_delete_cascades_scoped_only(
        self, backend: PersistenceBackend
    ) -> None:
        """Deleting a project removes its scoped sources; global survive."""
        await backend.projects.save(_project())
        await backend.knowledge_sources.save(_source(source_id="scoped"))
        await backend.knowledge_sources.save(_source(source_id="glob", project_id=None))
        await backend.projects.delete(NotBlankStr(sid("proj-1")))
        assert await backend.knowledge_sources.get(NotBlankStr("scoped")) is None
        assert await backend.knowledge_sources.get(NotBlankStr("glob")) is not None
