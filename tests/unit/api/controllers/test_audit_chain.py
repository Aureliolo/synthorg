"""Tests for the on-demand audit-chain verification controller."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from synthorg.api.controllers.audit_chain import _verify_live_chain
from synthorg.core.domain_errors import ServiceUnavailableError
from synthorg.observability.audit_chain.chain import HashChain
from synthorg.observability.audit_chain.protocol import AuditChainSigner, SignedPayload
from synthorg.observability.audit_chain.sink import AuditChainSink
from synthorg.observability.audit_chain.timestamping import LocalClockProvider

pytestmark = pytest.mark.unit


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


class TestAuditChainController:
    async def test_verify_raises_when_no_sink_installed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "synthorg.api.controllers.audit_chain.iter_logging_handlers",
            lambda: (),
        )
        with pytest.raises(ServiceUnavailableError):
            await _verify_live_chain()

    async def test_verify_returns_the_result_for_the_live_sink(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        chain = HashChain()
        chain.append(b"event-0", b"sig", datetime.now(UTC))
        sink = AuditChainSink(
            signer=_make_signer(),
            timestamp_provider=LocalClockProvider(),
            chain=chain,
        )
        monkeypatch.setattr(
            "synthorg.api.controllers.audit_chain.iter_logging_handlers",
            lambda: (sink,),
        )
        response = await _verify_live_chain()
        assert response.valid is True
        assert response.entries_checked == 1
        assert response.first_break_position is None
