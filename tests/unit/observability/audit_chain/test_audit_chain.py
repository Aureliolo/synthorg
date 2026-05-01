"""Tests for the audit chain module."""

import logging
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from synthorg.observability.audit_chain.chain import HashChain
from synthorg.observability.audit_chain.config import AuditChainConfig
from synthorg.observability.audit_chain.protocol import (
    AuditChainSigner,
    SignedPayload,
)
from synthorg.observability.audit_chain.sink import AuditChainSink
from synthorg.observability.audit_chain.timestamping import (
    LocalClockProvider,
    ResilientTimestampProvider,
)
from synthorg.observability.audit_chain.verifier import (
    AuditChainVerifier,
)


def _make_mock_signer() -> AsyncMock:
    """Create a mock AuditChainSigner."""
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


# ── Config Tests ───────────────────────────────────────────────────


@pytest.mark.unit
class TestAuditChainConfig:
    """Tests for AuditChainConfig defaults."""

    def test_defaults(self) -> None:
        config = AuditChainConfig()
        assert config.enabled is False
        assert config.backend == "asqav"
        assert config.tsa_url is None
        assert config.signing_key_path is None

    def test_frozen(self) -> None:
        config = AuditChainConfig()
        with pytest.raises(ValidationError):
            config.enabled = True  # type: ignore[misc]


# ── Protocol Tests ─────────────────────────────────────────────────


@pytest.mark.unit
class TestSignedPayload:
    """Tests for SignedPayload model."""

    def test_construction(self) -> None:
        payload = SignedPayload(
            signature=b"sig",
            algorithm="ml-dsa-65",
            signer_id="signer-1",
            signed_at=datetime.now(UTC),
        )
        assert payload.algorithm == "ml-dsa-65"
        assert payload.signature == b"sig"

    def test_frozen(self) -> None:
        payload = SignedPayload(
            signature=b"sig",
            algorithm="ml-dsa-65",
            signer_id="signer-1",
            signed_at=datetime.now(UTC),
        )
        with pytest.raises(ValidationError):
            payload.algorithm = "ed25519"  # type: ignore[misc]


@pytest.mark.unit
class TestAuditChainSignerProtocol:
    """Tests for AuditChainSigner protocol."""

    def test_mock_satisfies_protocol(self) -> None:
        signer = _make_mock_signer()
        assert isinstance(signer, AuditChainSigner)


# ── HashChain Tests ────────────────────────────────────────────────


@pytest.mark.unit
class TestHashChain:
    """Tests for HashChain append and verify."""

    def test_empty_chain_verifies(self) -> None:
        chain = HashChain()
        assert chain.verify_integrity() is True
        assert len(chain.entries) == 0

    def test_append_creates_entry(self) -> None:
        chain = HashChain()
        entry = chain.append(
            event_data=b"event-1",
            signature=b"sig-1",
            timestamp=datetime.now(UTC),
        )
        assert entry.position == 0
        assert entry.previous_hash == "genesis"
        assert len(chain.entries) == 1

    def test_chain_links_entries(self) -> None:
        chain = HashChain()
        chain.append(b"event-1", b"sig-1", datetime.now(UTC))
        entry2 = chain.append(b"event-2", b"sig-2", datetime.now(UTC))
        assert entry2.position == 1
        assert entry2.previous_hash != "genesis"

    def test_chain_verifies_after_multiple_appends(self) -> None:
        chain = HashChain()
        for i in range(10):
            chain.append(
                f"event-{i}".encode(),
                f"sig-{i}".encode(),
                datetime.now(UTC),
            )
        assert chain.verify_integrity() is True

    def test_tampered_chain_fails_verification(self) -> None:
        chain = HashChain()
        chain.append(b"event-1", b"sig-1", datetime.now(UTC))
        chain.append(b"event-2", b"sig-2", datetime.now(UTC))
        # Tamper: modify the previous_hash of entry 1.
        tampered = chain._entries[1].model_copy(
            update={"previous_hash": "tampered"},
        )
        chain._entries[1] = tampered
        assert chain.verify_integrity() is False


# ── Timestamping Tests ─────────────────────────────────────────────


@pytest.mark.unit
class TestLocalClockProvider:
    """Tests for LocalClockProvider."""

    async def test_returns_utc_datetime(self) -> None:
        provider = LocalClockProvider()
        result = await provider.get_timestamp()
        assert result.timestamp.tzinfo is not None
        assert result.source == "local_clock"


