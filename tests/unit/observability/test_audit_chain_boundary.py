"""Boundary tests for the audit-chain typed payload.

The sink.emit() pipeline validates the assembled payload through
:func:`synthorg.api.boundary.parse_typed` against
:class:`AuditChainEventPayload`. The single most important test in
this file pins a known-good payload to a hard-coded ``json.dumps``
byte string so future changes cannot silently break the chain hash
even if the model later evolves.
"""

import hashlib
import json

import pytest
import structlog
from pydantic import ValidationError

from synthorg.api.boundary import parse_typed
from synthorg.observability.audit_chain.payloads import AuditChainEventPayload

# Frozen reference payload -- every field exercised so the byte-stable
# serialisation test catches any future drift in field order, encoding,
# or default-value handling. The attached values are deterministic
# fixtures (fake hashes, fake correlation IDs).
_GOLDEN_PAYLOAD: dict[str, object] = {
    "event": "security.auth.login",
    "level": "INFO",
    "timestamp": 1714694400.0,
    "module": "auth.controller",
    "tool_name": "synthorg_auth_login",
    "expected_hash": "a" * 64,
    "actual_hash": "b" * 64,
    "correlation_id": "corr-1234",
    "principal": "user-001",
    "resource": "user:user-001",
    "action_type": "auth:login",
    "error": "[REDACTED]",
}

# Computed once via the same json.dumps call sink.emit() uses; this
# constant pins the byte-stable wire shape that the audit-chain hash
# depends on. Regenerating it requires explicit reviewer sign-off
# because a chain-hash change invalidates every previously-signed
# audit entry.
_GOLDEN_JSON_BYTES: bytes = (
    b'{"action_type": "auth:login", "actual_hash": '
    b'"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", '
    b'"correlation_id": "corr-1234", "error": "[REDACTED]", '
    b'"event": "security.auth.login", "expected_hash": '
    b'"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", '
    b'"level": "INFO", "module": "auth.controller", '
    b'"principal": "user-001", "resource": "user:user-001", '
    b'"timestamp": 1714694400.0, "tool_name": "synthorg_auth_login"}'
)
# Hard-coded SHA-256 hex digest of _GOLDEN_JSON_BYTES. Pinned as a
# literal (NOT recomputed) so a coordinated accidental change to both
# the payload bytes and the hash expectation cannot pass the gate
# silently. Regenerating this value requires explicit reviewer
# sign-off because a chain-hash change invalidates every previously
# signed audit entry.
_GOLDEN_HASH: str = "f07d69ba3008a66e129e6e9fb11a71aed53c715021ab660bb471c1c140cdfd72"


@pytest.mark.unit
class TestAuditChainPayloadModel:
    """Direct coverage of the AuditChainEventPayload contract."""

    def test_round_trips(self) -> None:
        validated = parse_typed(
            "audit_chain",
            _GOLDEN_PAYLOAD,
            AuditChainEventPayload,
        )
        assert validated.event == "security.auth.login"
        assert validated.timestamp == 1714694400.0

    def test_minimal_payload_round_trips(self) -> None:
        # Only the four required LogRecord-derived fields.
        validated = parse_typed(
            "audit_chain",
            {
                "event": "security.x",
                "level": "INFO",
                "timestamp": 1.0,
                "module": "x",
            },
            AuditChainEventPayload,
        )
        assert validated.tool_name is None
        assert validated.error is None

    def test_extra_field_rejected(self) -> None:
        with pytest.raises(ValidationError):
            parse_typed(
                "audit_chain",
                {**_GOLDEN_PAYLOAD, "totally_made_up": "boom"},
                AuditChainEventPayload,
            )

    def test_missing_required_field_rejected(self) -> None:
        bad = dict(_GOLDEN_PAYLOAD)
        del bad["event"]
        with pytest.raises(ValidationError):
            parse_typed("audit_chain", bad, AuditChainEventPayload)

    def test_validation_failure_emits_boundary_log(self) -> None:
        with (
            structlog.testing.capture_logs() as logs,
            pytest.raises(ValidationError),
        ):
            parse_typed(
                "audit_chain",
                {**_GOLDEN_PAYLOAD, "totally_made_up": "boom"},
                AuditChainEventPayload,
            )
        boundary_logs = [
            log for log in logs if log.get("event") == "api.boundary.validation_failed"
        ]
        assert len(boundary_logs) == 1
        record = boundary_logs[0]
        assert record["boundary"] == "audit_chain"
        assert record["log_level"] == "warning"


