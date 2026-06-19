"""Conformance tests for ``PruningRequestRepository``.

Dual-backend parity: a single assertion set runs against SQLite and
Postgres via the ``backend`` fixture. The repo is built over the
migrated ``backend.get_db()`` handle.

Covers id-keyed CRUD keyed by ``agent_id`` (save / upsert / get /
delete) and the ``list_items`` rehydration ordering, plus a full
round-trip of the rich :class:`PruningEvaluation` blob.
"""

from datetime import UTC, datetime, timedelta
from typing import cast

import aiosqlite
import pytest

from synthorg.approval.enums import ApprovalStatus
from synthorg.core.types import NotBlankStr
from synthorg.hr.performance.models import AgentPerformanceSnapshot
from synthorg.hr.pruning.models import PruningEvaluation, PruningRequest
from synthorg.persistence.postgres.pruning_request_repo import (
    PostgresPruningRequestRepository,
)
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.persistence.pruning_request_protocol import PruningRequestRepository
from synthorg.persistence.sqlite.pruning_request_repo import (
    SQLitePruningRequestRepository,
)
from tests._shared import as_uuid

pytestmark = pytest.mark.integration

_NOW = datetime(2026, 5, 20, 12, 0, tzinfo=UTC)


def _repo(backend: PersistenceBackend) -> PruningRequestRepository:
    name = backend.backend_name
    handle = backend.get_db()
    if name == "sqlite":
        return SQLitePruningRequestRepository(
            cast("aiosqlite.Connection", handle),
            write_context=backend.write_context,
        )
    if name == "postgres":
        from psycopg_pool import AsyncConnectionPool

        return PostgresPruningRequestRepository(cast("AsyncConnectionPool", handle))
    msg = f"Unknown backend: {name}"
    raise ValueError(msg)


def _evaluation(*, agent_id: str = "agent-1") -> PruningEvaluation:
    return PruningEvaluation(
        agent_id=NotBlankStr(agent_id),
        eligible=True,
        reasons=(NotBlankStr("quality below threshold"),),
        scores={"quality": 2.5},
        policy_name=NotBlankStr("quality_threshold"),
        snapshot=AgentPerformanceSnapshot(
            agent_id=NotBlankStr(agent_id),
            computed_at=_NOW,
            windows=(),
            trends=(),
            overall_quality_score=2.5,
            overall_collaboration_score=4.0,
        ),
        evaluated_at=_NOW,
    )


def _request(
    *,
    agent_id: str = "agent-1",
    request_id: str = "pruning-request-1",
    created_at: datetime = _NOW,
) -> PruningRequest:
    return PruningRequest(
        id=as_uuid(request_id),
        agent_id=NotBlankStr(agent_id),
        agent_name=NotBlankStr(f"Agent {agent_id}"),
        evaluation=_evaluation(agent_id=agent_id),
        approval_id=NotBlankStr(f"approval-{agent_id}"),
        status=ApprovalStatus.PENDING,
        created_at=created_at,
    )


class TestPruningRequestCrud:
    async def test_save_and_get_round_trip(self, backend: PersistenceBackend) -> None:
        repo = _repo(backend)
        await repo.save(_request())

        fetched = await repo.get(NotBlankStr("agent-1"))
        assert fetched is not None
        assert fetched.status is ApprovalStatus.PENDING
        assert fetched.approval_id == "approval-agent-1"
        # The rich evaluation blob round-trips intact.
        assert fetched.evaluation.policy_name == "quality_threshold"
        assert fetched.evaluation.scores == {"quality": 2.5}
        assert fetched.evaluation.snapshot.overall_quality_score == 2.5

    async def test_get_returns_none_when_absent(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _repo(backend)
        assert await repo.get(NotBlankStr("nope")) is None

    async def test_save_upsert_replaces_per_agent(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _repo(backend)
        await repo.save(_request(request_id="pruning-request-1"))
        await repo.save(_request(request_id="pruning-request-2"))

        items = await repo.list_items()
        assert len(items) == 1
        assert str(items[0].id) == str(as_uuid("pruning-request-2"))

    async def test_delete(self, backend: PersistenceBackend) -> None:
        repo = _repo(backend)
        await repo.save(_request())
        assert await repo.delete(NotBlankStr("agent-1")) is True
        assert await repo.delete(NotBlankStr("agent-1")) is False
        assert await repo.get(NotBlankStr("agent-1")) is None


class TestPruningRequestList:
    async def test_list_items_oldest_first(self, backend: PersistenceBackend) -> None:
        repo = _repo(backend)
        for index in range(3):
            await repo.save(
                _request(
                    agent_id=f"agent-{index}",
                    request_id=f"pruning-request-{index}",
                    created_at=_NOW + timedelta(seconds=index),
                )
            )

        items = await repo.list_items()
        assert [r.agent_id for r in items] == ["agent-0", "agent-1", "agent-2"]
