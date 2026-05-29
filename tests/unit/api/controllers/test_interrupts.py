"""Tests for InterruptController (polling fallback)."""

from datetime import UTC, datetime

import pytest

from synthorg.communication.event_stream.interrupt import (
    Interrupt,
    InterruptStore,
    InterruptType,
)
from tests._shared import LoopAsyncClient
from tests.unit.api.conftest import make_auth_headers

_WRITE_HEADERS = make_auth_headers("ceo")
_READ_HEADERS = make_auth_headers("observer")
_BASE = "/api/v1/interrupts"


@pytest.mark.unit
class TestListInterrupts:
    async def test_list_empty(self, async_test_client: LoopAsyncClient) -> None:
        resp = await async_test_client.get(_BASE, headers=_READ_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"] == []

    async def test_list_with_pending(
        self,
        async_test_client: LoopAsyncClient,
        interrupt_store: InterruptStore,
    ) -> None:
        interrupt = Interrupt(
            id="int-list-001",
            type=InterruptType.TOOL_APPROVAL,
            session_id="s1",
            agent_id="agent-001",
            created_at=datetime(2026, 4, 13, tzinfo=UTC),
            timeout_seconds=300.0,
            tool_name="deploy",
        )
        await interrupt_store.create(interrupt)

        resp = await async_test_client.get(_BASE, headers=_READ_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        ids = [item["id"] for item in body["data"]]
        assert "int-list-001" in ids

    async def test_list_filtered_by_session(
        self,
        async_test_client: LoopAsyncClient,
        interrupt_store: InterruptStore,
    ) -> None:
        await interrupt_store.create(
            Interrupt(
                id="int-filter-s1",
                type=InterruptType.TOOL_APPROVAL,
                session_id="session-filter-1",
                agent_id="agent-001",
                created_at=datetime(2026, 4, 13, tzinfo=UTC),
                timeout_seconds=300.0,
                tool_name="deploy",
            ),
        )
        await interrupt_store.create(
            Interrupt(
                id="int-filter-s2",
                type=InterruptType.TOOL_APPROVAL,
                session_id="session-filter-2",
                agent_id="agent-001",
                created_at=datetime(2026, 4, 13, tzinfo=UTC),
                timeout_seconds=300.0,
                tool_name="deploy",
            ),
        )

        resp = await async_test_client.get(
            _BASE,
            params={"session_id": "session-filter-1"},
            headers=_READ_HEADERS,
        )
        assert resp.status_code == 200
        body = resp.json()
        ids = [i["id"] for i in body["data"]]
        assert "int-filter-s1" in ids
        assert "int-filter-s2" not in ids


@pytest.mark.unit
class TestResumeInterrupt:
    async def test_resume_nonexistent_404(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        resp = await async_test_client.post(
            f"{_BASE}/nonexistent/resume",
            json={"decision": "approve"},
            headers=_WRITE_HEADERS,
        )
        assert resp.status_code == 404

    async def test_resume_success(
        self,
        async_test_client: LoopAsyncClient,
        interrupt_store: InterruptStore,
    ) -> None:
        await interrupt_store.create(
            Interrupt(
                id="int-resume-poll",
                type=InterruptType.TOOL_APPROVAL,
                session_id="s1",
                agent_id="agent-001",
                created_at=datetime(2026, 4, 13, tzinfo=UTC),
                timeout_seconds=300.0,
                tool_name="deploy",
            ),
        )
        resp = await async_test_client.post(
            f"{_BASE}/int-resume-poll/resume",
            json={"decision": "approve"},
            headers=_WRITE_HEADERS,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["status"] == "resumed"