@pytest.mark.unit
class TestAuditChainBytestability:
    """The migration MUST NOT change the bytes that go into the chain hash.

    parse_typed validates the payload dict but never replaces it; the
    sink.emit() pipeline still serialises the same dict via
    ``json.dumps(payload, sort_keys=True, ensure_ascii=True, default=str)``.
    These tests pin the expected byte layout to a hard-coded golden
    string so a future change that breaks byte-stability fails loudly.
    """

    def test_golden_json_byte_stable(self) -> None:
        actual_bytes = json.dumps(
            _GOLDEN_PAYLOAD,
            sort_keys=True,
            ensure_ascii=True,
            default=str,
        ).encode("utf-8")
        assert actual_bytes == _GOLDEN_JSON_BYTES, (
            "audit-chain JSON layout drifted; the chain hash would "
            "break. Compare actual vs golden: "
            f"actual={actual_bytes!r}"
        )

    def test_golden_hash_matches(self) -> None:
        actual_bytes = json.dumps(
            _GOLDEN_PAYLOAD,
            sort_keys=True,
            ensure_ascii=True,
            default=str,
        ).encode("utf-8")
        actual_hash = hashlib.sha256(actual_bytes).hexdigest()
        assert actual_hash == _GOLDEN_HASH

    def test_validation_does_not_mutate_payload(self) -> None:
        # parse_typed must never mutate the input dict; the sink relies
        # on that to feed the same dict into json.dumps.
        before = dict(_GOLDEN_PAYLOAD)
        parse_typed("audit_chain", _GOLDEN_PAYLOAD, AuditChainEventPayload)
        assert before == _GOLDEN_PAYLOAD


@pytest.mark.unit
class TestAuditChainSinkValidationFailure:
    """Sink.emit() must NOT swallow a ValidationError into the generic
    ``except Exception`` handler.

    The generic handler logs ``exc_info=True`` whose traceback may carry
    signer / TSA frame-locals; routing through the explicit
    ValidationError branch keeps the audit-chain integrity drop visible
    on its own event (``audit_chain.emit_validation_failed``) and
    avoids credential exposure.
    """

    def test_validation_failure_routes_to_explicit_branch(self) -> None:
        from logging import (
            LogRecord,
        )

        from synthorg.observability.audit_chain.config import AuditChainConfig
        from synthorg.observability.audit_chain.sink import AuditChainSink

        # Stand up a sink with stub signer / timestamp providers.
        # We never reach the signing path because validation fails
        # first, so the stubs only need to satisfy the constructor.
        class _StubSigner:
            async def sign(self, data: bytes) -> object:
                msg = "should not be reached"
                raise AssertionError(msg)

        class _StubTimestampProvider:
            async def get_timestamp(self, data: bytes) -> object:
                msg = "should not be reached"
                raise AssertionError(msg)

        sink = AuditChainSink(
            signer=_StubSigner(),  # type: ignore[arg-type]
            timestamp_provider=_StubTimestampProvider(),  # type: ignore[arg-type]
            config=AuditChainConfig(),
        )

        # A LogRecord whose extras include a smuggled key the typed
        # payload model does not declare. parse_typed must reject the
        # assembled dict.
        record = LogRecord(
            name="synthorg.test",
            level=20,
            pathname=__file__,
            lineno=0,
            msg="security.auth.login",
            args=None,
            exc_info=None,
        )
        callback_calls: list[tuple[str, int, float]] = []

        def _callback(status: str, depth: int, ts: float) -> None:
            callback_calls.append((status, depth, ts))

        sink.set_append_callback(_callback)

        # Inject a ValidationError by mutating the LogRecord so the
        # payload sink.emit() assembles is malformed at parse_typed
        # time: ``record.levelname = 42`` violates the model's
        # str-typed ``level`` field, which AuditChainEventPayload
        # rejects when parse_typed validates the assembled dict. This
        # exercises the explicit ``except ValidationError`` branch
        # added in this PR; the production iteration over LogRecord
        # extras is closed and cannot smuggle an unknown key, so a
        # field-type mutation is the cleanest way to reach the
        # validation path under test.
        with structlog.testing.capture_logs() as logs:
            object.__setattr__(record, "levelname", 42)
            sink.emit(record)

        validation_logs = [
            log
            for log in logs
            if log.get("event") == "audit_chain.emit_validation_failed"
        ]
        emit_error_logs = [
            log for log in logs if log.get("event") == "audit_chain.emit_error"
        ]
        # Must hit the explicit branch, NOT fall through to the
        # generic emit_error handler.
        assert len(validation_logs) == 1, (
            f"expected one audit_chain.emit_validation_failed, got "
            f"{len(validation_logs)} (and "
            f"{len(emit_error_logs)} emit_error logs)"
        )
        assert validation_logs[0]["log_level"] == "error"
        assert validation_logs[0]["audited_event"] == "security.auth.login"
        # Callback fires with status="error" so chain-depth metrics
        # accurately track dropped events.
        assert callback_calls == [("error", 0, 0.0)]
