"""Tests for A2A typed RPC param models."""

import pytest
from pydantic import ValidationError

from synthorg.a2a.models import (
    A2AMessage,
    A2AMessageRole,
    A2ATextPart,
    JsonRpcRequest,
)
from synthorg.a2a.rpc_params import (
    A2AMessageSendParams,
    A2ARpcParams,
    A2ATaskCancelParams,
    A2ATaskGetParams,
    parse_rpc_params,
)

_MESSAGE_ID = "33333333-3333-3333-3333-333333333333"


def _sample_message() -> A2AMessage:
    """Build a minimal valid A2AMessage for tests."""
    return A2AMessage(
        role=A2AMessageRole.USER,
        parts=(A2ATextPart(text="hello"),),
    )


def _send_params() -> A2AMessageSendParams:
    """Build a minimal valid message/send params model."""
    from uuid import UUID

    return A2AMessageSendParams(message=_sample_message(), message_id=UUID(_MESSAGE_ID))


class TestA2AMessageSendParams:
    """Typed params for ``message/send``."""

    @pytest.mark.unit
    def test_construction(self) -> None:
        """Carries a typed A2AMessage."""
        params = _send_params()
        assert params.method == "message/send"
        assert params.message.role == A2AMessageRole.USER

    @pytest.mark.unit
    def test_message_id_required(self) -> None:
        """Audit 133: message_id is strictly required for idempotency."""
        with pytest.raises(ValidationError):
            A2AMessageSendParams.model_validate(
                {
                    "method": "message/send",
                    "message": {
                        "role": "user",
                        "parts": [{"type": "text", "text": "hi"}],
                    },
                },
            )

    @pytest.mark.unit
    def test_method_is_literal(self) -> None:
        """A non-matching method literal is rejected.

        Includes a valid ``message_id`` so the failure is the method
        literal, not the now-required idempotency key.
        """
        with pytest.raises(ValidationError):
            A2AMessageSendParams.model_validate(
                {
                    "method": "tasks/get",
                    "message": {
                        "role": "user",
                        "parts": [{"type": "text", "text": "hi"}],
                    },
                    "message_id": _MESSAGE_ID,
                },
            )

    @pytest.mark.unit
    def test_message_required(self) -> None:
        """Missing message field is rejected (with a valid message_id)."""
        with pytest.raises(ValidationError):
            A2AMessageSendParams.model_validate(
                {"method": "message/send", "message_id": _MESSAGE_ID},
            )

    @pytest.mark.unit
    def test_message_must_have_parts(self) -> None:
        """Empty parts tuple violates A2AMessage min_length."""
        with pytest.raises(ValidationError):
            A2AMessageSendParams.model_validate(
                {
                    "method": "message/send",
                    "message": {"role": "user", "parts": []},
                    "message_id": _MESSAGE_ID,
                },
            )

    @pytest.mark.unit
    def test_extra_keys_forbidden(self) -> None:
        """Unknown keys are rejected."""
        with pytest.raises(ValidationError):
            A2AMessageSendParams.model_validate(
                {
                    "method": "message/send",
                    "message": {
                        "role": "user",
                        "parts": [{"type": "text", "text": "hi"}],
                    },
                    "message_id": _MESSAGE_ID,
                    "id": "stowaway",
                },
            )

    @pytest.mark.unit
    def test_frozen(self) -> None:
        """Cannot reassign fields after construction."""
        params = _send_params()
        with pytest.raises(ValidationError):
            params.method = "tasks/get"  # type: ignore[assignment,misc]


class TestA2ATaskGetParams:
    """Typed params for ``tasks/get``."""

    @pytest.mark.unit
    def test_construction(self) -> None:
        """Carries a non-blank task id."""
        params = A2ATaskGetParams(id="task-123")
        assert params.method == "tasks/get"
        assert params.id == "task-123"

    @pytest.mark.unit
    def test_blank_id_rejected(self) -> None:
        """Whitespace-only ids are rejected."""
        with pytest.raises(ValidationError):
            A2ATaskGetParams.model_validate({"method": "tasks/get", "id": "   "})

    @pytest.mark.unit
    def test_missing_id_rejected(self) -> None:
        """Missing id is rejected."""
        with pytest.raises(ValidationError):
            A2ATaskGetParams.model_validate({"method": "tasks/get"})


