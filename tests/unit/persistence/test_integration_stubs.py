"""Unit tests for in-memory integration repository stubs.

These doubles must mirror the durable repos' insert-or-replace
semantics so test code that swaps them in does not observe behaviour
(e.g. duplicate rows on re-save) the real backends never produce.
"""

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.integrations.connections.models import WebhookReceipt
from synthorg.persistence.integration_stubs import (
    InMemoryWebhookReceiptRepository,
)
from tests._shared import as_uuid, sid

pytestmark = pytest.mark.unit


def _receipt(receipt_id: str, *, status: str = "received") -> WebhookReceipt:
    return WebhookReceipt(
        id=as_uuid(receipt_id),
        connection_name=NotBlankStr("conn-1"),
        event_type=NotBlankStr("push"),
        status=NotBlankStr(status),
    )


async def test_save_is_idempotent_upsert_by_id() -> None:
    """Re-saving the same id replaces in place instead of duplicating."""
    repo = InMemoryWebhookReceiptRepository()
    await repo.save(_receipt("r-1"))
    await repo.save(_receipt("r-1", status="processed"))

    items = await repo.list_items()
    assert len(items) == 1
    assert items[0].status == "processed"

    fetched = await repo.get(sid("r-1"))
    assert fetched is not None
    assert fetched.status == "processed"


async def test_save_keeps_distinct_ids() -> None:
    """Distinct ids are stored separately."""
    repo = InMemoryWebhookReceiptRepository()
    await repo.save(_receipt("r-1"))
    await repo.save(_receipt("r-2"))

    items = await repo.list_items()
    assert {i.id for i in items} == {as_uuid("r-1"), as_uuid("r-2")}
