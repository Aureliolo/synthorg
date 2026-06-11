"""Conformance tests for ``ParkedContextRepository``."""

from datetime import UTC, datetime

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.execution.parked_context import ParkedContext
from synthorg.persistence.protocol import PersistenceBackend
from tests._shared import as_uuid, sid

pytestmark = pytest.mark.integration

_NOW = datetime(2026, 3, 15, 10, 0, 0, tzinfo=UTC)


def _parked(
    *,
    parked_id: str = "park-001",
    execution_id: str = "exec-001",
    agent_id: str = "agent-001",
    approval_id: str = "appr-001",
    task_id: str | None = "task-001",
) -> ParkedContext:
    return ParkedContext(
        id=as_uuid(parked_id),
        execution_id=NotBlankStr(execution_id),
        agent_id=NotBlankStr(agent_id),
        task_id=NotBlankStr(task_id) if task_id else None,
        approval_id=NotBlankStr(approval_id),
        parked_at=_NOW,
        context_json='{"turn": 3}',
        metadata={"tool": "send_email"},
    )


class TestParkedContextRepository:
    async def test_save_and_get(self, backend: PersistenceBackend) -> None:
        await backend.parked_contexts.save(_parked())

        fetched = await backend.parked_contexts.get(sid("park-001"))
        assert fetched is not None
        assert fetched.id == as_uuid("park-001")
        assert fetched.execution_id == "exec-001"
        assert fetched.metadata == {"tool": "send_email"}

    async def test_get_missing_returns_none(self, backend: PersistenceBackend) -> None:
        assert await backend.parked_contexts.get(NotBlankStr("ghost")) is None

    async def test_get_by_approval(self, backend: PersistenceBackend) -> None:
        await backend.parked_contexts.save(_parked(approval_id="appr-xyz"))

        fetched = await backend.parked_contexts.get_by_approval(
            NotBlankStr("appr-xyz"),
        )
        assert fetched is not None
        assert fetched.approval_id == "appr-xyz"

    async def test_get_by_agent(self, backend: PersistenceBackend) -> None:
        await backend.parked_contexts.save(_parked(parked_id="p1", agent_id="alice"))
        await backend.parked_contexts.save(_parked(parked_id="p2", agent_id="alice"))
        await backend.parked_contexts.save(_parked(parked_id="p3", agent_id="bob"))

        alice_rows = await backend.parked_contexts.get_by_agent(NotBlankStr("alice"))
        ids = {r.id for r in alice_rows}
        assert ids == {as_uuid("p1"), as_uuid("p2")}

    async def test_list_items_returns_all_in_id_order(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.parked_contexts.save(
            _parked(parked_id="list-b", approval_id="appr-list-b"),
        )
        await backend.parked_contexts.save(
            _parked(parked_id="list-a", approval_id="appr-list-a"),
        )

        results = await backend.parked_contexts.list_items()
        expected = {as_uuid("list-a"), as_uuid("list-b")}
        scoped = [r.id for r in results if r.id in expected]
        assert scoped == sorted(expected, key=str)

    async def test_delete_existing(self, backend: PersistenceBackend) -> None:
        await backend.parked_contexts.save(_parked())

        deleted = await backend.parked_contexts.delete(sid("park-001"))
        assert deleted is True
        assert await backend.parked_contexts.get(sid("park-001")) is None

    async def test_delete_missing(self, backend: PersistenceBackend) -> None:
        assert await backend.parked_contexts.delete(NotBlankStr("ghost")) is False

    async def test_taskless_roundtrip(self, backend: PersistenceBackend) -> None:
        await backend.parked_contexts.save(_parked(task_id=None))

        fetched = await backend.parked_contexts.get(sid("park-001"))
        assert fetched is not None
        assert fetched.task_id is None
