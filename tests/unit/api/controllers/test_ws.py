"""Tests for WebSocket handler message parsing."""

import json

import pytest

from synthorg.api.controllers.ws import _handle_message


@pytest.mark.unit
class TestWsHandleMessage:
    def test_subscribe(self) -> None:
        subscribed: set[str] = set()
        filters: dict[str, dict[str, str]] = {}
        result = _handle_message(
            json.dumps({"action": "subscribe", "channels": ["tasks"]}),
            subscribed,
            filters,
        )
        data = json.loads(result)
        assert data["action"] == "subscribed"
        assert "tasks" in data["channels"]
        assert "tasks" in subscribed

    def test_unsubscribe(self) -> None:
        subscribed: set[str] = {"tasks", "budget"}
        filters: dict[str, dict[str, str]] = {"tasks": {"agent_id": "a1"}}
        result = _handle_message(
            json.dumps({"action": "unsubscribe", "channels": ["tasks"]}),
            subscribed,
            filters,
        )
        data = json.loads(result)
        assert data["action"] == "unsubscribed"
        assert "tasks" not in data["channels"]
        assert "budget" in data["channels"]
        assert "tasks" not in filters

    def test_invalid_json(self) -> None:
        subscribed: set[str] = set()
        filters: dict[str, dict[str, str]] = {}
        result = _handle_message("not json", subscribed, filters)
        data = json.loads(result)
        assert "error" in data

    def test_unknown_action(self) -> None:
        subscribed: set[str] = set()
        filters: dict[str, dict[str, str]] = {}
        result = _handle_message(
            json.dumps({"action": "unknown"}),
            subscribed,
            filters,
        )
        data = json.loads(result)
        assert "error" in data

    def test_subscribe_ignores_invalid_channels(self) -> None:
        subscribed: set[str] = set()
        filters: dict[str, dict[str, str]] = {}
        _handle_message(
            json.dumps(
                {
                    "action": "subscribe",
                    "channels": ["tasks", "invalid"],
                }
            ),
            subscribed,
            filters,
        )
        assert "tasks" in subscribed
        assert "invalid" not in subscribed

    def test_subscribe_with_filters(self) -> None:
        subscribed: set[str] = set()
        filters: dict[str, dict[str, str]] = {}
        _handle_message(
            json.dumps(
                {
                    "action": "subscribe",
                    "channels": ["tasks"],
                    "filters": {
                        "agent_id": "agent-1",
                        "project": "proj-1",
                    },
                }
            ),
            subscribed,
            filters,
        )
        assert "tasks" in subscribed
        assert filters["tasks"] == {
            "agent_id": "agent-1",
            "project": "proj-1",
        }

    def test_unsubscribe_clears_filters(self) -> None:
        subscribed: set[str] = {"tasks"}
        filters: dict[str, dict[str, str]] = {"tasks": {"agent_id": "agent-1"}}
        _handle_message(
            json.dumps({"action": "unsubscribe", "channels": ["tasks"]}),
            subscribed,
            filters,
        )
        assert "tasks" not in subscribed
        assert "tasks" not in filters

    def test_subscribe_without_filters_keeps_existing(self) -> None:
        subscribed: set[str] = set()
        filters: dict[str, dict[str, str]] = {}
        _handle_message(
            json.dumps({"action": "subscribe", "channels": ["tasks"]}),
            subscribed,
            filters,
        )
        assert "tasks" in subscribed
        assert "tasks" not in filters

    def test_subscribe_too_many_filter_keys(self) -> None:
        subscribed: set[str] = set()
        filters: dict[str, dict[str, str]] = {}
        many_filters = {f"key_{i}": f"val_{i}" for i in range(11)}
        result = _handle_message(
            json.dumps(
                {
                    "action": "subscribe",
                    "channels": ["tasks"],
                    "filters": many_filters,
                }
            ),
            subscribed,
            filters,
        )
        data = json.loads(result)
        assert data["error"] == "Filter bounds exceeded"
        assert "tasks" not in subscribed

    def test_subscribe_filter_value_too_long(self) -> None:
        subscribed: set[str] = set()
        filters: dict[str, dict[str, str]] = {}
        result = _handle_message(
            json.dumps(
                {
                    "action": "subscribe",
                    "channels": ["tasks"],
                    "filters": {"key": "x" * 257},
                }
            ),
            subscribed,
            filters,
        )
        data = json.loads(result)
        assert data["error"] == "Filter bounds exceeded"
        assert "tasks" not in subscribed

    def test_message_size_limit_boundary(self) -> None:
        subscribed: set[str] = set()
        filters: dict[str, dict[str, str]] = {}

        # 4096 bytes should pass (valid JSON that fits)
        small_msg = json.dumps({"action": "subscribe", "channels": ["tasks"]})
        result = _handle_message(small_msg, subscribed, filters)
        data = json.loads(result)
        assert "error" not in data or data.get("action") == "subscribed"

        # Message whose encoded bytes exceed 4096 should fail
        big_msg = json.dumps({"action": "subscribe", "data": "x" * 4096})
        result = _handle_message(big_msg, subscribed, filters)
        data = json.loads(result)
        assert data["error"] == "Message too large"

    def test_non_dict_json_returns_error(self) -> None:
        subscribed: set[str] = set()
        filters: dict[str, dict[str, str]] = {}

        # JSON array
        result = _handle_message(
            json.dumps([1, 2, 3]),
            subscribed,
            filters,
        )
        data = json.loads(result)
        assert "error" in data

        # JSON string
        result = _handle_message(
            json.dumps("hello"),
            subscribed,
            filters,
        )
        data = json.loads(result)
        assert "error" in data

        # JSON number
        result = _handle_message(
            json.dumps(42),
            subscribed,
            filters,
        )
        data = json.loads(result)
        assert "error" in data
