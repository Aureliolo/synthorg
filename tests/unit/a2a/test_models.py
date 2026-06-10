"""Tests for A2A protocol models."""

from typing import cast

import pytest
from pydantic import JsonValue

from synthorg.a2a.models import (
    A2A_AUTH_REQUIRED,
    A2A_PEER_NOT_ALLOWED,
    A2A_TASK_NOT_FOUND,
    JSONRPC_INTERNAL_ERROR,
    JSONRPC_INVALID_PARAMS,
    JSONRPC_INVALID_REQUEST,
    JSONRPC_METHOD_NOT_FOUND,
    JSONRPC_PARSE_ERROR,
    A2AAgentCard,
    A2AAgentProvider,
    A2AAgentSkill,
    A2AAuthSchemeInfo,
    A2ADataPart,
    A2AFilePart,
    A2AMessage,
    A2AMessageRole,
    A2AMetadataKey,
    A2ATask,
    A2ATaskState,
    A2ATextPart,
    JsonRpcErrorData,
    JsonRpcRequest,
    JsonRpcResponse,
)
from synthorg.integrations.connections.models import ConnectionType


class TestJsonRpcRequest:
    """JSON-RPC 2.0 request envelope."""

    @pytest.mark.unit
    def test_defaults(self) -> None:
        """Request has jsonrpc=2.0, auto-generated id."""
        req = JsonRpcRequest(method="message/send")
        assert req.jsonrpc == "2.0"
        assert req.method == "message/send"
        assert isinstance(req.id, str)
        assert req.params == {}

    @pytest.mark.unit
    def test_with_params(self) -> None:
        """Request carries params dict."""
        req = JsonRpcRequest(
            method="tasks/get",
            params={"task_id": "abc-123"},
        )
        assert req.params["task_id"] == "abc-123"

    @pytest.mark.unit
    def test_params_deep_copied(self) -> None:
        """Params dict is deep-copied at construction."""
        nested: dict[str, JsonValue] = {"key": "value"}
        original: dict[str, JsonValue] = {"nested": nested}
        req = JsonRpcRequest(method="test", params=original)
        nested["key"] = "changed"
        params = cast("dict[str, dict[str, str]]", req.params)
        assert params["nested"]["key"] == "value"

    @pytest.mark.unit
    def test_frozen(self) -> None:
        """Request is immutable."""
        req = JsonRpcRequest(method="test")
        with pytest.raises(Exception):  # noqa: B017, PT011
            req.method = "other"  # type: ignore[misc]

    @pytest.mark.unit
    def test_blank_method_rejected(self) -> None:
        """Blank method names are rejected."""
        with pytest.raises(ValueError, match="whitespace"):
            JsonRpcRequest(method="  ")

    @pytest.mark.unit
    def test_integer_id(self) -> None:
        """Integer IDs are accepted per JSON-RPC spec."""
        req = JsonRpcRequest(id=42, method="test")
        assert req.id == 42

    @pytest.mark.unit
    def test_serialization_round_trip(self) -> None:
        """Request survives JSON round-trip."""
        req = JsonRpcRequest(
            id="req-1",
            method="message/send",
            params={"message": {"role": "user"}},
        )
        data = req.model_dump()
        restored = JsonRpcRequest.model_validate(data)
        assert restored == req


class TestJsonRpcResponse:
    """JSON-RPC 2.0 response envelope."""

    @pytest.mark.unit
    def test_success_response(self) -> None:
        """Success response has result, no error."""
        resp = JsonRpcResponse(
            id="req-1",
            result={"status": "ok"},
        )
        assert resp.result == {"status": "ok"}
        assert resp.error is None

    @pytest.mark.unit
    def test_error_response(self) -> None:
        """Error response has error, no result."""
        resp = JsonRpcResponse(
            id="req-1",
            error=JsonRpcErrorData(
                code=JSONRPC_METHOD_NOT_FOUND,
                message="Method not found",
            ),
        )
        assert resp.error is not None
        assert resp.error.code == -32601
        assert resp.result is None

    @pytest.mark.unit
    def test_both_result_and_error_rejected(self) -> None:
        """Cannot set both result and error."""
        with pytest.raises(ValueError, match="not both"):
            JsonRpcResponse(
                id="req-1",
                result={"ok": True},
                error=JsonRpcErrorData(code=-1, message="fail"),
            )

    @pytest.mark.unit
    def test_neither_result_nor_error_rejected(self) -> None:
        """Must set at least one of result or error."""
        with pytest.raises(ValueError, match="must have result or error"):
            JsonRpcResponse(id="req-1")

    @pytest.mark.unit
    def test_result_deep_copied(self) -> None:
        """Result dict is deep-copied at construction."""
        nested: dict[str, JsonValue] = {"key": "value"}
        original: dict[str, JsonValue] = {"nested": nested}
        resp = JsonRpcResponse(id="1", result=original)
        nested["key"] = "changed"
        assert resp.result is not None
        result = cast("dict[str, dict[str, str]]", resp.result)
        assert result["nested"]["key"] == "value"


