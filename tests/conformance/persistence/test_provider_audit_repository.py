"""Parametrized conformance tests for ``ProviderAuditRepo``.

Runs against both SQLite and Postgres via the ``backend`` fixture so
SQLite-vs-Postgres divergence is caught on every commit.  The
provider audit log is append-only with monotonic integer ids; both
backends serialise / deserialise the JSON payload + UTC ``occurred_at``
timestamp through the same Pydantic model on read.
"""

from datetime import UTC, datetime

import pytest
from pydantic import JsonValue

from synthorg.api.dto_provider_capabilities import (
    ProviderAuditActor,
    ProviderAuditEvent,
)
from synthorg.persistence.protocol import PersistenceBackend

pytestmark = pytest.mark.integration


def _event(  # noqa: PLR0913 -- test factory with explicit knobs
    *,
    provider_name: str = "cloud-test",
    event_type: str = "provider_updated",
    actor_id: str = "user-1",
    actor_label: str = "Operator",
    payload: dict[str, JsonValue] | None = None,
    occurred_at: datetime | None = None,
) -> ProviderAuditEvent:
    return ProviderAuditEvent(
        provider_name=provider_name,
        event_type=event_type,  # type: ignore[arg-type]
        actor=ProviderAuditActor(id=actor_id, label=actor_label),
        # Use ``is None`` instead of truthy fallback: ``payload={}``
        # is a meaningful test input (intentional empty payload) and
        # must not be silently replaced with the default object.
        payload=payload if payload is not None else {"k": "v"},
        occurred_at=occurred_at or datetime.now(UTC),
    )


async def test_record_assigns_monotonic_id(backend: PersistenceBackend) -> None:
    repo = backend.provider_audit_events
    a = await repo.record(_event())
    b = await repo.record(_event())
    assert a.id is not None
    assert b.id is not None
    assert b.id > a.id


async def test_list_newest_first(backend: PersistenceBackend) -> None:
    repo = backend.provider_audit_events
    for i in range(3):
        await repo.record(_event(payload={"i": i}))
    events, has_more = await repo.list(provider_name="cloud-test", limit=10)
    assert has_more is False
    payload_iters = [e.payload["i"] for e in events]
    assert payload_iters == [2, 1, 0]


async def test_list_keyset_pagination(backend: PersistenceBackend) -> None:
    repo = backend.provider_audit_events
    for i in range(5):
        await repo.record(_event(payload={"i": i}))
    first, has_more = await repo.list(provider_name="cloud-test", limit=2)
    assert has_more is True
    assert len(first) == 2

    cursor_id = first[-1].id
    assert cursor_id is not None
    second, _ = await repo.list(
        provider_name="cloud-test",
        after_id=cursor_id,
        limit=2,
    )
    assert all(e.id is not None and e.id < cursor_id for e in second)


async def test_list_isolates_provider_scope(backend: PersistenceBackend) -> None:
    repo = backend.provider_audit_events
    await repo.record(_event(provider_name="cloud-a"))
    await repo.record(_event(provider_name="cloud-b"))
    a_events, _ = await repo.list(provider_name="cloud-a")
    assert all(e.provider_name == "cloud-a" for e in a_events)
    assert len(a_events) == 1


async def test_payload_round_trip_complex(backend: PersistenceBackend) -> None:
    repo = backend.provider_audit_events
    payload: dict[str, JsonValue] = {
        "list": [1, 2, 3],
        "nested": {"key": "value", "n": 42},
        "string": "abc",
        "bool": True,
    }
    saved = await repo.record(_event(payload=payload))
    loaded, _ = await repo.list(provider_name=saved.provider_name, limit=1)
    assert len(loaded) == 1
    # ``ProviderAuditEvent._freeze_payload`` recursively converts the
    # payload into immutable equivalents (``MappingProxyType`` /
    # ``tuple`` / ``frozenset``); compare via the JSON-serialised form
    # which is the wire-shape callers actually consume.
    assert loaded[0].model_dump(mode="json")["payload"] == payload


async def test_purge_before_id(backend: PersistenceBackend) -> None:
    repo = backend.provider_audit_events
    saved_ids: list[int] = []
    for _ in range(4):
        rec = await repo.record(_event())
        assert rec.id is not None
        saved_ids.append(rec.id)
    cutoff = saved_ids[2]  # purge ids strictly less than this
    removed = await repo.purge_before_id(before_id=cutoff)
    assert removed == 2  # ids 0 and 1
    remaining, _ = await repo.list(provider_name="cloud-test", limit=10)
    assert all(e.id is not None and e.id >= cutoff for e in remaining)


async def test_occurred_at_round_trips_utc(backend: PersistenceBackend) -> None:
    repo = backend.provider_audit_events
    fixed = datetime(2026, 4, 28, 12, 30, 45, tzinfo=UTC)
    await repo.record(_event(occurred_at=fixed))
    events, _ = await repo.list(provider_name="cloud-test", limit=1)
    assert len(events) == 1
    assert events[0].occurred_at == fixed
