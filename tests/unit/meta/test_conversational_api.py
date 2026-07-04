"""Unit tests for the conversational write-path controller.

Mirrors the propose-route coverage in ``test_api.py``: route presence,
rate-limit policy registration, request-model validation, plus a direct
controller-method call that exercises the 503 None-guard without
standing up a full TestClient (the ``test_budget_forecast_controller``
pattern).
"""

from types import SimpleNamespace

import pytest
from litestar.datastructures import State
from litestar.testing import RequestFactory
from pydantic import ValidationError

from synthorg.api.controllers.conversational import (
    ChatActRequest,
    ChatStreamRequest,
    ConversationalController,
    GroupChatRequest,
)
from synthorg.api.rate_limits.policies import RATE_LIMIT_POLICIES
from synthorg.core.domain_errors import ServiceUnavailableError
from synthorg.core.types import NotBlankStr
from synthorg.meta.state import MetaStateSlice
from tests._shared import make_app_state

pytestmark = pytest.mark.unit


class TestConversationalControllerRoutes:
    """Route definitions on the conversational write-path controller."""

    def test_controller_path(self) -> None:
        assert ConversationalController.path == "/meta/chat"

    def test_has_chat_group_endpoint(self) -> None:
        methods = [
            name for name in dir(ConversationalController) if not name.startswith("_")
        ]
        assert "chat_group" in methods

    def test_group_rate_limit_policy_registered(self) -> None:
        assert "meta.chat.group" in RATE_LIMIT_POLICIES

    def test_has_streaming_endpoints(self) -> None:
        methods = [
            name for name in dir(ConversationalController) if not name.startswith("_")
        ]
        assert "chat_stream" in methods
        assert "chat_act_stream" in methods

    def test_streaming_rate_limit_policies_registered(self) -> None:
        # The stream endpoints reuse the buffered endpoints' policies.
        assert "meta.chat" in RATE_LIMIT_POLICIES
        assert "meta.chat.act" in RATE_LIMIT_POLICIES


class TestChatStreamRequest:
    """Request-model validation at the streaming-chat boundary."""

    def test_minimal_valid(self) -> None:
        req = ChatStreamRequest(question=NotBlankStr("what is our runway?"))
        assert req.question == "what is our runway?"

    def test_blank_question_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ChatStreamRequest(question=NotBlankStr("   "))

    def test_extra_forbidden(self) -> None:
        # No scoping ids: streaming serves only the free-form path.
        with pytest.raises(ValidationError):
            ChatStreamRequest(
                question=NotBlankStr("hi"),
                proposal_id="p1",  # type: ignore[call-arg]
            )

    def test_question_max_length(self) -> None:
        with pytest.raises(ValidationError):
            ChatStreamRequest(question=NotBlankStr("a" * 2001))


class TestGroupChatRequest:
    """Request-model validation at the group-chat boundary."""

    def test_minimal_valid(self) -> None:
        req = GroupChatRequest(message=NotBlankStr("kick off the round"))
        assert req.conversation_id is None
        assert req.participants == ()

    def test_blank_message_rejected(self) -> None:
        with pytest.raises(ValidationError):
            GroupChatRequest(message=NotBlankStr("   "))

    def test_extra_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            GroupChatRequest(
                message=NotBlankStr("hi"),
                unexpected="x",  # type: ignore[call-arg]
            )

    def test_message_max_length(self) -> None:
        with pytest.raises(ValidationError):
            GroupChatRequest(message=NotBlankStr("a" * 2001))

    def test_participants_accepts_tuple(self) -> None:
        req = GroupChatRequest(
            message=NotBlankStr("open with these agents"),
            participants=(NotBlankStr("agent-ceo"), NotBlankStr("agent-cfo")),
        )
        assert req.participants == ("agent-ceo", "agent-cfo")


async def test_chat_group_503_when_unwired() -> None:
    """Group chat disabled / deps absent -> 503, not a crash.

    ``group_chat_service`` defaults to ``None`` on the meta slice, which
    is exactly the unwired condition the controller's None-guard must
    surface as a ``ServiceUnavailableError`` before it touches the actor
    context or the round loop.
    """
    controller = object.__new__(ConversationalController)
    state = State()
    state.app_state = make_app_state(
        slices={MetaStateSlice: {"group_chat_service": None}}
    )
    with pytest.raises(ServiceUnavailableError):
        await ConversationalController.chat_group.fn(
            controller,
            data=GroupChatRequest(message=NotBlankStr("anyone there?")),
            state=state,
        )


async def test_chat_act_live_gate_runs_before_actor() -> None:
    """chat_act consults the live feature gate before the actor path.

    A wired actor sits on the slice, so the ``None``-guard would pass;
    the ``ServiceUnavailableError`` therefore proves the live gate ran
    first (fail-closed here because the test app_state carries no config
    resolver). This is the security kill-switch: the flag is re-read per
    request, so flipping it off 503s without a restart. Removing the gate
    would let execution fall through to the actor path and raise
    something other than ``ServiceUnavailableError``.
    """
    controller = object.__new__(ConversationalController)
    state = State()
    state.app_state = make_app_state(
        slices={MetaStateSlice: {"conversational_actor": SimpleNamespace()}}
    )
    with pytest.raises(ServiceUnavailableError):
        await ConversationalController.chat_act.fn(
            controller,
            data=ChatActRequest(
                instruction=NotBlankStr("ship it"), agent=NotBlankStr("ceo")
            ),
            state=state,
        )


async def test_chat_stream_503_when_gate_fails_closed() -> None:
    """Streaming chat 503s cleanly before the first SSE frame.

    With no config resolver the ``explain_chat_enabled`` live gate
    fail-closes, so the endpoint raises ``ServiceUnavailableError`` (a
    normal RFC 9457 body) rather than opening a stream and only then
    discovering the feature is off.
    """
    controller = object.__new__(ConversationalController)
    state = State()
    state.app_state = make_app_state(
        slices={MetaStateSlice: {"chief_of_staff_chat": None}}
    )
    with pytest.raises(ServiceUnavailableError):
        await ConversationalController.chat_stream.fn(
            controller,
            request=RequestFactory().get("/meta/chat/stream"),
            data=ChatStreamRequest(question=NotBlankStr("runway?")),
            state=state,
        )


async def test_chat_act_stream_live_gate_runs_before_actor() -> None:
    """Streaming act consults the live gate before the actor path.

    Same kill-switch contract as the buffered ``/act``: a wired actor
    would pass the ``None``-guard, so the ``ServiceUnavailableError``
    proves the ``direct_mcp_enabled`` gate ran first (fail-closed with no
    resolver).
    """
    controller = object.__new__(ConversationalController)
    state = State()
    state.app_state = make_app_state(
        slices={MetaStateSlice: {"conversational_actor": SimpleNamespace()}}
    )
    with pytest.raises(ServiceUnavailableError):
        await ConversationalController.chat_act_stream.fn(
            controller,
            request=RequestFactory().get("/meta/chat/act/stream"),
            data=ChatActRequest(
                instruction=NotBlankStr("ship it"), agent=NotBlankStr("ceo")
            ),
            state=state,
        )
