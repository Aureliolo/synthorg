"""WebhookActivityService unit tests.

Covers the activity-service contract that the webhooks controller now
routes ``list_activity`` through: limit validation, audit logging on
every call, and pass-through delegation to the receipt repository.
"""

from datetime import UTC, datetime
from typing import cast
from unittest.mock import AsyncMock, create_autospec

import pytest
import structlog

from synthorg.core.domain_errors import ValidationError
from synthorg.core.types import NotBlankStr
from synthorg.integrations.connections.models import WebhookReceipt
from synthorg.integrations.webhooks.activity_service import (
    WebhookActivityService,
)
from synthorg.observability.events.integrations import WEBHOOK_ACTIVITY_LISTED
from synthorg.persistence.connection_protocol import WebhookReceiptRepository

pytestmark = pytest.mark.unit


def _make_receipt(receipt_id: str) -> WebhookReceipt:
    return WebhookReceipt(
        id=NotBlankStr(receipt_id),
        connection_name=NotBlankStr("test-conn"),
        event_type="event.test",
        status="received",
        received_at=datetime(2026, 5, 14, tzinfo=UTC),
    )


def _make_repo(
    receipts: tuple[WebhookReceipt, ...] = (),
) -> WebhookReceiptRepository:
    repo = create_autospec(WebhookReceiptRepository, instance=True, spec_set=True)
    repo.get_by_connection = AsyncMock(return_value=receipts)
    return cast("WebhookReceiptRepository", repo)


class TestListActivity:
    """``list_activity`` delegates and audits but validates first."""

    async def test_returns_receipts_from_repo(self) -> None:
        receipts = (_make_receipt("r-1"), _make_receipt("r-2"))
        service = WebhookActivityService(receipts_repo=_make_repo(receipts))

        result = await service.list_activity(
            connection_name=NotBlankStr("test-conn"),
            limit=100,
        )
        assert result == receipts

    async def test_audit_event_emitted_on_success(self) -> None:
        receipts = (_make_receipt("r-1"),)
        service = WebhookActivityService(receipts_repo=_make_repo(receipts))

        with structlog.testing.capture_logs() as logs:
            await service.list_activity(
                connection_name=NotBlankStr("acme"),
                limit=50,
            )
        listed = [e for e in logs if e.get("event") == WEBHOOK_ACTIVITY_LISTED]
        assert len(listed) == 1
        assert listed[0]["connection_name"] == "acme"
        assert listed[0]["limit"] == 50
        assert listed[0]["count"] == 1

    @pytest.mark.parametrize("bad_limit", [0, -1, -100])
    async def test_limit_below_one_raises_validation_error(
        self,
        bad_limit: int,
    ) -> None:
        service = WebhookActivityService(receipts_repo=_make_repo())
        with pytest.raises(ValidationError, match="limit must be between"):
            await service.list_activity(
                connection_name=NotBlankStr("test-conn"),
                limit=bad_limit,
            )

    @pytest.mark.parametrize("bad_limit", [501, 1000, 10_000])
    async def test_limit_above_max_raises_validation_error(
        self,
        bad_limit: int,
    ) -> None:
        service = WebhookActivityService(receipts_repo=_make_repo())
        with pytest.raises(ValidationError, match="limit must be between"):
            await service.list_activity(
                connection_name=NotBlankStr("test-conn"),
                limit=bad_limit,
            )

    async def test_repo_not_called_on_validation_error(self) -> None:
        """Bad limit must not reach the persistence layer."""
        repo = _make_repo()
        service = WebhookActivityService(receipts_repo=repo)
        with pytest.raises(ValidationError):
            await service.list_activity(
                connection_name=NotBlankStr("test-conn"),
                limit=0,
            )
        repo.get_by_connection.assert_not_called()  # type: ignore[attr-defined]

    async def test_limit_passed_through_to_repo(self) -> None:
        repo = _make_repo()
        service = WebhookActivityService(receipts_repo=repo)
        await service.list_activity(
            connection_name=NotBlankStr("test-conn"),
            limit=42,
        )
        repo.get_by_connection.assert_awaited_once_with(  # type: ignore[attr-defined]
            NotBlankStr("test-conn"),
            limit=42,
        )
