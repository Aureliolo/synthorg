"""Boundary tests for the typed WebSocket control plane.

``handle_message`` validates the parsed JSON against
:data:`WS_CONTROL_MESSAGE_ADAPTER` so a malformed inbound frame
emits ``api.boundary.validation_failed`` and the connection gets a
generic error envelope instead of leaking
``Unknown action`` / ``filters must be an object`` strings.
"""

import json

import pytest
import structlog
from pydantic import ValidationError

from synthorg.api.controllers.ws_protocol import handle_message
from synthorg.api.ws_control_models import (
    WS_CONTROL_MESSAGE_ADAPTER,
    WsAuthMessage,
    WsPingMessage,
    WsSubscribeMessage,
    WsUnsubscribeMessage,
)
from synthorg.core.auth.models import AuthenticatedUser, AuthMethod
from synthorg.core.auth.roles import HumanRole
from synthorg.core.boundary import parse_typed

_USER = AuthenticatedUser(
    user_id="boundary-user",
    username="boundary",
    role=HumanRole.CEO,
    auth_method=AuthMethod.JWT,
)


@pytest.mark.unit
class TestWsControlMessageAdapter:
    """Direct coverage of the discriminated-union variants."""

    def test_subscribe_round_trips(self) -> None:
        msg = parse_typed(
            "ws.control",
            {"action": "subscribe", "channels": ["tasks"], "filters": None},
            WS_CONTROL_MESSAGE_ADAPTER,
        )
        assert isinstance(msg, WsSubscribeMessage)
        assert msg.channels == ("tasks",)
        assert msg.filters is None

    def test_unsubscribe_round_trips(self) -> None:
        msg = parse_typed(
            "ws.control",
            {"action": "unsubscribe", "channels": ["tasks"]},
            WS_CONTROL_MESSAGE_ADAPTER,
        )
        assert isinstance(msg, WsUnsubscribeMessage)
        assert msg.channels == ("tasks",)

    def test_ping_round_trips(self) -> None:
        msg = parse_typed(
            "ws.control",
            {"action": "ping"},
            WS_CONTROL_MESSAGE_ADAPTER,
        )
        assert isinstance(msg, WsPingMessage)

    def test_auth_round_trips(self) -> None:
        msg = parse_typed(
            "ws.control",
            {"action": "auth", "ticket": "abc-ticket"},
            WS_CONTROL_MESSAGE_ADAPTER,
        )
        assert isinstance(msg, WsAuthMessage)
        assert msg.ticket == "abc-ticket"

    def test_extra_field_rejected(self) -> None:
        with pytest.raises(ValidationError):
            parse_typed(
                "ws.control",
                {"action": "subscribe", "channels": ["tasks"], "extra": "boom"},
                WS_CONTROL_MESSAGE_ADAPTER,
            )

    def test_unknown_action_rejected(self) -> None:
        with pytest.raises(ValidationError):
            parse_typed(
                "ws.control",
                {"action": "noop"},
                WS_CONTROL_MESSAGE_ADAPTER,
            )

    def test_subscribe_filters_must_be_str_to_str(self) -> None:
        with pytest.raises(ValidationError):
            parse_typed(
                "ws.control",
                {
                    "action": "subscribe",
                    "channels": ["tasks"],
                    "filters": {"agent_id": 42},  # not a str
                },
                WS_CONTROL_MESSAGE_ADAPTER,
            )

    def test_validation_failure_emits_boundary_log(self) -> None:
        with (
            structlog.testing.capture_logs() as logs,
            pytest.raises(ValidationError),
        ):
            parse_typed(
                "ws.control",
                {"action": "noop"},
                WS_CONTROL_MESSAGE_ADAPTER,
            )
        boundary_logs = [
            log for log in logs if log.get("event") == "api.boundary.validation_failed"
        ]
        assert len(boundary_logs) == 1
        record = boundary_logs[0]
        assert record["boundary"] == "ws.control"
        assert record["log_level"] == "warning"


@pytest.mark.unit
class TestHandleMessageBoundary:
    """End-to-end boundary coverage at the dispatch surface."""

    def test_handle_subscribe_typed(self) -> None:
        subscribed: set[str] = set()
        filters: dict[str, dict[str, str]] = {}
        result = handle_message(
            json.dumps({"action": "subscribe", "channels": ["tasks"]}),
            subscribed,
            filters,
            _USER,
        )
        data = json.loads(result)
        assert data["action"] == "subscribed"
        assert "tasks" in subscribed

    def test_handle_extra_field_rejected_with_envelope(self) -> None:
        subscribed: set[str] = set()
        filters: dict[str, dict[str, str]] = {}
        result = handle_message(
            json.dumps(
                {"action": "subscribe", "channels": ["tasks"], "totally_extra": True},
            ),
            subscribed,
            filters,
            _USER,
        )
        data = json.loads(result)
        assert data["error"] == "Invalid control message"
        assert "tasks" not in subscribed

    def test_handle_validation_failure_emits_boundary_log(self) -> None:
        subscribed: set[str] = set()
        filters: dict[str, dict[str, str]] = {}
        with structlog.testing.capture_logs() as logs:
            handle_message(
                json.dumps({"action": "noop"}),
                subscribed,
                filters,
                _USER,
            )
        boundary_logs = [
            log for log in logs if log.get("event") == "api.boundary.validation_failed"
        ]
        assert len(boundary_logs) == 1
        assert boundary_logs[0]["boundary"] == "ws.control"
