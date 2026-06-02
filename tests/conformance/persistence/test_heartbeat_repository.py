"""Conformance tests for ``HeartbeatRepository`` (SQLite + Postgres)."""

from datetime import UTC, datetime, timedelta

import pytest

from synthorg.core.persistence_errors import QueryError
from synthorg.core.types import NotBlankStr
from synthorg.engine.checkpoint.models import Heartbeat
from synthorg.persistence.protocol import PersistenceBackend

pytestmark = pytest.mark.integration

_NOW = datetime(2026, 3, 15, 10, 0, 0, tzinfo=UTC)


def _heartbeat(
    *,
    execution_id: str = "exec-001",
    agent_id: str = "agent-001",
    task_id: str = "task-001",
    at: datetime = _NOW,
) -> Heartbeat:
    return Heartbeat(
        execution_id=NotBlankStr(execution_id),
        agent_id=NotBlankStr(agent_id),
        task_id=NotBlankStr(task_id),
        last_heartbeat_at=at,
    )


class TestHeartbeatRepository:
    async def test_save_and_get(self, backend: PersistenceBackend) -> None:
        hb = _heartbeat()
        await backend.heartbeats.save(hb)

        fetched = await backend.heartbeats.get(NotBlankStr("exec-001"))
        assert fetched is not None
        assert fetched.execution_id == "exec-001"
        assert fetched.agent_id == "agent-001"

    async def test_get_missing_returns_none(self, backend: PersistenceBackend) -> None:
        assert await backend.heartbeats.get(NotBlankStr("ghost")) is None

    async def test_save_upsert_overwrites(self, backend: PersistenceBackend) -> None:
        first = _heartbeat(at=_NOW)
        await backend.heartbeats.save(first)

        later = _NOW + timedelta(seconds=30)
        await backend.heartbeats.save(_heartbeat(at=later))

        fetched = await backend.heartbeats.get(NotBlankStr("exec-001"))
        assert fetched is not None
        assert fetched.last_heartbeat_at == later

    async def test_get_stale_returns_old_heartbeats(
        self, backend: PersistenceBackend
    ) -> None:
        old = _NOW - timedelta(minutes=10)
        fresh = _NOW - timedelta(seconds=5)
        await backend.heartbeats.save(
            _heartbeat(execution_id="stale", at=old),
        )
        await backend.heartbeats.save(
            _heartbeat(execution_id="fresh", at=fresh),
        )

        stale = await backend.heartbeats.get_stale(
            threshold=_NOW - timedelta(minutes=1),
        )
        ids = {h.execution_id for h in stale}
        assert "stale" in ids
        assert "fresh" not in ids

    async def test_get_stale_rejects_naive(self, backend: PersistenceBackend) -> None:
        with pytest.raises((ValueError, QueryError)):
            await backend.heartbeats.get_stale(
                threshold=datetime(2025, 1, 1),  # noqa: DTZ001 -- naive on purpose
            )

    async def test_delete_existing(self, backend: PersistenceBackend) -> None:
        await backend.heartbeats.save(_heartbeat())

        deleted = await backend.heartbeats.delete(NotBlankStr("exec-001"))
        assert deleted is True
        assert await backend.heartbeats.get(NotBlankStr("exec-001")) is None

    async def test_delete_missing(self, backend: PersistenceBackend) -> None:
        assert await backend.heartbeats.delete(NotBlankStr("ghost")) is False
