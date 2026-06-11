"""Tests for :class:`SQLiteWebhookReceiptRepository`."""

from datetime import UTC, datetime

import aiosqlite
import pytest

from synthorg.core.persistence_errors import MalformedRowError
from synthorg.core.types import NotBlankStr
from synthorg.integrations.connections.models import WebhookReceipt
from synthorg.persistence.sqlite.webhook_receipt_repo import (
    SQLiteWebhookReceiptRepository,
)
from tests._shared import as_pk, as_uuid, sid
from tests._shared.persistence import make_private_write_context

pytestmark = pytest.mark.unit


@pytest.fixture
def repo(
    migrated_db: aiosqlite.Connection,
) -> SQLiteWebhookReceiptRepository:
    return SQLiteWebhookReceiptRepository(
        migrated_db, write_context=make_private_write_context()
    )


def _receipt(receipt_id: str = "rcpt-001") -> WebhookReceipt:
    return WebhookReceipt(
        id=as_pk(receipt_id),
        connection_name=NotBlankStr("github-bot"),
        event_type="push",
        status="received",
        received_at=datetime(2026, 5, 13, 10, 0, 0, tzinfo=UTC),
        payload_json='{"event":"push"}',
    )


async def test_save_and_get_roundtrip(
    repo: SQLiteWebhookReceiptRepository,
) -> None:
    receipt = _receipt()
    await repo.save(receipt)

    fetched = await repo.get(sid("rcpt-001"))

    assert fetched is not None
    assert fetched.id == as_uuid("rcpt-001")
    assert fetched.payload_json == '{"event":"push"}'


async def test_get_rejects_non_uuid_id(
    repo: SQLiteWebhookReceiptRepository,
    migrated_db: aiosqlite.Connection,
) -> None:
    """A non-UUID stored id is rejected at read instead of silently passing."""
    await repo.save(_receipt())
    await migrated_db.execute(
        "UPDATE webhook_receipts SET id = ? WHERE id = ?",
        ("not-a-uuid", sid("rcpt-001")),
    )
    await migrated_db.commit()

    with pytest.raises(MalformedRowError, match="Failed to deserialize"):
        await repo.get("not-a-uuid")


def test_id_default_factory_mints_unique_ids() -> None:
    # Guards against ``default=uuid4()`` (one shared id) being used in
    # place of ``default_factory=uuid4`` (a fresh id per instance).
    first = WebhookReceipt(
        connection_name=NotBlankStr("github-bot"),
        event_type="push",
        status="received",
        received_at=datetime(2026, 5, 13, 10, 0, 0, tzinfo=UTC),
        payload_json="{}",
    )
    second = WebhookReceipt(
        connection_name=NotBlankStr("github-bot"),
        event_type="push",
        status="received",
        received_at=datetime(2026, 5, 13, 10, 0, 0, tzinfo=UTC),
        payload_json="{}",
    )
    assert first.id != second.id
