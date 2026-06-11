"""Tests for the notification sender tool."""

from typing import cast

import pytest

from synthorg.security.autonomy.enums import ActionType, ToolCategory
from synthorg.tools.communication.notification_sender import (
    NotificationSenderTool,
)
from tests._shared import JsonDict

from .conftest import MockNotificationDispatcher


@pytest.mark.unit
class TestNotificationSenderTool:
    """Tests for NotificationSenderTool."""

    @pytest.mark.parametrize(
        ("attr", "expected"),
        [
            ("category", ToolCategory.COMMUNICATION),
            ("action_type", ActionType.COMMS_INTERNAL),
            ("name", "notification_sender"),
        ],
        ids=["category", "action_type", "name"],
    )
    def test_tool_attributes(
        self,
        mock_dispatcher: MockNotificationDispatcher,
        attr: str,
        expected: object,
    ) -> None:
        tool = NotificationSenderTool(dispatcher=mock_dispatcher)
        assert getattr(tool, attr) == expected

    async def test_execute_no_dispatcher_returns_error(self) -> None:
        tool = NotificationSenderTool(dispatcher=None)
        result = await tool.execute(
            arguments={
                "category": "system",
                "severity": "info",
                "title": "Test",
                "source": "test-agent",
            }
        )
        assert result.is_error
        assert "NotificationDispatcher" in result.content

    async def test_execute_success(
        self,
        mock_dispatcher: MockNotificationDispatcher,
    ) -> None:
        tool = NotificationSenderTool(dispatcher=mock_dispatcher)
        result = await tool.execute(
            arguments={
                "category": "system",
                "severity": "info",
                "title": "Deployment complete",
                "source": "deploy-agent",
                "body": "All services healthy.",
            }
        )
        assert not result.is_error
        assert "Deployment complete" in result.content
        assert len(mock_dispatcher.dispatched) == 1
        notif = mock_dispatcher.dispatched[0]
        assert notif.title == "Deployment complete"
        assert notif.source == "deploy-agent"

    @pytest.mark.parametrize(
        ("args", "expected_msg"),
        [
            (
                {
                    "category": "invalid",
                    "severity": "info",
                    "title": "Test",
                    "source": "test",
                },
                "Invalid category",
            ),
            (
                {
                    "category": "system",
                    "severity": "invalid",
                    "title": "Test",
                    "source": "test",
                },
                "Invalid severity",
            ),
        ],
        ids=["invalid_category", "invalid_severity"],
    )
    async def test_execute_invalid_enum(
        self,
        mock_dispatcher: MockNotificationDispatcher,
        args: dict[str, object],
        expected_msg: str,
    ) -> None:
        tool = NotificationSenderTool(dispatcher=mock_dispatcher)
        result = await tool.execute(arguments=args)
        assert result.is_error
        assert expected_msg in result.content

    async def test_execute_dispatch_error(
        self,
        failing_dispatcher: MockNotificationDispatcher,
    ) -> None:
        tool = NotificationSenderTool(dispatcher=failing_dispatcher)
        result = await tool.execute(
            arguments={
                "category": "system",
                "severity": "error",
                "title": "Alert",
                "source": "test",
            }
        )
        assert result.is_error
        assert "dispatch failed" in result.content

    async def test_execute_returns_metadata(
        self,
        mock_dispatcher: MockNotificationDispatcher,
    ) -> None:
        tool = NotificationSenderTool(dispatcher=mock_dispatcher)
        result = await tool.execute(
            arguments={
                "category": "budget",
                "severity": "warning",
                "title": "Budget threshold",
                "source": "budget-enforcer",
            }
        )
        assert not result.is_error
        assert result.metadata["category"] == "budget"
        assert result.metadata["severity"] == "warning"
        assert "notification_id" in result.metadata

    def test_parameters_schema_required_fields(
        self,
        mock_dispatcher: MockNotificationDispatcher,
    ) -> None:
        tool = NotificationSenderTool(dispatcher=mock_dispatcher)
        schema = tool.parameters_schema
        assert schema is not None
        for field in ("category", "severity", "title", "source"):
            assert field in cast(JsonDict, schema)["required"]