@pytest.mark.unit
class TestResilientTimestampProvider:
    """Tests for ResilientTimestampProvider fallback."""

    async def test_fallback_to_local_on_tsa_error(self) -> None:
        """TSA failure falls back to local clock."""
        from synthorg.observability.audit_chain.tsa_client import (
            TsaClient,
            TsaTransportError,
        )

        client = TsaClient("https://tsa.example.invalid")

        async def _boom(_data: bytes) -> None:
            error_msg = "simulated"
            raise TsaTransportError(error_msg)

        client.request_timestamp = _boom  # type: ignore[assignment,method-assign]
        provider = ResilientTimestampProvider(client)
        result = await provider.get_timestamp()
        assert result.timestamp.tzinfo is not None
        assert result.source == "fallback"

    @pytest.mark.parametrize(
        "incident_cls_name",
        ["TsaHashMismatchError", "TsaNonceMismatchError", "TsaSignatureError"],
    )
    async def test_security_incidents_propagate(
        self,
        incident_cls_name: str,
    ) -> None:
        """Hash / nonce / signature failures must NOT silently fall back.

        Parametrised so a regression in one incident class fails that
        case on its own -- the previous loop hid all three cases
        behind a single test ID and masked failures in the first
        incident class.
        """
        from synthorg.observability.audit_chain import tsa_client as _tsa

        incident_cls: type[BaseException] = getattr(_tsa, incident_cls_name)
        client = _tsa.TsaClient("https://tsa.example.invalid")

        async def _incident(_data: bytes) -> None:
            error_msg = f"simulated {incident_cls.__name__}"
            raise incident_cls(error_msg)

        client.request_timestamp = _incident  # type: ignore[assignment,method-assign]
        provider = ResilientTimestampProvider(client)
        with pytest.raises(incident_cls):
            await provider.get_timestamp()


# ── Sink Tests ─────────────────────────────────────────────────────


@pytest.mark.unit
class TestAuditChainSink:
    """Tests for AuditChainSink logging handler."""

    def test_filters_non_security_events(self) -> None:
        signer = _make_mock_signer()
        provider = LocalClockProvider()
        chain = HashChain()
        sink = AuditChainSink(
            signer=signer,
            timestamp_provider=provider,
            chain=chain,
        )
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="tool.invoke.start",
            args=(),
            exc_info=None,
        )
        sink.emit(record)
        assert len(chain.entries) == 0

    def test_signs_security_events(self) -> None:
        signer = _make_mock_signer()
        provider = LocalClockProvider()
        chain = HashChain()
        sink = AuditChainSink(
            signer=signer,
            timestamp_provider=provider,
            chain=chain,
        )
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="security.verdict.allow",
            args=(),
            exc_info=None,
        )
        sink.emit(record)
        assert len(chain.entries) == 1

    def test_multiple_security_events_chain(self) -> None:
        signer = _make_mock_signer()
        provider = LocalClockProvider()
        chain = HashChain()
        sink = AuditChainSink(
            signer=signer,
            timestamp_provider=provider,
            chain=chain,
        )
        for i in range(5):
            record = logging.LogRecord(
                name="test",
                level=logging.INFO,
                pathname="",
                lineno=0,
                msg=f"security.event.{i}",
                args=(),
                exc_info=None,
            )
            sink.emit(record)
        assert len(chain.entries) == 5
        assert chain.verify_integrity() is True


# ── Verifier Tests ─────────────────────────────────────────────────


@pytest.mark.unit
class TestAuditChainVerifier:
    """Tests for AuditChainVerifier."""

    async def test_empty_chain_valid(self) -> None:
        signer = _make_mock_signer()
        verifier = AuditChainVerifier(signer=signer)
        chain = HashChain()
        result = await verifier.verify_chain(chain)
        assert result.valid is True
        assert result.entries_checked == 0

    async def test_valid_chain_passes(self) -> None:
        signer = _make_mock_signer()
        verifier = AuditChainVerifier(signer=signer)
        chain = HashChain()
        for i in range(5):
            chain.append(
                f"event-{i}".encode(),
                b"sig",
                datetime.now(UTC),
            )
        result = await verifier.verify_chain(chain)
        assert result.valid is True
        assert result.entries_checked == 5

    async def test_broken_chain_detected(self) -> None:
        signer = _make_mock_signer()
        verifier = AuditChainVerifier(signer=signer)
        chain = HashChain()
        chain.append(b"event-1", b"sig", datetime.now(UTC))
        chain.append(b"event-2", b"sig", datetime.now(UTC))
        # Tamper.
        tampered = chain._entries[1].model_copy(
            update={"previous_hash": "tampered"},
        )
        chain._entries[1] = tampered

        result = await verifier.verify_chain(chain)
        assert result.valid is False
        assert result.first_break_position == 1


