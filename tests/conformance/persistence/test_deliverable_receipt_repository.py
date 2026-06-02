"""Conformance tests for ``DeliverableReceiptRepository`` (SQLite + Postgres)."""

from datetime import UTC, datetime

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.deliverable_receipts.models import (
    DeliverableReceipt,
    ReceiptCassetteRef,
    ReceiptSourceEntry,
    ReceiptTestEntry,
)
from synthorg.persistence.deliverable_receipt_protocol import (
    DeliverableReceiptFilterSpec,
)
from synthorg.persistence.protocol import PersistenceBackend

pytestmark = pytest.mark.integration

_NOW = datetime(2026, 6, 2, 12, 0, tzinfo=UTC)


def _receipt(  # noqa: PLR0913 -- keyword-only test builder
    *,
    receipt_id: str = "rcpt-001",
    task_id: str = "task-001",
    project_id: str = "proj-001",
    execution_id: str = "exec-001",
    slug: str = "the-deliverable",
    total_cost: float = 1.5,
    with_payload: bool = False,
) -> DeliverableReceipt:
    sources: tuple[ReceiptSourceEntry, ...] = ()
    tests: tuple[ReceiptTestEntry, ...] = ()
    cassette: ReceiptCassetteRef | None = None
    if with_payload:
        sources = (
            ReceiptSourceEntry(
                source_id="src-1",
                chunk_id="chunk-1",
                title="Spec",
                uri="file:///spec.pdf",
                content_hash="abc",
            ),
        )
        tests = (
            ReceiptTestEntry(
                record_id="cer-1",
                command="python -m pytest",
                returncode=0,
                passed=True,
                timed_out=False,
                executed_at=_NOW,
            ),
        )
        cassette = ReceiptCassetteRef(
            path="cassettes/run.json",
            content_hash="deadbeef",
        )
    return DeliverableReceipt(
        receipt_id=NotBlankStr(receipt_id),
        task_id=NotBlankStr(task_id),
        project_id=NotBlankStr(project_id),
        execution_id=NotBlankStr(execution_id),
        deliverable_doc_slug=NotBlankStr(slug),
        issued_at=_NOW,
        total_cost=total_cost,
        currency="EUR",
        sources=sources,
        tests=tests,
        cassette=cassette,
    )


class TestDeliverableReceiptRepository:
    async def test_save_and_get(self, backend: PersistenceBackend) -> None:
        await backend.deliverable_receipts.save(_receipt(with_payload=True))
        fetched = await backend.deliverable_receipts.get(NotBlankStr("rcpt-001"))
        assert fetched is not None
        assert fetched.task_id == "task-001"
        assert fetched.total_cost == pytest.approx(1.5)
        assert len(fetched.sources) == 1
        assert fetched.sources[0].source_id == "src-1"
        assert fetched.tests[0].passed is True
        assert fetched.cassette is not None
        assert fetched.cassette.content_hash == "deadbeef"

    async def test_get_missing_returns_none(self, backend: PersistenceBackend) -> None:
        assert await backend.deliverable_receipts.get(NotBlankStr("nope")) is None

    async def test_upsert_by_task_replaces_prior(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.deliverable_receipts.save(_receipt(receipt_id="old"))
        await backend.deliverable_receipts.save(
            _receipt(receipt_id="new", total_cost=9.0),
        )
        # The prior receipt for the same task is gone (replaced).
        assert await backend.deliverable_receipts.get(NotBlankStr("old")) is None
        current = await backend.deliverable_receipts.query(
            DeliverableReceiptFilterSpec(
                project_id=NotBlankStr("proj-001"),
                task_id=NotBlankStr("task-001"),
            ),
        )
        assert len(current) == 1
        assert current[0].receipt_id == "new"
        assert current[0].total_cost == pytest.approx(9.0)

    async def test_query_by_slug(self, backend: PersistenceBackend) -> None:
        await backend.deliverable_receipts.save(
            _receipt(receipt_id="a", task_id="t-a", slug="alpha"),
        )
        await backend.deliverable_receipts.save(
            _receipt(receipt_id="b", task_id="t-b", slug="beta"),
        )
        page = await backend.deliverable_receipts.query(
            DeliverableReceiptFilterSpec(
                project_id=NotBlankStr("proj-001"),
                deliverable_doc_slug=NotBlankStr("beta"),
            ),
        )
        assert [r.receipt_id for r in page] == ["b"]

    async def test_count(self, backend: PersistenceBackend) -> None:
        await backend.deliverable_receipts.save(_receipt(receipt_id="a", task_id="t-a"))
        await backend.deliverable_receipts.save(_receipt(receipt_id="b", task_id="t-b"))
        count = await backend.deliverable_receipts.count(
            DeliverableReceiptFilterSpec(project_id=NotBlankStr("proj-001")),
        )
        assert count == 2

    async def test_delete(self, backend: PersistenceBackend) -> None:
        await backend.deliverable_receipts.save(_receipt())
        receipts = backend.deliverable_receipts
        assert await receipts.delete(NotBlankStr("rcpt-001")) is True
        assert await receipts.delete(NotBlankStr("rcpt-001")) is False
