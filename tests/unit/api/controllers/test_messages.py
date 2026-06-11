"""Tests for message controller."""

from uuid import uuid4

import pytest

from tests._shared import LoopAsyncClient
from tests.unit.api.fakes import FakePersistenceBackend
from tests.unit.persistence.conftest import make_message


@pytest.mark.unit
class TestMessageController:
    async def test_list_messages_no_channel(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        resp = await async_test_client.get("/api/v1/messages")
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"] == []

    async def test_list_channels(self, async_test_client: LoopAsyncClient) -> None:
        resp = await async_test_client.get("/api/v1/messages/channels")
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
        async_test_client: LoopAsyncClient,
        fake_persistence: FakePersistenceBackend,
    ) -> None:
        msg = make_message(msg_id=uuid4(), channel="ops")
        await fake_persistence.messages.append(msg)

        resp = await async_test_client.delete(f"/api/v1/messages/{msg.id}")

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"] is None

    async def test_delete_returns_404_for_unknown_id(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        resp = await async_test_client.delete(f"/api/v1/messages/{uuid4()}")

        assert resp.status_code == 404
        body = resp.json()
        assert "not found" in body["error"].lower()
