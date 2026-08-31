"""Unit tests for the audit-chain durability post-startup wiring.

``AuditChainVerificationScheduler`` runs its first verify cycle eagerly on
``start()``, so a wiring bug here (constructed but never started, or started
but never stored where shutdown can find it) would silently leave a live
deployment's audit-chain tamper detection dark for its whole lifetime -- the
exact failure mode this issue exists to catch.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from synthorg.api.lifecycle_helpers.durability_wiring import (
    _try_wire_audit_chain_persistence,
)
from synthorg.observability.audit_chain.chain import ChainEntry
from synthorg.observability.audit_chain.protocol import AuditChainSigner, SignedPayload
from synthorg.observability.audit_chain.sink import AuditChainSink
from synthorg.observability.audit_chain.timestamping import LocalClockProvider
from synthorg.observability.state import ObservabilityStateSlice
from synthorg.persistence.audit_chain_protocol import AuditChainFilterSpec
from synthorg.persistence.protocol import PersistenceBackend
from tests._shared import make_app_state, mock_of

pytestmark = pytest.mark.unit


class _EmptyAuditChainRepo:
    """A durable audit-chain repository with nothing stored yet."""

    async def append(self, entry: ChainEntry) -> None:
        msg = "not exercised: wiring appends nothing"
        raise AssertionError(msg)

    async def query(
        self,
        filter_spec: AuditChainFilterSpec,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[ChainEntry, ...]:
        return ()

    async def purge_before(self, threshold: datetime, /) -> int:
        return 0

    async def get_tail(self) -> ChainEntry | None:
        return None


def _make_signer() -> AsyncMock:
    signer = AsyncMock(spec=AuditChainSigner)
    signer.algorithm = "test-algo"
    signer.sign = AsyncMock(
        return_value=SignedPayload(
            signature=b"test-sig",
            algorithm="test-algo",
            signer_id="test-signer",
            signed_at=datetime.now(UTC),
        ),
    )
    signer.verify = AsyncMock(return_value=True)
    return signer


async def test_wires_and_starts_the_verification_scheduler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A connected backend hydrates the live sink and starts the periodic
    verification scheduler, storing it where shutdown can stop it."""
    sink = AuditChainSink(
        signer=_make_signer(), timestamp_provider=LocalClockProvider()
    )
    monkeypatch.setattr(
        "synthorg.observability.sinks.iter_logging_handlers",
        lambda: (sink,),
    )
    app_state = make_app_state(
        persistence=mock_of[PersistenceBackend](
            audit_chain_entries=_EmptyAuditChainRepo(),
        ),
    )

    await _try_wire_audit_chain_persistence(app_state)

    scheduler = app_state.slice(ObservabilityStateSlice).audit_chain_verify_scheduler
    assert scheduler is not None
    try:
        assert scheduler.is_running
    finally:
        await scheduler.stop()
        await sink.aclose_persistence()


async def test_no_persistence_backend_wires_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A persistence-less boot leaves the chain in-memory-only: no scheduler,
    and the live logging handlers are never even consulted."""
    monkeypatch.setattr(
        "synthorg.observability.sinks.iter_logging_handlers",
        lambda: (_ for _ in ()).throw(AssertionError("should not be reached")),
    )
    app_state = make_app_state()

    await _try_wire_audit_chain_persistence(app_state)

    assert app_state.slice(ObservabilityStateSlice).audit_chain_verify_scheduler is None