# ── Property Tests ─────────────────────────────────────────────────


@pytest.mark.unit
class TestHashChainProperties:
    """Property-based tests for HashChain."""

    @given(n=st.integers(min_value=1, max_value=20))
    @settings(max_examples=20)
    def test_untampered_chain_always_verifies(self, n: int) -> None:
        chain = HashChain()
        for i in range(n):
            chain.append(
                f"event-{i}".encode(),
                f"sig-{i}".encode(),
                datetime.now(UTC),
            )
        assert chain.verify_integrity() is True
        assert len(chain.entries) == n


# ── _extract_event_name ────────────────────────────────────────────


@pytest.mark.unit
class TestExtractEventName:
    """Cover all four shapes of ``record.msg`` the helper handles."""

    def _record(self, msg: object) -> logging.LogRecord:
        return logging.LogRecord(
            name="synthorg.test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg=msg,
            args=(),
            exc_info=None,
        )

    def test_string_msg_returns_message(self) -> None:
        from synthorg.observability.audit_chain.sink import _extract_event_name

        assert (
            _extract_event_name(self._record("security.connection.created"))
            == "security.connection.created"
        )

    def test_dict_msg_returns_event_key(self) -> None:
        """structlog pre-``wrap_for_formatter`` records carry the event_dict
        directly as ``record.msg``."""
        from synthorg.observability.audit_chain.sink import _extract_event_name

        msg = {"event": "security.connection.updated", "extra": "kept"}
        assert _extract_event_name(self._record(msg)) == "security.connection.updated"

    def test_tuple_msg_returns_event_key(self) -> None:
        """structlog post-``wrap_for_formatter`` records wrap the event_dict
        in a tuple so ``ProcessorFormatter`` can rebuild it later."""
        from synthorg.observability.audit_chain.sink import _extract_event_name

        msg = ({"event": "security.connection.deleted"}, ["foreign", "chain"])
        assert _extract_event_name(self._record(msg)) == "security.connection.deleted"

    def test_unknown_shape_returns_none(self) -> None:
        """Sentinel: unknown shapes return ``None`` so the caller can log
        a warning and skip the emit instead of silently dropping."""
        from synthorg.observability.audit_chain.sink import _extract_event_name

        assert _extract_event_name(self._record(42)) is None
        assert _extract_event_name(self._record(["list", "msg"])) is None
        assert _extract_event_name(self._record(())) is None
        assert _extract_event_name(self._record({"missing_event_key": True})) is None


# ── AuditChainSink emit() failure paths ────────────────────────────


def _build_log_record(event: str) -> logging.LogRecord:
    """Stdlib LogRecord pre-shaped with a structlog event-dict ``msg``.

    Mirrors the dict shape produced by ``synthorg.observability.get_logger``
    so the sink's ``_extract_event_name`` and ``emit`` paths exercise the
    structured route, not the bare-string fallback.
    """
    return logging.LogRecord(
        name="synthorg.test",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg={"event": event, "level": "info"},
        args=(),
        exc_info=None,
    )


