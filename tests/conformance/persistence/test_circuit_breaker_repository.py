"""Conformance tests for ``CircuitBreakerStateRepository``."""

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.persistence.circuit_breaker_protocol import (
    CircuitBreakerStateRecord,
)
from synthorg.persistence.protocol import PersistenceBackend

pytestmark = pytest.mark.integration


def _record(
    *,
    a: str = "agent-a",
    b: str = "agent-b",
    bounce: int = 2,
    trips: int = 1,
    opened_at: float | None = None,
) -> CircuitBreakerStateRecord:
    return CircuitBreakerStateRecord(
        pair_key_a=NotBlankStr(a),
        pair_key_b=NotBlankStr(b),
        bounce_count=bounce,
        trip_count=trips,
        opened_at=opened_at,
    )


class TestCircuitBreakerStateRepository:
    async def test_save_and_load_all(self, backend: PersistenceBackend) -> None:
        rec = _record()
        await backend.circuit_breaker_state.save(rec)

        rows = await backend.circuit_breaker_state.load_all()
        assert any(
            r.pair_key_a == "agent-a" and r.pair_key_b == "agent-b" for r in rows
        )

    async def test_save_upserts(self, backend: PersistenceBackend) -> None:
        await backend.circuit_breaker_state.save(_record(bounce=1))
        await backend.circuit_breaker_state.save(_record(bounce=7))

        rows = await backend.circuit_breaker_state.load_all()
        match = [
            r for r in rows if r.pair_key_a == "agent-a" and r.pair_key_b == "agent-b"
        ]
        assert len(match) == 1
        assert match[0].bounce_count == 7

    async def test_load_all_empty(self, backend: PersistenceBackend) -> None:
        rows = await backend.circuit_breaker_state.load_all()
        assert rows == ()

    async def test_delete_existing(self, backend: PersistenceBackend) -> None:
        await backend.circuit_breaker_state.save(_record())

        deleted = await backend.circuit_breaker_state.delete(
            (NotBlankStr("agent-a"), NotBlankStr("agent-b")),
        )
        assert deleted is True

        rows = await backend.circuit_breaker_state.load_all()
        assert not any(
            r.pair_key_a == "agent-a" and r.pair_key_b == "agent-b" for r in rows
        )

    async def test_delete_missing(self, backend: PersistenceBackend) -> None:
        deleted = await backend.circuit_breaker_state.delete(
            (NotBlankStr("ghost-a"), NotBlankStr("ghost-b")),
        )
        assert deleted is False

    async def test_get_returns_existing_record(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.circuit_breaker_state.save(_record(a="get-a", b="get-b", trips=4))

        result = await backend.circuit_breaker_state.get(
            (NotBlankStr("get-a"), NotBlankStr("get-b")),
        )
        assert result is not None
        assert result.trip_count == 4

    async def test_get_missing_returns_none(self, backend: PersistenceBackend) -> None:
        result = await backend.circuit_breaker_state.get(
            (NotBlankStr("missing-a"), NotBlankStr("missing-b")),
        )
        assert result is None

    async def test_list_items_in_pair_key_order(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.circuit_breaker_state.save(_record(a="li-z", b="li-z2"))
        await backend.circuit_breaker_state.save(_record(a="li-a", b="li-a2"))

        results = await backend.circuit_breaker_state.list_items()
        scoped = [
            (r.pair_key_a, r.pair_key_b)
            for r in results
            if r.pair_key_a.startswith("li-")
        ]
        assert scoped == [("li-a", "li-a2"), ("li-z", "li-z2")]

    async def test_opened_at_roundtrip(self, backend: PersistenceBackend) -> None:
        await backend.circuit_breaker_state.save(
            _record(bounce=0, trips=3, opened_at=1234.5),
        )

        rows = await backend.circuit_breaker_state.load_all()
        match = next(
            r for r in rows if r.pair_key_a == "agent-a" and r.pair_key_b == "agent-b"
        )
        assert match.opened_at == pytest.approx(1234.5)
        assert match.trip_count == 3
