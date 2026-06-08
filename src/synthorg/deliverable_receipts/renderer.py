# module-kind: service
"""Project a receipt into its deliverable's living document.

The receipt table is the system of record; this renderer writes a
human-readable ``Provenance Receipt`` section into the deliverable
``LivingDocument`` using existing block kinds only. Re-rendering is
idempotent: any prior receipt section (everything from the receipt
heading onward) is stripped before the fresh section is appended.
"""

from typing import TYPE_CHECKING, Final

from synthorg.core.types import NotBlankStr
from synthorg.docs_engine.enums import DocType
from synthorg.docs_engine.models import (
    BulletListBlock,
    DocBlock,
    HeadingBlock,
    MetricBlock,
)
from synthorg.observability import get_logger

if TYPE_CHECKING:
    from synthorg.deliverable_receipts.models import (
        DeliverableReceipt,
        ReceiptTestEntry,
    )
    from synthorg.docs_engine.models import DocMetadata, LivingDocument
    from synthorg.docs_engine.service import DocsService

logger = get_logger(__name__)

#: Heading text that marks the start of the rendered receipt section.
RECEIPT_HEADING: str = "Provenance Receipt"
#: Synthetic author attributed to the receipt projection write.
RECEIPT_AUTHOR: NotBlankStr = NotBlankStr("_system:deliverable-receipts")

_BULLET_LIMIT: Final[int] = 1024
_COST_DECIMALS: Final[int] = 4


def _clip(text: str, limit: int) -> str:
    """Truncate *text* to *limit* characters (block payload caps).

    Returns:
        The text truncated to at most ``limit`` characters.
    """
    return text[:limit]


class ReceiptRenderer:
    """Render a receipt's human projection into its deliverable doc."""

    def __init__(self, *, docs_service: DocsService) -> None:
        self._docs_service = docs_service

    async def render_into_doc(
        self,
        *,
        receipt: DeliverableReceipt,
    ) -> DocMetadata:
        """Append (idempotently) the receipt section to the deliverable doc.

        Reads the current deliverable document, strips any prior receipt
        section, appends the freshly rendered one, and re-writes the doc.

        Returns:
            The updated :class:`DocMetadata` row.
        """
        existing = await self._docs_service.read_doc(
            project_id=receipt.project_id,
            slug=receipt.deliverable_doc_slug,
        )
        body = (*_without_receipt_section(existing), *_render_blocks(receipt))
        return await self._docs_service.write_doc(
            project_id=receipt.project_id,
            title=NotBlankStr(existing.title),
            doc_type=DocType.DELIVERABLE,
            author_agent_id=RECEIPT_AUTHOR,
            body=body,
            tags=existing.tags,
            related_task_ids=existing.related_task_ids,
            slug=receipt.deliverable_doc_slug,
        )


def _without_receipt_section(doc: LivingDocument) -> tuple[DocBlock, ...]:
    """Return the doc body with any prior receipt section removed.

    Returns:
        The body up to (but excluding) the first receipt heading; the
        whole body when no receipt section is present.
    """
    for index, block in enumerate(doc.body):
        if isinstance(block, HeadingBlock) and block.text == RECEIPT_HEADING:
            return doc.body[:index]
    return doc.body


def _render_blocks(receipt: DeliverableReceipt) -> tuple[DocBlock, ...]:
    """Render the receipt as a sequence of doc blocks.

    Returns:
        The ``Provenance Receipt`` section blocks, marker heading first.
    """
    blocks: list[DocBlock] = [
        HeadingBlock(level=2, text=RECEIPT_HEADING),
        MetricBlock(
            name="Total cost",
            value=f"{receipt.total_cost:.{_COST_DECIMALS}f}",
            unit=receipt.currency,
        ),
        MetricBlock(name="Issued at", value=receipt.issued_at.isoformat()),
        MetricBlock(name="Execution", value=receipt.execution_id),
    ]
    blocks.extend(_sources_blocks(receipt))
    blocks.extend(_decisions_blocks(receipt))
    blocks.extend(_tests_blocks(receipt))
    blocks.extend(_red_team_blocks(receipt))
    blocks.extend(_cassette_blocks(receipt))
    return tuple(blocks)


def _sources_blocks(receipt: DeliverableReceipt) -> list[DocBlock]:
    if not receipt.sources:
        return []
    items = tuple(
        _clip(f"{s.title} ({s.source_id[:12]})", _BULLET_LIMIT) for s in receipt.sources
    )
    return [
        HeadingBlock(level=3, text="Sources used"),
        BulletListBlock(items=items),
    ]


def _decisions_blocks(receipt: DeliverableReceipt) -> list[DocBlock]:
    # Render only structured handles (entry id + revision), never the
    # agent-authored title/rationale: this section is indexed into the
    # trusted PROJECT_DOC RAG channel, and brain-authored prose is
    # attacker-influenceable. Full rationale lives in the receipt record
    # (REST API + dashboard panel), which is not indexed into memory.
    if not receipt.decisions:
        return []
    items = tuple(f"{d.entry_id} (rev {d.revision})" for d in receipt.decisions)
    return [
        HeadingBlock(level=3, text="Key decisions"),
        MetricBlock(name="Decisions recorded", value=str(len(receipt.decisions))),
        BulletListBlock(items=items),
    ]


def _tests_blocks(receipt: DeliverableReceipt) -> list[DocBlock]:
    if not receipt.tests:
        return []
    return [
        HeadingBlock(level=3, text="Tests run"),
        BulletListBlock(items=tuple(_test_item(t) for t in receipt.tests)),
    ]


def _test_item(test: ReceiptTestEntry) -> str:
    # Omit the agent-authored command string (indexed into the trusted doc
    # channel); the record id + result are the safe cross-reference handles.
    verdict = "PASS" if test.passed else "FAIL"
    return f"{verdict} (rc={test.returncode}) [{test.record_id}]"


def _red_team_blocks(receipt: DeliverableReceipt) -> list[DocBlock]:
    red_team = receipt.red_team
    if red_team is None:
        return []
    # Omit the red-team summary prose (attacker-influenced, indexed into the
    # trusted doc channel); structured counts only. Full summary lives in the
    # receipt record.
    return [
        HeadingBlock(level=3, text="Red-team review"),
        MetricBlock(name="Verdict", value=red_team.verdict.value),
        MetricBlock(name="Findings", value=str(red_team.finding_count)),
        MetricBlock(name="High+ findings", value=str(red_team.high_plus_count)),
    ]


def _cassette_blocks(receipt: DeliverableReceipt) -> list[DocBlock]:
    cassette = receipt.cassette
    if cassette is None:
        return []
    return [
        HeadingBlock(level=3, text="Replayable cassette"),
        MetricBlock(name="Cassette path", value=cassette.path),
        MetricBlock(name="Cassette hash", value=cassette.content_hash),
    ]
