# module-kind: tests
"""Unit tests for the conversational SSE ``error`` frame builder.

Once the ``text/event-stream`` headers are on the wire the RFC 9457 handler
can no longer run, so a post-start failure surfaces only as an in-stream
``event: error`` frame. These tests pin the frame's redaction contract: a
typed :class:`DomainError` crosses the wire with its client-safe detail /
code / retry semantics (scrubbed to the fallback for a 5xx), while an
unexpected fault stays opaque so its message cannot leak.
"""

import json as _json

import pytest

from synthorg.api.controllers._conversational_stream import _error_frame
from synthorg.core.domain_errors import (
    NotFoundError,
    PerOperationRateLimitError,
    ServiceUnavailableError,
)
from synthorg.core.error_taxonomy import ErrorCode

pytestmark = pytest.mark.unit


def _payload(exc: Exception) -> dict[str, object]:
    """Build the error frame for *exc* and return its parsed data body.

    Returns:
        The JSON-decoded ``data`` payload of the ``error`` frame.
    """
    frame = _error_frame(exc)
    assert frame["event"] == "error"
    parsed = _json.loads(frame["data"])
    assert isinstance(parsed, dict)
    return parsed


class TestErrorFrame:
    """The SSE ``error`` frame's redaction + discrimination contract."""

    def test_non_domain_error_stays_opaque(self) -> None:
        payload = _payload(ValueError("connection string user:hunter2@db"))
        # Only the class name crosses the wire; the message never does.
        assert payload == {"error": "Internal error: ValueError"}
        assert "hunter2" not in str(payload["error"])

    def test_client_error_surfaces_detail_and_code(self) -> None:
        payload = _payload(NotFoundError("The requested agent is not registered"))
        assert payload["error"] == "The requested agent is not registered"
        assert payload["error_code"] == ErrorCode.RESOURCE_NOT_FOUND.value
        assert payload["retryable"] is False
        assert "retry_after" not in payload

    def test_server_error_scrubs_to_fallback(self) -> None:
        # A 503 carries a 5xx-safe fallback, never the constructed detail
        # (which could name an internal dependency).
        payload = _payload(
            ServiceUnavailableError("chat backend down: internal-host:5432")
        )
        assert payload["error"] == "Service unavailable"
        assert "5432" not in str(payload["error"])
        assert payload["error_code"] == ErrorCode.SERVICE_UNAVAILABLE.value
        assert payload["retryable"] is True

    def test_rate_limit_carries_retry_after(self) -> None:
        payload = _payload(PerOperationRateLimitError(retry_after=30))
        assert payload["retryable"] is True
        assert payload["retry_after"] == 30
        assert payload["error_code"] == ErrorCode.PER_OPERATION_RATE_LIMITED.value
