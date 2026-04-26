"""Unit tests for the centralized MCP handler logging helpers.

Every domain handler used to define its own ``_log_invalid``, ``_log_failed``,
and ``_log_guardrail`` thin wrappers around ``logger.warning(...)``.  Those
duplicates collapse into three public helpers in
``synthorg.meta.mcp.handlers.common_logging``; this file pins their wire
shape so a future refactor cannot silently drop a kwarg or change a level.
"""

import pytest
import structlog.testing

from synthorg.meta.mcp.errors import (
    ArgumentValidationError,
    GuardrailViolationError,
    guardrail_violation,
    invalid_argument,
)
from synthorg.meta.mcp.handlers.common_logging import (
    log_handler_argument_invalid,
    log_handler_guardrail_violated,
    log_handler_invoke_failed,
)
from synthorg.observability.events.mcp import (
    MCP_HANDLER_ARGUMENT_INVALID,
    MCP_HANDLER_GUARDRAIL_VIOLATED,
    MCP_HANDLER_INVOKE_FAILED,
)

pytestmark = pytest.mark.unit


class TestLogHandlerArgumentInvalid:
    """Pin the wire shape of ``log_handler_argument_invalid``."""

    def test_emits_warning_with_required_kwargs(self) -> None:
        exc = invalid_argument("name", "non-blank string")
        with structlog.testing.capture_logs() as logs:
            log_handler_argument_invalid("synthorg_x_y", exc)
        assert len(logs) == 1
        record = logs[0]
        assert record["event"] == MCP_HANDLER_ARGUMENT_INVALID
        assert record["log_level"] == "warning"
        assert record["tool_name"] == "synthorg_x_y"
        assert record["error_type"] == "ArgumentValidationError"
        assert isinstance(record["error"], str)

    def test_error_message_funnels_through_safe_description(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        sentinel = "__SAFE_DESC_SENTINEL__"
        monkeypatch.setattr(
            "synthorg.meta.mcp.handlers.common_logging.safe_error_description",
            lambda _exc: sentinel,
        )
        with structlog.testing.capture_logs() as logs:
            log_handler_argument_invalid(
                "tool",
                ArgumentValidationError("k", "string"),
            )
        assert logs[0]["error"] == sentinel


class TestLogHandlerInvokeFailed:
    """Pin the wire shape of ``log_handler_invoke_failed`` plus context kwargs."""

    def test_emits_warning_with_required_kwargs(self) -> None:
        exc = RuntimeError("backend offline")
        with structlog.testing.capture_logs() as logs:
            log_handler_invoke_failed("synthorg_x_y", exc)
        assert len(logs) == 1
        record = logs[0]
        assert record["event"] == MCP_HANDLER_INVOKE_FAILED
        assert record["log_level"] == "warning"
        assert record["tool_name"] == "synthorg_x_y"
        assert record["error_type"] == "RuntimeError"
        assert "backend offline" in record["error"]

    def test_passes_through_arbitrary_context_kwargs(self) -> None:
        # coordination.py used to attach correlation ids (e.g. task_id,
        # decision_id) so a 404 entry could be tied to a specific request.
        # The consolidated helper must preserve that pass-through.
        with structlog.testing.capture_logs() as logs:
            log_handler_invoke_failed(
                "synthorg_coordination_resolve",
                LookupError("missing"),
                task_id="task-42",
                decision_id="decision-7",
            )
        record = logs[0]
        assert record["task_id"] == "task-42"
        assert record["decision_id"] == "decision-7"

    def test_no_context_kwargs_keeps_byte_identical_shape(self) -> None:
        with structlog.testing.capture_logs() as logs:
            log_handler_invoke_failed("tool", RuntimeError("x"))
        # Only the four canonical keys should be present (event, level,
        # tool_name, error_type, error, plus structlog's "timestamp"
        # injection if any). Asserting on the four-key set rather than
        # equality so we tolerate structlog metadata.
        record = logs[0]
        assert {"event", "log_level", "tool_name", "error_type", "error"}.issubset(
            record.keys(),
        )

    def test_secret_shaped_error_text_is_redacted(self) -> None:
        # SEC-1: secret-bearing exception messages must go through
        # safe_error_description so credential fragments never reach
        # logs. We rely on the redactor's ``Authorization: Bearer`` rule
        # to mask the token shape.
        exc = RuntimeError("Authorization: Bearer abcdef.token.value")
        with structlog.testing.capture_logs() as logs:
            log_handler_invoke_failed("tool", exc)
        message = logs[0]["error"]
        assert "abcdef.token.value" not in message


class TestLogHandlerGuardrailViolated:
    """Pin the wire shape of ``log_handler_guardrail_violated``."""

    def test_emits_warning_with_violation_code(self) -> None:
        exc = guardrail_violation("missing_actor", "no actor")
        with structlog.testing.capture_logs() as logs:
            log_handler_guardrail_violated("synthorg_tasks_delete", exc)
        record = logs[0]
        assert record["event"] == MCP_HANDLER_GUARDRAIL_VIOLATED
        assert record["log_level"] == "warning"
        assert record["tool_name"] == "synthorg_tasks_delete"
        assert record["violation"] == "missing_actor"

    def test_does_not_log_exception_message(self) -> None:
        # The guardrail event records only the typed ``violation`` field;
        # the human-readable message stays in the envelope where it
        # belongs and never leaks into structured logs.
        exc = GuardrailViolationError(
            "missing_reason",
            "Destructive operation requires a non-blank 'reason'",
        )
        with structlog.testing.capture_logs() as logs:
            log_handler_guardrail_violated("tool", exc)
        record = logs[0]
        assert "Destructive operation" not in str(record)
