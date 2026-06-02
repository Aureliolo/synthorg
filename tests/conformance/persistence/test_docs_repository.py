"""Conformance tests for ``DocsRepository`` (SQLite + Postgres).

Asserts the shared contract between the SQLite and Postgres implementations:
composite-key get/save, recency-first list ordering, project + type + tag
filtering, count parity with query, FK cascade from ``projects``.
"""

from datetime import UTC, datetime, timedelta

import pytest

from synthorg.core.enums import DocType
from synthorg.core.project import Project
from synthorg.core.types import NotBlankStr
from synthorg.docs_engine.models import DocMetadata
from synthorg.persistence.docs_protocol import DocsFilterSpec
from synthorg.persistence.protocol import PersistenceBackend

pytestmark = pytest.mark.integration


def _project(project_id: str = "proj-1") -> Project:
    return Project(id=NotBlankStr(project_id), name=NotBlankStr("Demo"))


def _meta(  # noqa: PLR0913 -- test helper takes one kwarg per metadata field
    *,
    project_id: str = "proj-1",
    slug: str = "q2-status",
    doc_type: DocType = DocType.STATUS_REPORT,
    title: str = "Q2 status",
    tags: tuple[str, ...] = (),
    head_sha: str = "deadbeefcafe1111deadbeefcafe1111deadbeef",
    last_indexed: str | None = None,
    ts: datetime | None = None,
) -> DocMetadata:
    timestamp = ts if ts is not None else datetime(2026, 5, 20, tzinfo=UTC)
    return DocMetadata(
        project_id=NotBlankStr(project_id),
        slug=NotBlankStr(slug),
        doc_type=doc_type,
        title=NotBlankStr(title),
        tags=tuple(NotBlankStr(t) for t in tags),
        head_commit_sha=NotBlankStr(head_sha),
        last_indexed_commit_sha=NotBlankStr(last_indexed) if last_indexed else None,
        created_at=timestamp,
        updated_at=timestamp,
    )


