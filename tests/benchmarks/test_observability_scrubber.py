"""CodSpeed benchmark for the observability log scrubber.

``scrub_event_fields`` runs as a structlog processor on **every** log
call across the backend. A regression here scales linearly with log
volume.

Realistic event-payload shapes:

- ``small_clean``: 6 fields, all strings/ints, no secret-shaped values
- ``medium_mixed``: 20 fields with nested list + dict, no secrets
- ``adversarial``: 20 fields including credential-shaped values
  (``client_secret=...``, ``"access_token":"..."``, Bearer header)
  exercising every redaction path
"""

import pytest
from pytest_codspeed import BenchmarkFixture

from synthorg.observability import scrub_event_fields
from tests._shared import JsonDict

_SMALL_CLEAN: JsonDict = {
    "event": "API_REQUEST_STARTED",
    "request_id": "req-0001",
    "method": "GET",
    "path": "/api/v1/agents",
    "status_code": 200,
    "duration_ms": 12.5,
}

_MEDIUM_MIXED: JsonDict = {
    "event": "ENGINE_STAGE_COMPLETED",
    "task_id": "task-abc-0042",
    "agent_id": "agent-backend-7",
    "stage": "verification",
    "duration_ms": 1234.5,
    "tokens_in": 4096,
    "tokens_out": 512,
    "cost": 0.0123,
    "tags": ["routing", "verification", "pass"],
    "metadata": {
        "retries": 0,
        "fallback_used": False,
        "selected_provider": "test-provider",
        "selected_model": "test-medium-001",
    },
    "tools_invoked": ["read", "grep", "edit"],
    "subtask_count": 3,
    "parallel_factor": 2,
    "success_rate": 0.94,
    "queue_depth": 5,
    "memory_hits": 12,
    "memory_misses": 3,
    "rate_limit_remaining": 480,
    "trace_id": "trace-deadbeef-cafe",
    "span_id": "span-1234abcd",
}

_ADVERSARIAL: JsonDict = {
    "event": "API_AUTH_VERIFY_FAILED",
    "request_id": "req-0002",
    "method": "POST",
    "path": "/api/v1/sessions",
    "status_code": 401,
    "raw_body": (
        "client_id=demo&client_secret=sk-very-secret-do-not-leak-12345"
        "&grant_type=client_credentials"
    ),
    "headers": {
        "Authorization": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.payload.sig",
        "Content-Type": "application/x-www-form-urlencoded",
    },
    "response_body": (
        '{"error":"invalid_grant","access_token":"sk-leaked-token-12345"}'
    ),
    "fernet_blob": "gAAAAABl-not-actually-fernet-but-shaped-like-it-1234567890abcdef",
    "params": [
        "redirect_uri=https://example.test/cb",
        "scope=read+write",
        "state=abc123",
    ],
    "user_agent": "synthorg-cli/0.7.3",
    "trace_id": "trace-abcd1234",
    "span_id": "span-5678efgh",
    "duration_ms": 8.3,
    "retry_count": 2,
    "downstream": "auth-service",
    "tags": ["auth", "rejected"],
    "ip": "203.0.113.42",
    "session_hint": "session-cookie-abcdef0123456789",
    "csrf_token": "csrf-token-not-redacted-by-key-name",
}


# ``.copy()`` on the payload before each scrub call is bench hygiene,
# not a correctness requirement: ``scrub_event_fields`` returns a new
# dict via a comprehension over ``_scrub_value``-recursed values and
# does NOT mutate its input. The shallow copy guards against any
# future change to that contract (and lets the bench iterations
# exercise an identical input each time).


@pytest.mark.benchmark
def test_scrub_small_clean(benchmark: BenchmarkFixture) -> None:
    """Best-case path: no secret-shaped strings present."""

    @benchmark
    def _() -> None:
        scrub_event_fields(None, "info", _SMALL_CLEAN.copy())


@pytest.mark.benchmark
def test_scrub_medium_mixed(benchmark: BenchmarkFixture) -> None:
    """Realistic engine-event payload with nested list + dict."""

    @benchmark
    def _() -> None:
        scrub_event_fields(None, "info", _MEDIUM_MIXED.copy())


@pytest.mark.benchmark
def test_scrub_adversarial(benchmark: BenchmarkFixture) -> None:
    """Worst-case path: every credential-pattern branch hit."""

    @benchmark
    def _() -> None:
        scrub_event_fields(None, "warning", _ADVERSARIAL.copy())