class TestJsonRpcErrorCodes:
    """Standard and A2A-specific error codes."""

    @pytest.mark.unit
    def test_standard_codes(self) -> None:
        """Standard JSON-RPC error codes."""
        assert JSONRPC_PARSE_ERROR == -32700
        assert JSONRPC_INVALID_REQUEST == -32600
        assert JSONRPC_METHOD_NOT_FOUND == -32601
        assert JSONRPC_INVALID_PARAMS == -32602
        assert JSONRPC_INTERNAL_ERROR == -32603

    @pytest.mark.unit
    def test_a2a_codes(self) -> None:
        """A2A-specific error codes are in application range."""
        assert A2A_TASK_NOT_FOUND == -32001
        assert A2A_AUTH_REQUIRED == -32003
        assert A2A_PEER_NOT_ALLOWED == -32004


class TestA2ATaskState:
    """A2A task state enum."""

    @pytest.mark.unit
    def test_all_states(self) -> None:
        """All expected states exist."""
        states = {s.value for s in A2ATaskState}
        expected = {
            "submitted",
            "working",
            "input-required",
            "completed",
            "failed",
            "canceled",
            "rejected",
            "auth-required",
        }
        assert states == expected

    @pytest.mark.unit
    def test_string_value(self) -> None:
        """States are StrEnum with hyphenated values."""
        assert A2ATaskState.INPUT_REQUIRED.value == "input-required"
        assert A2ATaskState.AUTH_REQUIRED.value == "auth-required"


class TestA2AMessageParts:
    """A2A message part types."""

    @pytest.mark.unit
    def test_text_part(self) -> None:
        """TextPart carries text content."""
        part = A2ATextPart(text="hello")
        assert part.type == "text"
        assert part.text == "hello"

    @pytest.mark.unit
    def test_text_part_blank_rejected(self) -> None:
        """Blank text is rejected."""
        with pytest.raises(ValueError, match="whitespace"):
            A2ATextPart(text="  ")

    @pytest.mark.unit
    def test_data_part(self) -> None:
        """DataPart carries structured JSON."""
        part = A2ADataPart(data={"key": "value"})
        assert part.type == "data"
        assert part.data == {"key": "value"}

    @pytest.mark.unit
    def test_data_part_deep_copy(self) -> None:
        """DataPart deep-copies data at construction."""
        nested: dict[str, JsonValue] = {"k": "v"}
        original: dict[str, JsonValue] = {"nested": nested}
        part = A2ADataPart(data=original)
        nested["k"] = "changed"
        assert cast("dict[str, str]", part.data["nested"])["k"] == "v"

    @pytest.mark.unit
    def test_file_part(self) -> None:
        """FilePart references a file URI."""
        part = A2AFilePart(uri="https://example.com/file.pdf")
        assert part.type == "file"
        assert part.uri == "https://example.com/file.pdf"
        assert part.mime_type is None

    @pytest.mark.unit
    def test_file_part_with_mime(self) -> None:
        """FilePart can carry a MIME type."""
        part = A2AFilePart(
            uri="https://example.com/file.pdf",
            mime_type="application/pdf",
        )
        assert part.mime_type == "application/pdf"


class TestA2AMessage:
    """A2A protocol message."""

    @pytest.mark.unit
    def test_user_message(self) -> None:
        """User message with text part."""
        msg = A2AMessage(
            role=A2AMessageRole.USER,
            parts=(A2ATextPart(text="Hello"),),
        )
        assert msg.role == "user"
        assert len(msg.parts) == 1

    @pytest.mark.unit
    def test_agent_message(self) -> None:
        """Agent message with text part."""
        msg = A2AMessage(
            role=A2AMessageRole.AGENT,
            parts=(A2ATextPart(text="I'll help"),),
        )
        assert msg.role == "agent"

    @pytest.mark.unit
    def test_empty_parts_rejected(self) -> None:
        """Message must have at least one part."""
        with pytest.raises(ValueError, match="at least 1"):
            A2AMessage(role=A2AMessageRole.USER, parts=())

    @pytest.mark.unit
    def test_multi_part(self) -> None:
        """Message can have multiple parts of different types."""
        msg = A2AMessage(
            role=A2AMessageRole.AGENT,
            parts=(
                A2ATextPart(text="Here's the data"),
                A2ADataPart(data={"results": [1, 2, 3]}),
            ),
        )
        assert len(msg.parts) == 2

    @pytest.mark.unit
    def test_metadata_deep_copy(self) -> None:
        """Metadata dict is deep-copied at construction."""
        original = {A2AMetadataKey.ORIG_MESSAGE_TYPE: "value"}
        msg = A2AMessage(
            role=A2AMessageRole.USER,
            parts=(A2ATextPart(text="test"),),
            metadata=original,
        )
        original[A2AMetadataKey.ORIG_MESSAGE_TYPE] = "changed"
        assert msg.metadata[A2AMetadataKey.ORIG_MESSAGE_TYPE] == "value"


