"""Unit tests for the run-narrative orchestrating service."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from synthorg.core.enums import DocType, TaskStatus
from synthorg.core.types import NotBlankStr
from synthorg.docs_engine.models import DocMetadata, DocSummary
from synthorg.docs_engine.service import DocsService
from synthorg.meta.chief_of_staff.narrative.errors import (
    NarrativeSourceUnavailableError,
)
from synthorg.meta.chief_of_staff.narrative.models import (
    NarrativeProse,
    RunNarrativeInputs,
)
from synthorg.meta.chief_of_staff.narrative.reader import NarrativeReader
from synthorg.meta.chief_of_staff.narrative.service import ChiefOfStaffNarrator
from synthorg.meta.chief_of_staff.narrative.synthesiser import NarrativeSynthesiser
from tests._shared import mock_of

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


def _inputs() -> RunNarrativeInputs:
    return RunNarrativeInputs(
        project_id=NotBlankStr("proj-1"),
        task_id=NotBlankStr("task-1"),
        execution_id=NotBlankStr("exec-1"),
        brief_title=NotBlankStr("Ship checkout"),
        final_status=TaskStatus.COMPLETED,
        total_cost=1.0,
        total_turns=3,
        frame_count=3,
    )


def _metadata(slug: str = "run-narrative") -> DocMetadata:
    return DocMetadata(
        project_id=NotBlankStr("proj-1"),
        slug=NotBlankStr(slug),
        doc_type=DocType.RUN_NARRATIVE,
        title=NotBlankStr("Run narrative: Ship checkout"),
        head_commit_sha=NotBlankStr("deadbeefcafe1111deadbeefcafe1111deadbeef"),
        created_at=_NOW,
        updated_at=_NOW,
    )


def _summary(slug: str) -> DocSummary:
    return DocSummary(
        project_id=NotBlankStr("proj-1"),
        slug=NotBlankStr(slug),
        title=NotBlankStr("Run narrative: Ship checkout"),
        doc_type=DocType.RUN_NARRATIVE,
        updated_at=_NOW,
    )


def _narrator(
    *,
    gather: AsyncMock,
    list_docs: AsyncMock,
    write_doc: AsyncMock,
) -> ChiefOfStaffNarrator:
    reader = mock_of[NarrativeReader](gather=gather)
    synthesiser = mock_of[NarrativeSynthesiser](
        write_prose=AsyncMock(return_value=NarrativeProse(summary="A clean run."))
    )
    docs = mock_of[DocsService](list_docs=list_docs, write_doc=write_doc)
    return ChiefOfStaffNarrator(reader=reader, synthesiser=synthesiser, docs=docs)


class TestGenerate:
    async def test_generates_and_persists(self) -> None:
        gather = AsyncMock(return_value=_inputs())
        list_docs = AsyncMock(return_value=())
        write_doc = AsyncMock(return_value=_metadata())
        narrator = _narrator(gather=gather, list_docs=list_docs, write_doc=write_doc)
        result = await narrator.generate(
            task_id=NotBlankStr("task-1"), project_id=NotBlankStr("proj-1")
        )
        assert result is not None
        assert result.doc_type is DocType.RUN_NARRATIVE
        call = write_doc.await_args
        assert call is not None
        kwargs = call.kwargs
        assert kwargs["doc_type"] is DocType.RUN_NARRATIVE
        assert kwargs["slug"] is None
        assert kwargs["related_task_ids"] == (NotBlankStr("task-1"),)
        # Both the per-brief task tag (idempotency key) and the
        # per-run execution tag (provenance) are stamped.
        assert NotBlankStr("task:task-1") in kwargs["tags"]
        assert NotBlankStr("execution:exec-1") in kwargs["tags"]
        # The dedup lookup keys on the brief, not the execution.
        assert list_docs.await_args is not None
        assert list_docs.await_args.kwargs["tag"] == NotBlankStr("task:task-1")

    async def test_source_unavailable_returns_none(self) -> None:
        gather = AsyncMock(side_effect=NarrativeSourceUnavailableError())
        list_docs = AsyncMock(return_value=())
        write_doc = AsyncMock(return_value=_metadata())
        narrator = _narrator(gather=gather, list_docs=list_docs, write_doc=write_doc)
        result = await narrator.generate(
            task_id=NotBlankStr("task-1"), project_id=NotBlankStr("proj-1")
        )
        assert result is None
        write_doc.assert_not_awaited()

    async def test_idempotent_update_uses_existing_slug(self) -> None:
        gather = AsyncMock(return_value=_inputs())
        list_docs = AsyncMock(return_value=(_summary("run-narrative-exec-1"),))
        write_doc = AsyncMock(return_value=_metadata(slug="run-narrative-exec-1"))
        narrator = _narrator(gather=gather, list_docs=list_docs, write_doc=write_doc)
        await narrator.generate(
            task_id=NotBlankStr("task-1"), project_id=NotBlankStr("proj-1")
        )
        call = write_doc.await_args
        assert call is not None
        assert call.kwargs["slug"] == "run-narrative-exec-1"
