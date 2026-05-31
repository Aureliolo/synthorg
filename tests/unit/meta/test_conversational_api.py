"""Unit tests for the conversational write-path controller.

Mirrors the propose-route coverage in ``test_api.py``: route presence,
rate-limit policy registration, request-model validation, plus a direct
controller-method call that exercises the 503 None-guard without
standing up a full TestClient (the ``test_budget_forecast_controller``
pattern).
"""

import pytest
from litestar.datastructures import State
from pydantic import ValidationError

from synthorg.api.controllers.conversational import (
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
