"""Tests for the on-demand audit-chain verification controller."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from synthorg.api.controllers.audit_chain import (
    AuditChainVerificationResponse,
    _verify_live_chain,
)
from synthorg.core.domain_errors import ServiceUnavailableError
from synthorg.observability.audit_chain.chain import HashChain
from synthorg.observability.audit_chain.protocol import AuditChainSigner, SignedPayload
from synthorg.observability.audit_chain.sink import AuditChainSink
from synthorg.observability.audit_chain.timestamping import LocalClockProvider
from tests._shared import LoopAsyncClient
from tests.unit.api.conftest import make_auth_headers

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

    async def test_endpoint_rejects_a_non_ceo_role(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        """The routed HTTP layer enforces require_ceo, not only the
        directly-called handler helper the other tests in this file cover."""
        saved_headers = dict(async_test_client.headers)
        async_test_client.headers.update(make_auth_headers("observer"))
        try:
            resp = await async_test_client.post(
                "/api/v1/observability/audit-chain/verify"
            )
        finally:
            async_test_client.headers.update(saved_headers)
        assert resp.status_code == 403

    async def test_endpoint_admits_the_ceo_role(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        """A CEO request reaches the handler; the guard is what test_endpoint_
        rejects_a_non_ceo_role proves blocks other roles, not a blanket
        rejection of the route itself."""
        resp = await async_test_client.post("/api/v1/observability/audit-chain/verify")
        assert resp.status_code != 403


class TestAuditChainVerificationResponseShape:
    """The wire-facing DTO must reject the same self-contradicting shapes
    as the domain ``ChainVerificationResult`` it is built from by hand."""

    def test_negative_first_break_position_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AuditChainVerificationResponse(
                valid=False, entries_checked=1, first_break_position=-1
            )

    def test_negative_entries_checked_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AuditChainVerificationResponse(
                valid=True, entries_checked=-1, first_break_position=None
            )

    def test_valid_with_break_position_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AuditChainVerificationResponse(
                valid=True, entries_checked=1, first_break_position=0
            )

    def test_invalid_without_break_position_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AuditChainVerificationResponse(
                valid=False, entries_checked=1, first_break_position=None
            )