class TestA2ATaskCancelParams:
    """Typed params for ``tasks/cancel``."""

    @pytest.mark.unit
    def test_construction(self) -> None:
        """Carries a non-blank task id."""
        params = A2ATaskCancelParams(id="task-456")
        assert params.method == "tasks/cancel"
        assert params.id == "task-456"

    @pytest.mark.unit
    def test_method_mismatch_rejected(self) -> None:
        """tasks/cancel literal cannot be reassigned."""
        with pytest.raises(ValidationError):
            A2ATaskCancelParams.model_validate({"method": "tasks/get", "id": "x"})


class TestParseRpcParams:
    """The gateway-facing dispatch helper."""

    @pytest.mark.unit
    def test_message_send_routes_to_message_variant(self) -> None:
        """A message/send request returns A2AMessageSendParams."""
        req = JsonRpcRequest(
            method="message/send",
            params={
                "message": {
                    "role": "user",
                    "parts": [{"type": "text", "text": "hi"}],
                },
                "message_id": _MESSAGE_ID,
            },
        )
        params = parse_rpc_params(req)
        assert isinstance(params, A2AMessageSendParams)
        assert params.message.parts[0].type == "text"

    @pytest.mark.unit
    def test_tasks_get_routes_to_get_variant(self) -> None:
        """A tasks/get request returns A2ATaskGetParams."""
        req = JsonRpcRequest(method="tasks/get", params={"id": "task-1"})
        params = parse_rpc_params(req)
        assert isinstance(params, A2ATaskGetParams)
        assert params.id == "task-1"

    @pytest.mark.unit
    def test_tasks_cancel_routes_to_cancel_variant(self) -> None:
        """A tasks/cancel request returns A2ATaskCancelParams."""
        req = JsonRpcRequest(method="tasks/cancel", params={"id": "task-2"})
        params = parse_rpc_params(req)
        assert isinstance(params, A2ATaskCancelParams)
        assert params.id == "task-2"

    @pytest.mark.unit
    def test_method_params_mismatch_rejected(self) -> None:
        """Sending message/send shape with tasks/get method is rejected."""
        req = JsonRpcRequest(
            method="tasks/get",
            params={
                "message": {
                    "role": "user",
                    "parts": [{"type": "text", "text": "hi"}],
                }
            },
        )
        with pytest.raises(ValidationError):
            parse_rpc_params(req)

    @pytest.mark.unit
    def test_unknown_method_rejected(self) -> None:
        """A method outside the supported set has no variant."""
        req = JsonRpcRequest(method="tasks/list", params={})
        with pytest.raises(ValidationError):
            parse_rpc_params(req)

    @pytest.mark.unit
    def test_envelope_method_overrides_params_method(self) -> None:
        """A peer cannot inject a 'method' inside params to bypass dispatch."""
        # Peer says envelope method is tasks/get but stuffs message/send shape
        # into params with an injected method override.
        req = JsonRpcRequest(
            method="tasks/get",
            params={
                "method": "message/send",
                "message": {
                    "role": "user",
                    "parts": [{"type": "text", "text": "x"}],
                },
            },
        )
        with pytest.raises(ValidationError):
            parse_rpc_params(req)

    @pytest.mark.unit
    def test_missing_id_for_tasks_get(self) -> None:
        """tasks/get without an id raises ValidationError."""
        req = JsonRpcRequest(method="tasks/get", params={})
        with pytest.raises(ValidationError):
            parse_rpc_params(req)


class TestDiscriminatedUnion:
    """``A2ARpcParams`` as a TypeAdapter-validated union."""

    @pytest.mark.unit
    def test_round_trip_each_variant(self) -> None:
        """Every variant survives model_dump / model_validate."""
        from pydantic import TypeAdapter

        adapter: TypeAdapter[A2ARpcParams] = TypeAdapter(A2ARpcParams)

        send = _send_params()
        get = A2ATaskGetParams(id="t1")
        cancel = A2ATaskCancelParams(id="t2")
        for original in (send, get, cancel):
            data = original.model_dump()
            restored = adapter.validate_python(data)
            assert restored == original
