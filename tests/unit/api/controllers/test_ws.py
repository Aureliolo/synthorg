"""Tests for WebSocket handler."""

import json

import pytest

from ai_company.api.controllers.ws import WsHandler


@pytest.mark.unit
class TestWsHandler:
    def test_on_receive_subscribe(self) -> None:
        handler = WsHandler.__new__(WsHandler)
        handler._subscribed = set()
        result = handler.on_receive(
            json.dumps({"action": "subscribe", "channels": ["tasks"]})
        )
        assert result is not None
        data = json.loads(result)
        assert data["action"] == "subscribed"
        assert "tasks" in data["channels"]

    def test_on_receive_unsubscribe(self) -> None:
        handler = WsHandler.__new__(WsHandler)
        handler._subscribed = {"tasks", "budget"}
        result = handler.on_receive(
            json.dumps({"action": "unsubscribe", "channels": ["tasks"]})
        )
        assert result is not None
        data = json.loads(result)
        assert data["action"] == "unsubscribed"
        assert "tasks" not in data["channels"]
        assert "budget" in data["channels"]

    def test_on_receive_invalid_json(self) -> None:
        handler = WsHandler.__new__(WsHandler)
        handler._subscribed = set()
        result = handler.on_receive("not json")
        assert result is not None
        data = json.loads(result)
        assert "error" in data

    def test_on_receive_unknown_action(self) -> None:
        handler = WsHandler.__new__(WsHandler)
        handler._subscribed = set()
        result = handler.on_receive(json.dumps({"action": "unknown"}))
        assert result is not None
        data = json.loads(result)
        assert "error" in data

    def test_subscribe_ignores_invalid_channels(self) -> None:
        handler = WsHandler.__new__(WsHandler)
        handler._subscribed = set()
        handler.on_receive(
            json.dumps(
                {
                    "action": "subscribe",
                    "channels": ["tasks", "invalid"],
                }
            )
        )
        assert "tasks" in handler._subscribed
        assert "invalid" not in handler._subscribed
