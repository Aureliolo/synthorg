"""Conformance tests for ``AuditChainRepository``."""

from datetime import UTC, datetime, timedelta

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.observability.audit_chain.chain import ChainEntry
from synthorg.persistence.audit_chain_protocol import AuditChainFilterSpec
from synthorg.persistence.protocol import PersistenceBackend

pytestmark = pytest.mark.integration

_NOW = datetime(2026, 6, 26, 12, 0, 0, tzinfo=UTC)


def _entry(*, position: int, when: datetime = _NOW) -> ChainEntry:
    return ChainEntry(
        position=position,
        event_hash=NotBlankStr(f"hash-{position}"),
        previous_hash=NotBlankStr(
            "genesis" if position == 0 else f"hash-{position - 1}"
        ),
        canonical_payload=f"payload-{position}".encode(),
        signature=bytes([position % 256, 0xAB, 0xCD]),
        timestamp=when,
    )


class TestAuditChainRepository:
    async def test_append_and_query_oldest_first(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.audit_chain_entries.append(_entry(position=0))
        await backend.audit_chain_entries.append(_entry(position=1))
        await backend.audit_chain_entries.append(_entry(position=2))

        results = await backend.audit_chain_entries.query(AuditChainFilterSpec())
        positions = [e.position for e in results if e.position <= 2]
        assert positions == [0, 1, 2]
        # Binary columns round-trip intact.
        assert results[0].canonical_payload == b"payload-0"
        assert results[0].signature == bytes([0, 0xAB, 0xCD])

    async def test_get_tail(self, backend: PersistenceBackend) -> None:
        assert await backend.audit_chain_entries.get_tail() is None
        await backend.audit_chain_entries.append(_entry(position=0))
        await backend.audit_chain_entries.append(_entry(position=1))

        tail = await backend.audit_chain_entries.get_tail()
        assert tail is not None
        assert tail.position == 1
        assert tail.event_hash == "hash-1"

    async def test_min_position_filter(self, backend: PersistenceBackend) -> None:
        for pos in range(4):
            await backend.audit_chain_entries.append(_entry(position=pos))

        tail_two = await backend.audit_chain_entries.query(
            AuditChainFilterSpec(min_position=2)
        )
        assert [e.position for e in tail_two] == [2, 3]

    async def test_purge_before(self, backend: PersistenceBackend) -> None:
        await backend.audit_chain_entries.append(
            _entry(position=0, when=_NOW - timedelta(days=30))
        )
        await backend.audit_chain_entries.append(_entry(position=1, when=_NOW))

        removed = await backend.audit_chain_entries.purge_before(
            _NOW - timedelta(days=1)
        )
        assert removed == 1
        remaining = await backend.audit_chain_entries.query(AuditChainFilterSpec())
        assert [e.position for e in remaining] == [1]