@pytest.mark.unit
class TestAuditChainSinkFailurePaths:
    """``emit()`` failure-path coverage and callback re-entry safety."""

    def _make_sink(self, *, signer: AsyncMock) -> AuditChainSink:
        return AuditChainSink(
            signer=signer,
            timestamp_provider=LocalClockProvider(),
        )

    def test_signing_timeout_invokes_callback_with_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A signer whose future times out fires the ``status="error"``
        callback AND emits AUDIT_CHAIN_EMIT_TIMEOUT.

        Deterministic via direct mock of the executor's future: the
        previous implementation relied on a real 50 ms wall-clock
        timeout which flaked under ``-n 8`` worker contention. Patching
        ``_SIGNING_EXECUTOR.submit`` so its returned future raises
        ``TimeoutError`` immediately removes the timing dependency.
        """
        import concurrent.futures

        import structlog

        from synthorg.observability.audit_chain import sink as sink_module
        from synthorg.observability.events.audit_chain import (
            AUDIT_CHAIN_EMIT_TIMEOUT,
        )

        fake_future = MagicMock(spec=concurrent.futures.Future)
        fake_future.result.side_effect = concurrent.futures.TimeoutError()
        monkeypatch.setattr(
            sink_module._SIGNING_EXECUTOR,
            "submit",
            lambda *_args, **_kwargs: fake_future,
        )

        sink = self._make_sink(signer=_make_mock_signer())
        callback_calls: list[tuple[str, int, float]] = []
        sink.set_append_callback(
            lambda status, depth, ts: callback_calls.append((status, depth, ts)),
        )

        with structlog.testing.capture_logs() as events:
            sink.emit(_build_log_record("security.test.timeout"))

        assert len(sink.chain.entries) == 0
        assert callback_calls == [("error", 0, 0.0)]
        # Diagnostic-event contract: the sink MUST log the
        # timeout-specific event so operators can distinguish a TSA /
        # signer hang from a one-off serialization failure.
        emitted = [e["event"] for e in events]
        assert AUDIT_CHAIN_EMIT_TIMEOUT in emitted, (
            f"expected AUDIT_CHAIN_EMIT_TIMEOUT, got {emitted}"
        )

    def test_signer_exception_invokes_callback_with_error(self) -> None:
        """A signer that raises a non-timeout exception fires the
        ``status="error"`` callback AND emits AUDIT_CHAIN_EMIT_ERROR.

        Deterministic by construction -- ``signer.sign`` raises
        synchronously inside ``asyncio.run`` so no wall-clock waits
        are involved.
        """
        import structlog

        from synthorg.observability.events.audit_chain import (
            AUDIT_CHAIN_EMIT_ERROR,
        )

        signer = _make_mock_signer()

        async def _crash(_data: bytes) -> SignedPayload:
            error_msg = "signer crashed"
            raise RuntimeError(error_msg)

        signer.sign.side_effect = _crash
        sink = self._make_sink(signer=signer)
        callback_calls: list[tuple[str, int, float]] = []
        sink.set_append_callback(
            lambda status, depth, ts: callback_calls.append((status, depth, ts)),
        )

        with structlog.testing.capture_logs() as events:
            sink.emit(_build_log_record("security.test.crash"))

        assert len(sink.chain.entries) == 0
        assert callback_calls == [("error", 0, 0.0)]
        # Diagnostic-event contract: the generic emit_error event is
        # the only signal operators have for non-timeout signer
        # failures; assert it fires so a refactor that drops the log
        # (or routes it to the timeout branch) is caught.
        emitted = [e["event"] for e in events]
        assert AUDIT_CHAIN_EMIT_ERROR in emitted, (
            f"expected AUDIT_CHAIN_EMIT_ERROR, got {emitted}"
        )

    def test_callback_exception_does_not_break_chain(self) -> None:
        """If the append callback raises, the chain still appended
        (``_invoke_append_callback`` swallows callback errors)."""

        def _bad_callback(_status: str, _depth: int, _ts: float) -> None:
            error_msg = "metrics db down"
            raise ValueError(error_msg)

        sink = self._make_sink(signer=_make_mock_signer())
        sink.set_append_callback(_bad_callback)

        # Should NOT raise; chain still appends.
        sink.emit(_build_log_record("security.test.callback_error"))

        assert len(sink.chain.entries) == 1

    def test_unknown_record_shape_drops_silently(self) -> None:
        """A record whose ``msg`` shape doesn't match any known pattern
        is dropped from the chain AND emits a warning under the
        non-recursive ``audit_chain.*`` prefix so operators can debug.

        Asserts both halves of the contract: the chain stays untouched,
        AND the AUDIT_CHAIN_RECORD_SHAPE_UNKNOWN warning fires. Uses
        ``structlog.testing.capture_logs`` since the diagnostic log is
        emitted via ``synthorg.observability.get_logger`` (structlog),
        not stdlib -- the bridge to stdlib isn't wired in this unit
        test process.
        """
        import structlog

        from synthorg.observability.events.audit_chain import (
            AUDIT_CHAIN_RECORD_SHAPE_UNKNOWN,
        )

        sink = self._make_sink(signer=_make_mock_signer())
        record = logging.LogRecord(
            name="synthorg.test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg=42,  # integer is not a recognized shape
            args=(),
            exc_info=None,
        )

        with structlog.testing.capture_logs() as events:
            sink.emit(record)

        assert len(sink.chain.entries) == 0
        emitted = [e["event"] for e in events]
        assert AUDIT_CHAIN_RECORD_SHAPE_UNKNOWN in emitted, (
            f"expected AUDIT_CHAIN_RECORD_SHAPE_UNKNOWN warning, got: {emitted}"
        )