class TestDocsRepository:
    async def test_save_and_get(self, backend: PersistenceBackend) -> None:
        await backend.projects.save(_project())
        await backend.project_docs.save(_meta())
        fetched = await backend.project_docs.get(
            (NotBlankStr("proj-1"), NotBlankStr("q2-status"))
        )
        assert fetched is not None
        assert fetched.slug == "q2-status"
        assert fetched.doc_type is DocType.STATUS_REPORT

    async def test_get_missing_returns_none(self, backend: PersistenceBackend) -> None:
        assert (
            await backend.project_docs.get((NotBlankStr("ghost"), NotBlankStr("none")))
            is None
        )

    async def test_save_upsert_replaces(self, backend: PersistenceBackend) -> None:
        await backend.projects.save(_project())
        await backend.project_docs.save(_meta(title="Original"))
        await backend.project_docs.save(
            _meta(
                title="Revised", last_indexed="11111111111111111111111111111111dadadada"
            ),
        )
        fetched = await backend.project_docs.get(
            (NotBlankStr("proj-1"), NotBlankStr("q2-status"))
        )
        assert fetched is not None
        assert fetched.title == "Revised"
        assert (
            fetched.last_indexed_commit_sha
            == "11111111111111111111111111111111dadadada"
        )

    async def test_tags_round_trip(self, backend: PersistenceBackend) -> None:
        await backend.projects.save(_project())
        await backend.project_docs.save(_meta(tags=("checkout", "q2")))
        fetched = await backend.project_docs.get(
            (NotBlankStr("proj-1"), NotBlankStr("q2-status"))
        )
        assert fetched is not None
        assert fetched.tags == ("checkout", "q2")

    async def test_list_items_recency_first(self, backend: PersistenceBackend) -> None:
        await backend.projects.save(_project())
        await backend.project_docs.save(
            _meta(slug="oldest", ts=datetime(2026, 5, 1, tzinfo=UTC)),
        )
        await backend.project_docs.save(
            _meta(slug="middle", ts=datetime(2026, 5, 10, tzinfo=UTC)),
        )
        await backend.project_docs.save(
            _meta(slug="newest", ts=datetime(2026, 5, 20, tzinfo=UTC)),
        )
        rows = await backend.project_docs.list_items()
        ordered_slugs = [r.slug for r in rows if r.project_id == "proj-1"]
        assert ordered_slugs == ["newest", "middle", "oldest"]

    async def test_query_by_doc_type(self, backend: PersistenceBackend) -> None:
        await backend.projects.save(_project())
        await backend.project_docs.save(
            _meta(slug="sr", doc_type=DocType.STATUS_REPORT)
        )
        await backend.project_docs.save(_meta(slug="del", doc_type=DocType.DELIVERABLE))
        await backend.project_docs.save(
            _meta(slug="kn", doc_type=DocType.KNOWLEDGE_NOTE)
        )

        deliverables = await backend.project_docs.query(
            DocsFilterSpec(
                project_id=NotBlankStr("proj-1"),
                doc_type=DocType.DELIVERABLE,
            )
        )
        assert {r.slug for r in deliverables} == {"del"}

    async def test_run_narrative_round_trip(self, backend: PersistenceBackend) -> None:
        """The widened doc_type CHECK admits run_narrative on both backends."""
        await backend.projects.save(_project())
        await backend.project_docs.save(
            _meta(
                slug="run-narrative-exec1",
                doc_type=DocType.RUN_NARRATIVE,
                title="Run narrative",
            )
        )
        fetched = await backend.project_docs.get(
            (NotBlankStr("proj-1"), NotBlankStr("run-narrative-exec1"))
        )
        assert fetched is not None
        assert fetched.doc_type is DocType.RUN_NARRATIVE
        narratives = await backend.project_docs.query(
            DocsFilterSpec(
                project_id=NotBlankStr("proj-1"),
                doc_type=DocType.RUN_NARRATIVE,
            )
        )
        assert {r.slug for r in narratives} == {"run-narrative-exec1"}

    async def test_codebase_analysis_round_trip(
        self, backend: PersistenceBackend
    ) -> None:
        """The widened CHECK also admits the previously-omitted codebase_analysis."""
        await backend.projects.save(_project())
        await backend.project_docs.save(
            _meta(
                slug="intake-analysis",
                doc_type=DocType.CODEBASE_ANALYSIS,
                title="Codebase analysis",
            )
        )
        fetched = await backend.project_docs.get(
            (NotBlankStr("proj-1"), NotBlankStr("intake-analysis"))
        )
        assert fetched is not None
        assert fetched.doc_type is DocType.CODEBASE_ANALYSIS
        analyses = await backend.project_docs.query(
            DocsFilterSpec(
                project_id=NotBlankStr("proj-1"),
                doc_type=DocType.CODEBASE_ANALYSIS,
            )
        )
        assert {r.slug for r in analyses} == {"intake-analysis"}

    async def test_query_by_tag(self, backend: PersistenceBackend) -> None:
        await backend.projects.save(_project())
        await backend.project_docs.save(_meta(slug="a", tags=("checkout",)))
        await backend.project_docs.save(_meta(slug="b", tags=("billing",)))
        await backend.project_docs.save(_meta(slug="c", tags=("checkout", "billing")))

        checkout = await backend.project_docs.query(
            DocsFilterSpec(
                project_id=NotBlankStr("proj-1"),
                tag=NotBlankStr("checkout"),
            )
        )
        assert {r.slug for r in checkout} == {"a", "c"}

    async def test_query_by_updated_since(self, backend: PersistenceBackend) -> None:
        await backend.projects.save(_project())
        await backend.project_docs.save(
            _meta(slug="old", ts=datetime(2026, 5, 1, tzinfo=UTC)),
        )
        await backend.project_docs.save(
            _meta(slug="new", ts=datetime(2026, 5, 20, tzinfo=UTC)),
        )

        recent = await backend.project_docs.query(
            DocsFilterSpec(
                project_id=NotBlankStr("proj-1"),
                updated_since=datetime(2026, 5, 15, tzinfo=UTC),
            )
        )
        assert {r.slug for r in recent} == {"new"}

    async def test_count_matches_query(self, backend: PersistenceBackend) -> None:
        await backend.projects.save(_project())
        for i in range(3):
            await backend.project_docs.save(
                _meta(
                    slug=f"doc-{i}",
                    ts=datetime(2026, 5, 20, tzinfo=UTC) + timedelta(seconds=i),
                )
            )
        spec = DocsFilterSpec(project_id=NotBlankStr("proj-1"))
        assert await backend.project_docs.count(spec) == 3
        assert len(await backend.project_docs.query(spec)) == 3

    async def test_delete_existing(self, backend: PersistenceBackend) -> None:
        await backend.projects.save(_project())
        await backend.project_docs.save(_meta())
        deleted = await backend.project_docs.delete(
            (NotBlankStr("proj-1"), NotBlankStr("q2-status"))
        )
        assert deleted is True
        assert (
            await backend.project_docs.get(
                (NotBlankStr("proj-1"), NotBlankStr("q2-status"))
            )
            is None
        )

    async def test_delete_missing(self, backend: PersistenceBackend) -> None:
        deleted = await backend.project_docs.delete(
            (NotBlankStr("ghost"), NotBlankStr("none"))
        )
        assert deleted is False

    async def test_project_delete_cascades_docs(
        self, backend: PersistenceBackend
    ) -> None:
        """Deleting the parent project removes its doc metadata (FK cascade)."""
        await backend.projects.save(_project())
        await backend.project_docs.save(_meta())
        await backend.projects.delete(NotBlankStr("proj-1"))
        assert (
            await backend.project_docs.get(
                (NotBlankStr("proj-1"), NotBlankStr("q2-status"))
            )
            is None
        )
