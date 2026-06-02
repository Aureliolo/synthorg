"""Unit tests for the deliverable-receipt living-doc renderer."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from synthorg.core.enums import DocType
from synthorg.deliverable_receipts.models import (
    DeliverableReceipt,
    ReceiptSourceEntry,
)
from synthorg.deliverable_receipts.renderer import RECEIPT_HEADING, ReceiptRenderer
from synthorg.docs_engine.models import (
    DocBlock,
    HeadingBlock,
    LivingDocument,
    ProseBlock,
)
from synthorg.docs_engine.service import DocsService
from tests._shared import mock_of

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 6, 2, 12, 0, tzinfo=UTC)


def _doc(*, body: tuple[DocBlock, ...]) -> LivingDocument:
    return LivingDocument(
        slug="the-deliverable",
        title="The Deliverable",
        doc_type=DocType.DELIVERABLE,
        author_agent_id="agent-1",
        body=body,
        related_task_ids=("t-1",),
        created_at=_NOW,
        updated_at=_NOW,
    )


def _receipt() -> DeliverableReceipt:
    return DeliverableReceipt(
        receipt_id="r-1",
        task_id="t-1",
        project_id="p-1",
        execution_id="exec-1",
        deliverable_doc_slug="the-deliverable",
        issued_at=_NOW,
        total_cost=1.5,
        currency="EUR",
        sources=(
            ReceiptSourceEntry(
                source_id="s-1",
                chunk_id="c-1",
                title="Spec",
                uri="repo://spec",
                content_hash="a" * 64,
            ),
        ),
    )


def _renderer(doc: LivingDocument) -> tuple[ReceiptRenderer, AsyncMock]:
    docs_service = mock_of[DocsService]()
    docs_service.read_doc = AsyncMock(spec=DocsService.read_doc, return_value=doc)
    write_doc = AsyncMock(spec=DocsService.write_doc)
    docs_service.write_doc = write_doc
    return ReceiptRenderer(docs_service=docs_service), write_doc


def _receipt_headings(body: tuple[DocBlock, ...]) -> int:
    return sum(
        1 for b in body if isinstance(b, HeadingBlock) and b.text == RECEIPT_HEADING
    )


class TestReceiptRenderer:
    async def test_appends_receipt_section(self) -> None:
        doc = _doc(body=(ProseBlock(text="Original content"),))
        renderer, write_doc = _renderer(doc)
        await renderer.render_into_doc(receipt=_receipt())
        body = write_doc.call_args.kwargs["body"]
        assert _receipt_headings(body) == 1
        # Original content is preserved ahead of the receipt section.
        assert isinstance(body[0], ProseBlock)

    async def test_idempotent_replace(self) -> None:
        # A doc that already has a receipt section is re-rendered once.
        original = ProseBlock(text="Original content")
        stale_receipt = (
            HeadingBlock(level=2, text=RECEIPT_HEADING),
            ProseBlock(text="stale receipt body"),
        )
        doc = _doc(body=(original, *stale_receipt))
        renderer, write_doc = _renderer(doc)
        await renderer.render_into_doc(receipt=_receipt())
        body = write_doc.call_args.kwargs["body"]
        assert _receipt_headings(body) == 1
        assert isinstance(body[0], ProseBlock)

    async def test_writes_under_same_slug(self) -> None:
        doc = _doc(body=(ProseBlock(text="x"),))
        renderer, write_doc = _renderer(doc)
        await renderer.render_into_doc(receipt=_receipt())
        assert write_doc.call_args.kwargs["slug"] == "the-deliverable"
        assert write_doc.call_args.kwargs["doc_type"] is DocType.DELIVERABLE
