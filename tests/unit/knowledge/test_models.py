"""Unit tests for :mod:`synthorg.knowledge.models`.

Covers the structural invariants the substrate relies on: frozen +
extra-forbid, the ProvenanceLocator discriminated union, char/line range
validation, and the round-trip shape of every public model.
"""

from datetime import UTC, datetime

import pytest
from pydantic import TypeAdapter, ValidationError

from synthorg.knowledge.enums import ContentKind, SourceStatus, SourceType
from synthorg.knowledge.models import (
    Citation,
    CodeLocator,
    KnowledgeChunk,
    KnowledgeHit,
    KnowledgeSource,
    PdfLocator,
    ProvenanceLocator,
    RawDocument,
    RawUnit,
    TicketLocator,
    WebLocator,
)

pytestmark = pytest.mark.unit

_HASH = "a" * 64


def _ts() -> datetime:
    return datetime(2026, 5, 21, 12, 0, 0, tzinfo=UTC)


class TestProvenanceLocators:
    """The citation precision model."""

    def test_pdf_locator_round_trip(self) -> None:
        loc = PdfLocator(page=3, bbox=(0.0, 1.0, 2.0, 3.0), char_start=0, char_end=10)
        assert loc.locator_kind == "pdf"
        assert loc.page == 3

    def test_pdf_locator_rejects_page_zero(self) -> None:
        with pytest.raises(ValidationError):
            PdfLocator(page=0, char_start=0, char_end=1)

    def test_char_range_rejects_inverted(self) -> None:
        with pytest.raises(ValidationError):
            WebLocator(url="https://x.test", char_start=10, char_end=5)

    def test_code_locator_rejects_inverted_lines(self) -> None:
        with pytest.raises(ValidationError):
            CodeLocator(path="a.py", line_start=20, line_end=10)

    def test_union_discriminates_by_kind(self) -> None:
        adapter: TypeAdapter[ProvenanceLocator] = TypeAdapter(ProvenanceLocator)
        parsed = adapter.validate_python(
            {"locator_kind": "code", "path": "a.py", "line_start": 1, "line_end": 4}
        )
        assert isinstance(parsed, CodeLocator)

    def test_ticket_locator_optional_comment(self) -> None:
        loc = TicketLocator(ticket_id="T-1", char_start=0, char_end=4)
        assert loc.comment_id is None

    def test_locator_is_frozen(self) -> None:
        loc = PdfLocator(page=1, char_start=0, char_end=1)
        with pytest.raises(ValidationError):
            loc.page = 2  # type: ignore[misc]


class TestKnowledgeChunk:
    def test_chunk_round_trip(self) -> None:
        chunk = KnowledgeChunk(
            chunk_id="c1",
            source_id="s1",
            content_kind=ContentKind.CODE,
            chunk_index=0,
            text="def f(): ...",
            content_hash=_HASH,
            locator=CodeLocator(path="a.py", line_start=1, line_end=1),
            tags=("source:s1", "chunk:c1"),
        )
        assert chunk.locator.locator_kind == "code"

    def test_rejects_bad_hash(self) -> None:
        with pytest.raises(ValidationError):
            KnowledgeChunk(
                chunk_id="c1",
                source_id="s1",
                content_kind=ContentKind.DOCUMENT,
                chunk_index=0,
                text="hi",
                content_hash="not-a-hash",
                locator=WebLocator(url="https://x.test", char_start=0, char_end=2),
            )

    def test_rejects_extra_field(self) -> None:
        with pytest.raises(ValidationError):
            KnowledgeChunk(
                chunk_id="c1",
                source_id="s1",
                content_kind=ContentKind.DOCUMENT,
                chunk_index=0,
                text="hi",
                content_hash=_HASH,
                locator=WebLocator(url="https://x.test", char_start=0, char_end=2),
                surprise=1,  # type: ignore[call-arg]
            )


class TestKnowledgeSource:
    def _make(self, **overrides: object) -> KnowledgeSource:
        fields: dict[str, object] = {
            "source_id": "s1",
            "source_type": SourceType.PDF,
            "uri": "corpus/spec.pdf",
            "title": "Spec",
            "content_hash": _HASH,
            "status": SourceStatus.PENDING,
            "created_at": _ts(),
            "updated_at": _ts(),
        }
        fields.update(overrides)
        return KnowledgeSource(**fields)  # type: ignore[arg-type]

    def test_global_when_no_project(self) -> None:
        assert self._make().is_global is True

    def test_scoped_when_project_set(self) -> None:
        assert self._make(project_id="proj-1").is_global is False

    def test_defaults(self) -> None:
        src = self._make()
        assert src.chunk_count == 0
        assert src.last_indexed_at is None
        assert src.last_error is None


class TestCitationAndHit:
    def test_hit_carries_citation(self) -> None:
        citation = Citation(
            source_id="s1",
            chunk_id="c1",
            source_type=SourceType.WEB,
            title="Page",
            uri="https://x.test",
            locator=WebLocator(url="https://x.test", char_start=0, char_end=4),
            content_hash=_HASH,
        )
        hit = KnowledgeHit(chunk_text="hello", relevance_score=0.9, citation=citation)
        assert hit.citation.chunk_id == "c1"

    def test_hit_rejects_score_above_one(self) -> None:
        citation = Citation(
            source_id="s1",
            chunk_id="c1",
            source_type=SourceType.WEB,
            title="Page",
            uri="https://x.test",
            locator=WebLocator(url="https://x.test", char_start=0, char_end=4),
            content_hash=_HASH,
        )
        with pytest.raises(ValidationError):
            KnowledgeHit(chunk_text="hello", relevance_score=1.5, citation=citation)


class TestRawDocument:
    def test_raw_document_round_trip(self) -> None:
        doc = RawDocument(
            source_id="s1",
            source_type=SourceType.PDF,
            uri="corpus/spec.pdf",
            title="Spec",
            content_hash=_HASH,
            units=(
                RawUnit(
                    text="page one",
                    locator=PdfLocator(page=1, char_start=0, char_end=8),
                    content_kind=ContentKind.PDF_PAGE,
                ),
            ),
        )
        assert len(doc.units) == 1

    def test_raw_unit_allows_empty_text(self) -> None:
        unit = RawUnit(
            text="",
            locator=PdfLocator(page=2, char_start=0, char_end=0),
            content_kind=ContentKind.PDF_PAGE,
        )
        assert unit.text == ""
