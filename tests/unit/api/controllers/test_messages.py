"""Tests for message controller."""

from typing import Any
from uuid import uuid4

import pytest
from litestar.testing import TestClient

from tests.unit.persistence.conftest import make_message


@pytest.mark.unit
class TestMessageController:
    def test_list_messages_no_channel(self, test_client: TestClient[Any]) -> None:
        resp = test_client.get("/api/v1/messages")
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"] == []

    def test_list_channels(self, test_client: TestClient[Any]) -> None:
        resp = test_client.get("/api/v1/messages/channels")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert isinstance(body["data"], list)
        # Paginated envelope: ``pagination`` is always present and the
        # consistency validator keeps ``has_more`` and ``next_cursor``
        # in lockstep (empty bus -> both falsy).
        assert "pagination" in body
        assert body["pagination"]["has_more"] is False
        assert body["pagination"]["next_cursor"] is None


@pytest.mark.unit
class TestMessageControllerDelete:
    """Controller-layer coverage for ``DELETE /messages/{message_id}``."""

    async def test_delete_returns_200_when_message_exists(
        self,
        test_client: TestClient[Any],
        fake_persistence: Any,
    ) -> None:
        msg = make_message(msg_id=uuid4(), channel="ops")
        await fake_persistence.messages.save(msg)

        resp = test_client.delete(f"/api/v1/messages/{msg.id}")

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"] is None

    async def test_delete_accepts_optional_channel_query_param(
        self,
        test_client: TestClient[Any],
        fake_persistence: Any,
    ) -> None:
        """``?channel=X`` is recorded in the audit log for parity with the MCP path."""
        msg = make_message(msg_id=uuid4(), channel="ops")
        await fake_persistence.messages.save(msg)

        resp = test_client.delete(
            f"/api/v1/messages/{msg.id}",
            params={"channel": "ops"},
        )

        assert resp.status_code == 200
        assert resp.json()["data"] is None

    def test_delete_returns_404_when_id_missing(
        self,
        test_client: TestClient[Any],
    ) -> None:
        resp = test_client.delete(f"/api/v1/messages/{uuid4()}")

        assert resp.status_code == 404
        body = resp.json()
        assert "not found" in body["error"].lower()