class TestA2ATask:
    """A2A protocol task."""

    @pytest.mark.unit
    def test_defaults(self) -> None:
        """Task defaults to submitted state with auto-generated id."""
        task = A2ATask()
        assert task.state == A2ATaskState.SUBMITTED
        assert task.id
        assert task.messages == ()

    @pytest.mark.unit
    def test_with_messages(self) -> None:
        """Task carries conversation messages."""
        msg = A2AMessage(
            role=A2AMessageRole.USER,
            parts=(A2ATextPart(text="Do X"),),
        )
        task = A2ATask(messages=(msg,))
        assert len(task.messages) == 1

    @pytest.mark.unit
    def test_frozen(self) -> None:
        """Task is immutable."""
        task = A2ATask()
        with pytest.raises(Exception):  # noqa: B017, PT011
            task.state = A2ATaskState.WORKING  # type: ignore[misc]

    @pytest.mark.unit
    def test_serialization_round_trip(self) -> None:
        """Task survives JSON round-trip."""
        task = A2ATask(
            id="task-1",
            state=A2ATaskState.WORKING,
            messages=(
                A2AMessage(
                    role=A2AMessageRole.USER,
                    parts=(A2ATextPart(text="test"),),
                ),
            ),
            metadata={A2AMetadataKey.A2A_PEER: "external"},
        )
        data = task.model_dump()
        restored = A2ATask.model_validate(data)
        assert restored == task


class TestA2AAgentCard:
    """A2A Agent Card model."""

    @pytest.mark.unit
    def test_minimal_card(self) -> None:
        """Minimal Agent Card with required fields only."""
        card = A2AAgentCard(
            name="test-agent",
            url="https://example.com/a2a",
        )
        assert card.name == "test-agent"
        assert card.url == "https://example.com/a2a"
        assert card.skills == ()
        assert card.version == "1.0"

    @pytest.mark.unit
    def test_full_card(self) -> None:
        """Full Agent Card with all fields."""
        card = A2AAgentCard(
            name="research-agent",
            description="Conducts market research",
            url="https://example.com/a2a",
            skills=(
                A2AAgentSkill(
                    id="research",
                    name="Market Research",
                    description="Analyze market trends",
                    tags=("research", "analytics"),
                ),
            ),
            auth_schemes=(
                A2AAuthSchemeInfo(scheme="api_key"),
                A2AAuthSchemeInfo(
                    scheme="oauth2",
                    service_url="https://auth.example.com/token",
                ),
            ),
            provider=A2AAgentProvider(
                organization="Test Corp",
                url="https://example.com",
            ),
        )
        assert len(card.skills) == 1
        assert card.skills[0].name == "Market Research"
        assert len(card.auth_schemes) == 2
        assert card.provider is not None
        assert card.provider.organization == "Test Corp"

    @pytest.mark.unit
    def test_frozen(self) -> None:
        """Agent Card is immutable."""
        card = A2AAgentCard(name="test", url="https://example.com")
        with pytest.raises(Exception):  # noqa: B017, PT011
            card.name = "other"  # type: ignore[misc]

    @pytest.mark.unit
    def test_serialization_round_trip(self) -> None:
        """Agent Card survives JSON round-trip."""
        card = A2AAgentCard(
            name="test-agent",
            description="A test agent",
            url="https://example.com/a2a",
            skills=(A2AAgentSkill(id="s1", name="Skill One"),),
        )
        data = card.model_dump()
        restored = A2AAgentCard.model_validate(data)
        assert restored == card


class TestConnectionTypeA2APeer:
    """A2A_PEER in ConnectionType enum."""

    @pytest.mark.unit
    def test_a2a_peer_exists(self) -> None:
        """A2A_PEER is a valid ConnectionType member."""
        assert ConnectionType.A2A_PEER.value == "a2a_peer"

    @pytest.mark.unit
    def test_a2a_peer_in_values(self) -> None:
        """A2A_PEER appears in all ConnectionType values."""
        assert "a2a_peer" in [ct.value for ct in ConnectionType]

    @pytest.mark.unit
    def test_a2a_peer_from_string(self) -> None:
        """A2A_PEER can be constructed from string."""
        ct = ConnectionType("a2a_peer")
        assert ct is ConnectionType.A2A_PEER
