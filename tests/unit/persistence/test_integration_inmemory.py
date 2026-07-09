"""Unit tests for the in-memory integration repositories.

These doubles must mirror the durable repos' insert-or-replace
semantics so test code that swaps them in does not observe behaviour
(e.g. duplicate rows on re-save) the real backends never produce.
"""

from datetime import UTC, datetime, timedelta

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.integrations.connections.models import OAuthState, WebhookReceipt
from synthorg.persistence.integration_inmemory import (
    InMemoryOAuthStateRepository,
    InMemoryWebhookReceiptRepository,
)
from tests._shared import as_uuid, sid

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 5, 20, 12, 0, 0, tzinfo=UTC)


def _receipt(receipt_id: str, *, status: str = "received") -> WebhookReceipt:
    return WebhookReceipt(
        id=as_uuid(receipt_id),
        connection_name=NotBlankStr("conn-1"),
        event_type=NotBlankStr("push"),
        status=NotBlankStr(status),
    )


def _oauth_state(token: str) -> OAuthState:
    return OAuthState(
        state_token=NotBlankStr(token),
        connection_name=NotBlankStr("conn-1"),
        created_at=_NOW,
        expires_at=_NOW + timedelta(minutes=10),
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


async def test_update_status_updates_existing_receipt() -> None:
    """update_status mutates the stored row and reports the hit."""
    repo = InMemoryWebhookReceiptRepository()
    await repo.save(_receipt("r-1"))

    updated = await repo.update_status(
        sid("r-1"), status="processed", processed_at=_NOW, error=None
    )

    assert updated is True
    fetched = await repo.get(sid("r-1"))
    assert fetched is not None
    assert fetched.status == "processed"
    assert fetched.processed_at == _NOW


async def test_update_status_missing_receipt_returns_false() -> None:
    """update_status on an absent id reports the miss without inserting."""
    repo = InMemoryWebhookReceiptRepository()

    updated = await repo.update_status(
        sid("absent"), status="processed", processed_at=_NOW, error=None
    )

    assert updated is False
    assert await repo.list_items() == ()


async def test_update_status_if_current_cas_matches() -> None:
    """CAS updates only when the current status equals expected."""
    repo = InMemoryWebhookReceiptRepository()
    await repo.save(_receipt("r-1", status="received"))

    updated = await repo.update_status_if_current(
        sid("r-1"),
        expected_status="received",
        status="processed",
        processed_at=_NOW,
        error=None,
    )

    assert updated is True
    fetched = await repo.get(sid("r-1"))
    assert fetched is not None
    assert fetched.status == "processed"


async def test_update_status_if_current_cas_mismatch_is_noop() -> None:
    """A lost CAS race leaves the row untouched and reports False."""
    repo = InMemoryWebhookReceiptRepository()
    await repo.save(_receipt("r-1", status="processed"))

    updated = await repo.update_status_if_current(
        sid("r-1"),
        expected_status="received",
        status="failed",
        processed_at=_NOW,
        error="boom",
    )

    assert updated is False
    fetched = await repo.get(sid("r-1"))
    assert fetched is not None
    assert fetched.status == "processed"


async def test_mark_consumed_stamps_once_then_rejects_replay() -> None:
    """mark_consumed is compare-and-set: the first call wins, replays lose."""
    repo = InMemoryOAuthStateRepository()
    await repo.save(_oauth_state("tok-1"))

    first = await repo.mark_consumed(
        NotBlankStr("tok-1"),
        connection_name=NotBlankStr("conn-1"),
        consumed_at=_NOW,
    )
    second = await repo.mark_consumed(
        NotBlankStr("tok-1"),
        connection_name=NotBlankStr("conn-1"),
        consumed_at=_NOW,
    )

    assert first is True
    assert second is False
    stored = await repo.get("tok-1")
    assert stored is not None
    assert stored.consumed_at == _NOW
    assert stored.connection_name_returned == "conn-1"


async def test_mark_consumed_missing_token_returns_false() -> None:
    """mark_consumed on an unknown token reports the miss."""
    repo = InMemoryOAuthStateRepository()

    consumed = await repo.mark_consumed(
        NotBlankStr("absent"),
        connection_name=NotBlankStr("conn-1"),
        consumed_at=_NOW,
    )

    assert consumed is False
