"""Conformance tests for ``ProviderFailoverEventRepository``.

Dual-backend parity: a single assertion set runs against SQLite and
Postgres via the ``backend`` fixture in
``tests/conformance/persistence/conftest.py``. The repo is reached through
``backend.provider_failover_events``, the same accessor the dispatch path
uses.

Covers:

* Append and read-back of both pairs, so "which connection served this"
  survives a restart.
* Newest-first ordering, which is the order the operator question arrives in.
* Each filter narrowing independently, and combining.
* An owner-less engagement (system work belongs to no agent and no task)
  round-tripping as ``None`` rather than as some invented id.
* Retention purge by timestamp.
* Invalid pagination args raising :class:`QueryError`.
"""

from datetime import UTC, datetime
from uuid import UUID

import pytest

from synthorg.core.persistence_errors import QueryError
from synthorg.core.types import NotBlankStr
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.persistence.provider_failover_event_protocol import (
    ProviderFailoverEventFilterSpec,
    ProviderFailoverEventRepository,
)
from synthorg.providers.failover_event import FailoverStage, ProviderFailoverEvent
from synthorg.providers.health import ProviderOutcomeClass

pytestmark = pytest.mark.integration

_EARLY = datetime(2026, 5, 20, 9, 0, tzinfo=UTC)
_LATE = datetime(2026, 5, 21, 9, 0, tzinfo=UTC)
_ALL = ProviderFailoverEventFilterSpec()


def _repo(backend: PersistenceBackend) -> ProviderFailoverEventRepository:
    """Return the failover event repository *backend* exposes."""
    return backend.provider_failover_events


_DECLARED = ("example-provider", "example-expert-001")
_SERVED = ("test-provider", "example-capable-001")
_OWNER = ("agent-7", "task-9")


def _event(
    label: str = "00000000-0000-4000-8000-000000000001",
    *,
    occurred_at: datetime = _LATE,
    feature: str = "engine.reasoning",
    declared: tuple[str, str] = _DECLARED,
    served: tuple[str, str] = _SERVED,
    trigger_stage: FailoverStage = "retry",
    owner: tuple[str, str] | None = _OWNER,
) -> ProviderFailoverEvent:
    return ProviderFailoverEvent(
        id=UUID(label),
        occurred_at=occurred_at,
        feature=NotBlankStr(feature),
        declared_provider=NotBlankStr(declared[0]),
        declared_model=NotBlankStr(declared[1]),
        served_provider=NotBlankStr(served[0]),
        served_model=NotBlankStr(served[1]),
        trigger_class=ProviderOutcomeClass.OVERLOADED,
        trigger_stage=trigger_stage,
        agent_id=None if owner is None else NotBlankStr(owner[0]),
        task_id=None if owner is None else NotBlankStr(owner[1]),
    )


