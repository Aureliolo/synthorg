"""Conformance tests for ``UpgradeRecommendationRepository``.

Not exposed on ``PersistenceBackend`` (the lifecycle wires it directly),
so the repo is built over the migrated ``backend.get_db()`` handle.
SQLite + Postgres share one assertion set.
"""

from datetime import UTC, datetime
from typing import cast

import aiosqlite
import pytest

from synthorg.persistence.postgres.upgrade_recommendation_repo import (
    PostgresUpgradeRecommendationRepository,
)
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.persistence.sqlite.upgrade_recommendation_repo import (
    SQLiteUpgradeRecommendationRepository,
)
from synthorg.persistence.upgrade_recommendation_protocol import (
    UpgradeRecommendationFilterSpec,
    UpgradeRecommendationRepository,
)
from synthorg.providers.enums import RecommendationStatus
from synthorg.providers.management.upgrade_models import (
    StoredUpgradeRecommendation,
    UpgradeRecommendation,
)
from tests._shared import as_uuid

pytestmark = pytest.mark.integration

_NOW = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


def _repo(backend: PersistenceBackend) -> UpgradeRecommendationRepository:
    """Return a concrete recommendation repository bound to *backend*."""
    name = backend.backend_name
    handle = backend.get_db()
    if name == "sqlite":
        return SQLiteUpgradeRecommendationRepository(
            cast("aiosqlite.Connection", handle),
            write_context=backend.write_context,
        )
    if name == "postgres":
        from psycopg_pool import AsyncConnectionPool

        return PostgresUpgradeRecommendationRepository(
            cast("AsyncConnectionPool", handle),
        )
    msg = f"Unknown backend: {name}"
    raise ValueError(msg)


def _make(
    *,
    rec_id: str = "rec-001",
    status: RecommendationStatus = RecommendationStatus.PENDING,
) -> StoredUpgradeRecommendation:
    # A decided status carries its decision pair; the entity invariant
    # (and the backend CHECK) require ``decided_at`` + ``decided_by``
    # together exactly when the status is not PENDING.
    decided = status is not RecommendationStatus.PENDING
    return StoredUpgradeRecommendation(
        id=as_uuid(rec_id),
        recommendation=UpgradeRecommendation(
            provider_name="example-provider",
            current_model_id="example-large-001",
            recommended_model_id="example-large-002",
            family="example-large",
            current_generation=1.0,
            recommended_generation=2.0,
            score=0.7,
            reason="newer in-family model available",
        ),
        agent_ids=("agent-a", "agent-b"),
        status=status,
        created_at=_NOW,
        decided_at=_NOW if decided else None,
        decided_by="operator" if decided else None,
    )


class TestUpgradeRecommendationRepository:
    async def test_save_and_get(self, backend: PersistenceBackend) -> None:
        repo = _repo(backend)
        entity = _make()
        await repo.save(entity)
        fetched = await repo.get(entity.id)
        assert fetched is not None
        assert fetched.id == entity.id
        assert fetched.recommendation.recommended_model_id == "example-large-002"
        assert fetched.agent_ids == ("agent-a", "agent-b")
        assert fetched.status is RecommendationStatus.PENDING
        assert fetched.created_at.tzinfo is not None

    async def test_get_returns_none_when_absent(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _repo(backend)
        assert await repo.get(as_uuid("rec-missing")) is None

    async def test_save_commits_visible_to_fresh_repo(
        self, backend: PersistenceBackend
    ) -> None:
        await _repo(backend).save(_make(rec_id="rec-commit"))
        fetched = await _repo(backend).get(as_uuid("rec-commit"))
        assert fetched is not None

    async def test_query_filter_by_status(self, backend: PersistenceBackend) -> None:
        repo = _repo(backend)
        await repo.save(_make(rec_id="rec-pending"))
        await repo.save(
            _make(rec_id="rec-approved", status=RecommendationStatus.APPROVED),
        )
        rows = await repo.query(
            UpgradeRecommendationFilterSpec(status=RecommendationStatus.APPROVED),
        )
        ids = {r.id for r in rows}
        assert as_uuid("rec-approved") in ids
        assert as_uuid("rec-pending") not in ids

    async def test_count(self, backend: PersistenceBackend) -> None:
        repo = _repo(backend)
        await repo.save(_make(rec_id="rec-c1"))
        await repo.save(_make(rec_id="rec-c2"))
        assert await repo.count(UpgradeRecommendationFilterSpec()) >= 2

    async def test_transition_if_sets_decision_columns(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _repo(backend)
        entity = _make(rec_id="rec-transition")
        await repo.save(entity)
        decided = datetime(2026, 6, 2, 9, 0, tzinfo=UTC)
        result = await repo.transition_if(
            entity.id,
            from_state=RecommendationStatus.PENDING,
            to_state=RecommendationStatus.APPROVED,
            decided_at=decided,
            decided_by="operator-001",
        )
        assert result is True
        fetched = await repo.get(entity.id)
        assert fetched is not None
        assert fetched.status is RecommendationStatus.APPROVED
        assert fetched.decided_by == "operator-001"
        assert fetched.decided_at == decided

    async def test_transition_if_returns_false_on_mismatch(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _repo(backend)
        entity = _make(rec_id="rec-mismatch", status=RecommendationStatus.REJECTED)
        await repo.save(entity)
        result = await repo.transition_if(
            entity.id,
            from_state=RecommendationStatus.PENDING,
            to_state=RecommendationStatus.APPROVED,
        )
        assert result is False

    async def test_transition_if_rejects_unknown_update_key(
        self, backend: PersistenceBackend
    ) -> None:
        from synthorg.core.persistence_errors import QueryError

        repo = _repo(backend)
        entity = _make(rec_id="rec-badkey")
        await repo.save(entity)
        with pytest.raises(QueryError):
            await repo.transition_if(
                entity.id,
                from_state=RecommendationStatus.PENDING,
                to_state=RecommendationStatus.APPROVED,
                bogus="x",
            )

    async def test_delete_returns_true_then_false(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _repo(backend)
        entity = _make(rec_id="rec-del")
        await repo.save(entity)
        assert await repo.delete(entity.id) is True
        assert await repo.get(entity.id) is None
        assert await repo.delete(entity.id) is False

    async def test_protocol_runtime_check(self, backend: PersistenceBackend) -> None:
        assert isinstance(_repo(backend), UpgradeRecommendationRepository)
