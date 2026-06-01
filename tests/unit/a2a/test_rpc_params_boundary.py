"""Boundary tests for the A2A JSON-RPC params surface.

``parse_rpc_params`` routes the discriminated :data:`A2ARpcParams`
union through :func:`parse_typed` so a malformed ``params`` payload
emits ``api.boundary.validation_failed`` and the gateway maps the
re-raised ``ValidationError`` into ``-32602 Invalid params``.
"""

import pytest
import structlog
from pydantic import JsonValue, ValidationError

from synthorg.a2a.models import A2AMessage, JsonRpcRequest
from synthorg.a2a.rpc_params import (
    A2AMessageSendParams,
    A2ATaskCancelParams,
    A2ATaskGetParams,
    parse_rpc_params,
)


def _envelope(method: str, params: dict[str, JsonValue]) -> JsonRpcRequest:
    return JsonRpcRequest(jsonrpc="2.0", method=method, id=1, params=params)


def _message_payload() -> dict[str, JsonValue]:
    msg = A2AMessage(
        role="agent",  # type: ignore[arg-type]
        parts=({"type": "text", "text": "hello"},),  # type: ignore[arg-type]
    )
    return {"message": msg.model_dump(mode="json")}


@pytest.mark.unit
class TestA2ARpcParamsBoundary:
    """End-to-end coverage of the typed-params boundary."""

    def test_message_send_round_trips(self) -> None:
        envelope = _envelope("message/send", _message_payload())
        result = parse_rpc_params(envelope)
        assert isinstance(result, A2AMessageSendParams)
        assert len(result.message.parts) == 1

    def test_tasks_get_round_trips(self) -> None:
        envelope = _envelope("tasks/get", {"id": "task-42"})
        result = parse_rpc_params(envelope)
        assert isinstance(result, A2ATaskGetParams)
        assert result.id == "task-42"

    def test_tasks_cancel_round_trips(self) -> None:
        envelope = _envelope("tasks/cancel", {"id": "task-42"})
        result = parse_rpc_params(envelope)
        assert isinstance(result, A2ATaskCancelParams)
        assert result.id == "task-42"

    def test_method_envelope_overrides_smuggled_method(self) -> None:
        # Peer declares tasks/get on the envelope but smuggles
        # message/send inside params -- the envelope wins, so the
        # discriminator picks the tasks/get variant which then fails
        # because it lacks the required ``id`` field.
        envelope = _envelope(
            "tasks/get",
            {"method": "message/send", "message": {"role": "agent", "parts": []}},
        )
        with pytest.raises(ValidationError):
            parse_rpc_params(envelope)

    def test_tasks_get_extra_field_rejected(self) -> None:
        envelope = _envelope("tasks/get", {"id": "task-1", "extra": "boom"})
        with pytest.raises(ValidationError):
            parse_rpc_params(envelope)

    def test_unknown_discriminator_rejected(self) -> None:
        envelope = _envelope("nope/unknown", {"id": "task-1"})
        with pytest.raises(ValidationError):
            parse_rpc_params(envelope)

    def test_validation_failure_emits_boundary_log(self) -> None:
        envelope = _envelope("tasks/get", {"id": "task-1", "extra": "boom"})
        with (
            structlog.testing.capture_logs() as logs,
            pytest.raises(ValidationError),
        ):
            parse_rpc_params(envelope)
        boundary_logs = [
            log for log in logs if log.get("event") == "api.boundary.validation_failed"
        ]
        assert len(boundary_logs) == 1
        record = boundary_logs[0]
        assert record["boundary"] == "a2a.jsonrpc"
        assert record["log_level"] == "warning"
        assert record["error_type"] == "ValidationError"
