"""Unit tests for :class:`MessageService`.

Covers the rewritten ``delete_message`` path: routes through the
persistence repository, returns the deletion bool, and emits the
audit-grade ``COMMUNICATION_MESSAGE_DELETED`` event on success only.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import structlog.testing

from synthorg.communication.bus_protocol import MessageBus
from synthorg.communication.messages.service import MessageService
from synthorg.core.types import NotBlankStr
from synthorg.observability.events.communication import (
    COMMUNICATION_MESSAGE_DELETED,
)

pytestmark = pytest.mark.unit


def _make_service(*, deleted: bool) -> tuple[MessageService, AsyncMock]:
    repo = AsyncMock()
    repo.delete = AsyncMock(return_value=deleted)
    persistence = SimpleNamespace(messages=repo)
    bus = AsyncMock(spec=MessageBus)
    service = MessageService(bus=bus, persistence=persistence)
    return service, repo


class TestMessageServiceDelete:
    """``MessageService.delete_message`` end-to-end behavior."""

    async def test_returns_true_and_emits_audit_on_success(self) -> None:
        service, repo = _make_service(deleted=True)

        with structlog.testing.capture_logs() as events:
            result = await service.delete_message(
                message_id=NotBlankStr("msg-1"),
                actor_id=NotBlankStr("user-1"),
                reason=NotBlankStr("operator user-deletion request"),
            )

        assert result is True
        repo.delete.assert_awaited_once_with("msg-1")
        audit = [e for e in events if e.get("event") == COMMUNICATION_MESSAGE_DELETED]
        assert len(audit) == 1
        assert "channel" not in audit[0]
        assert audit[0]["message_id"] == "msg-1"
        assert audit[0]["actor_id"] == "user-1"
        assert audit[0]["reason"] == "operator user-deletion request"

    async def test_returns_false_and_skips_audit_when_id_missing(self) -> None:
        service, repo = _make_service(deleted=False)

        with structlog.testing.capture_logs() as events:
            result = await service.delete_message(
                message_id=NotBlankStr("missing"),
                actor_id=NotBlankStr("user-1"),
                reason=NotBlankStr("cleanup"),
            )

        assert result is False
        repo.delete.assert_awaited_once_with("missing")
        audit = [e for e in events if e.get("event") == COMMUNICATION_MESSAGE_DELETED]
        assert audit == []
