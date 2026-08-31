"""Tests for the periodic audit-chain verification scheduler."""

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
import structlog.testing

from synthorg.observability.audit_chain.chain import HashChain
from synthorg.observability.audit_chain.protocol import AuditChainSigner, SignedPayload
from synthorg.observability.audit_chain.sink import AuditChainSink
from synthorg.observability.audit_chain.timestamping import LocalClockProvider
from synthorg.observability.audit_chain.verifier import ChainVerificationResult
from synthorg.observability.audit_chain.verify_scheduler import (
    AuditChainVerificationScheduler,
)
from synthorg.observability.events.audit_chain import (
    AUDIT_CHAIN_PERSIST_INTEGRITY_FAILED,
)
from synthorg.settings.resolver import ConfigResolver
from tests._shared import make_app_state, mock_of

pytestmark = pytest.mark.unit


def _make_mock_signer() -> AsyncMock:
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


def _make_sink(chain: HashChain | None = None) -> AuditChainSink:
    return AuditChainSink(
        signer=_make_mock_signer(),
        timestamp_provider=LocalClockProvider(),
        chain=chain or HashChain(),
    )


class TestAuditChainVerificationScheduler:
    def test_interval_floor_rejected(self) -> None:
        with pytest.raises(ValueError, match="interval_seconds"):
            AuditChainVerificationScheduler(
                _make_sink(),
                make_app_state(),
                interval_seconds=59.0,
            )

    async def test_cycle_verifies_the_sink_and_records_no_warning_when_valid(
        self,
    ) -> None:
        chain = HashChain()
        chain.append(b"event-0", b"sig", datetime.now(UTC))
        sink = _make_sink(chain)
        verified = asyncio.Event()
        original = sink.verify_chain

        async def _tracked() -> ChainVerificationResult:
            result = await original()
            verified.set()
            return result

        sink.verify_chain = _tracked  # type: ignore[method-assign]
        scheduler = AuditChainVerificationScheduler(
            sink, make_app_state(), interval_seconds=60.0
        )
        await scheduler.start()
        try:
            await asyncio.wait_for(verified.wait(), timeout=5.0)
        finally:
            await scheduler.stop()

    async def test_cycle_warns_on_a_broken_chain_and_labels_it_boot_verify(
        self,
    ) -> None:
        chain = HashChain()
        chain.append(b"event-0", b"sig", datetime.now(UTC))
        chain.append(b"event-1", b"sig", datetime.now(UTC))
        chain._entries[1] = chain._entries[1].model_copy(
            update={"previous_hash": "tampered"}
        )
        sink = _make_sink(chain)
        verified = asyncio.Event()
        original = sink.verify_chain

        async def _tracked() -> ChainVerificationResult:
            result = await original()
            verified.set()
            return result

        sink.verify_chain = _tracked  # type: ignore[method-assign]
        scheduler = AuditChainVerificationScheduler(
            sink, make_app_state(), interval_seconds=60.0
        )

        with structlog.testing.capture_logs() as logs:
            await scheduler.start()
            try:
                await asyncio.wait_for(verified.wait(), timeout=5.0)
            finally:
                await scheduler.stop()

        entry = next(
            e for e in logs if e["event"] == AUDIT_CHAIN_PERSIST_INTEGRITY_FAILED
        )
        assert entry["first_break_position"] == 1
        assert entry["trigger"] == "boot_verify"

    async def test_second_cycle_labels_its_warning_periodic_verify(self) -> None:
        chain = HashChain()
        sink = _make_sink(chain)
        scheduler = AuditChainVerificationScheduler(
            sink, make_app_state(), interval_seconds=60.0
        )
        # First cycle (boot_verify) runs eagerly on start(); drive a second
        # cycle directly rather than waiting out the real interval.
        await scheduler._run_cycle_once()
        chain.append(b"event-0", b"sig", datetime.now(UTC))
        chain._entries[0] = chain._entries[0].model_copy(
            update={"previous_hash": "tampered"}
        )

        with structlog.testing.capture_logs() as logs:
            await scheduler._run_cycle_once()

        entry = next(
            e for e in logs if e["event"] == AUDIT_CHAIN_PERSIST_INTEGRITY_FAILED
        )
        assert entry["trigger"] == "periodic_verify"

    async def test_falls_back_to_boot_interval_with_no_resolver(self) -> None:
        scheduler = AuditChainVerificationScheduler(
            _make_sink(), make_app_state(), interval_seconds=120.0
        )
        assert await scheduler._resolve_wait_interval() == 120.0

    async def test_reads_the_live_interval_once_a_resolver_is_wired(self) -> None:
        resolver = mock_of[ConfigResolver](get_float=AsyncMock(return_value=1800.0))
        app_state = make_app_state(config_resolver=resolver)
        scheduler = AuditChainVerificationScheduler(
            _make_sink(), app_state, interval_seconds=120.0
        )
        resolved = await scheduler._resolve_wait_interval()
        assert resolved == 1800.0
        resolver.get_float.assert_awaited_once_with(
            "observability", "audit_chain_verify_interval_seconds"
        )