class TestAppendAndRead:
    async def test_both_pairs_round_trip(self, backend: PersistenceBackend) -> None:
        """Recording only the declared pair would answer the wrong question."""
        repo = _repo(backend)
        await repo.append(_event())

        stored = await repo.query(_ALL)

        assert len(stored) == 1
        assert stored[0].declared_provider == "example-provider"
        assert stored[0].declared_model == "example-expert-001"
        assert stored[0].served_provider == "test-provider"
        assert stored[0].served_model == "example-capable-001"
        assert stored[0].trigger_class is ProviderOutcomeClass.OVERLOADED
        assert stored[0].trigger_stage == "retry"
        assert stored[0].occurred_at == _LATE

    async def test_preflight_stage_round_trips(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _repo(backend)
        await repo.append(_event(trigger_stage="preflight"))

        stored = await repo.query(_ALL)

        assert stored[0].trigger_stage == "preflight"

    async def test_system_work_has_no_owner(self, backend: PersistenceBackend) -> None:
        """A dispatch outside a run belongs to no agent, and says so."""
        repo = _repo(backend)
        await repo.append(_event(owner=None))

        stored = await repo.query(_ALL)

        assert stored[0].agent_id is None
        assert stored[0].task_id is None

    async def test_newest_first(self, backend: PersistenceBackend) -> None:
        repo = _repo(backend)
        await repo.append(_event(occurred_at=_EARLY))
        await repo.append(
            _event("00000000-0000-4000-8000-000000000002", occurred_at=_LATE)
        )

        stored = await repo.query(_ALL)

        assert [e.occurred_at for e in stored] == [_LATE, _EARLY]


class TestFiltering:
    async def test_by_feature(self, backend: PersistenceBackend) -> None:
        repo = _repo(backend)
        await repo.append(_event(feature="engine.reasoning"))
        await repo.append(
            _event("00000000-0000-4000-8000-000000000002", feature="memory.summariser")
        )

        stored = await repo.query(
            ProviderFailoverEventFilterSpec(feature=NotBlankStr("memory.summariser"))
        )

        assert len(stored) == 1
        assert stored[0].feature == "memory.summariser"

    async def test_by_declared_provider(self, backend: PersistenceBackend) -> None:
        repo = _repo(backend)
        await repo.append(_event())
        await repo.append(
            _event(
                "00000000-0000-4000-8000-000000000002",
                declared=("other-provider", "example-expert-001"),
            )
        )

        stored = await repo.query(
            ProviderFailoverEventFilterSpec(
                declared_provider=NotBlankStr("other-provider")
            )
        )

        assert len(stored) == 1
        assert stored[0].declared_provider == "other-provider"

    async def test_since_excludes_older(self, backend: PersistenceBackend) -> None:
        repo = _repo(backend)
        await repo.append(_event(occurred_at=_EARLY))
        await repo.append(
            _event("00000000-0000-4000-8000-000000000002", occurred_at=_LATE)
        )

        stored = await repo.query(ProviderFailoverEventFilterSpec(since=_LATE))

        assert len(stored) == 1
        assert stored[0].occurred_at == _LATE

    async def test_filters_combine(self, backend: PersistenceBackend) -> None:
        repo = _repo(backend)
        await repo.append(_event(feature="engine.reasoning", occurred_at=_EARLY))
        await repo.append(
            _event(
                "00000000-0000-4000-8000-000000000002",
                feature="engine.reasoning",
                occurred_at=_LATE,
            )
        )
        await repo.append(
            _event(
                "00000000-0000-4000-8000-000000000003",
                feature="memory.summariser",
                occurred_at=_LATE,
            )
        )

        stored = await repo.query(
            ProviderFailoverEventFilterSpec(
                feature=NotBlankStr("engine.reasoning"), since=_LATE
            )
        )

        assert len(stored) == 1
        assert stored[0].id == UUID("00000000-0000-4000-8000-000000000002")

    async def test_paginates(self, backend: PersistenceBackend) -> None:
        repo = _repo(backend)
        await repo.append(_event(occurred_at=_EARLY))
        await repo.append(
            _event("00000000-0000-4000-8000-000000000002", occurred_at=_LATE)
        )

        page = await repo.query(_ALL, limit=1, offset=1)

        assert len(page) == 1
        assert page[0].occurred_at == _EARLY

    async def test_invalid_pagination_rejected(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _repo(backend)
        with pytest.raises(QueryError):
            await repo.query(_ALL, limit=0)
        with pytest.raises(QueryError):
            await repo.query(_ALL, offset=-1)


class TestRetention:
    async def test_purge_before_removes_only_older(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _repo(backend)
        await repo.append(_event(occurred_at=_EARLY))
        await repo.append(
            _event("00000000-0000-4000-8000-000000000002", occurred_at=_LATE)
        )

        removed = await repo.purge_before(_LATE)

        assert removed == 1
        remaining = await repo.query(_ALL)
        assert len(remaining) == 1
        assert remaining[0].occurred_at == _LATE

    async def test_purge_with_nothing_older_removes_nothing(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _repo(backend)
        await repo.append(_event(occurred_at=_LATE))

        assert await repo.purge_before(_EARLY) == 0
        assert len(await repo.query(_ALL)) == 1
