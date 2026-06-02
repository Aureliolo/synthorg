"""Tests for the email sender tool."""

from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest

from synthorg.core.enums import ActionType, ToolCategory
from synthorg.tools.communication.config import CommunicationToolsConfig, EmailConfig
from synthorg.tools.communication.email_sender import EmailSenderTool


@pytest.mark.unit
class TestEmailSenderTool:
    """Tests for EmailSenderTool."""

    @pytest.mark.parametrize(
        ("attr", "expected"),
        [
            ("category", ToolCategory.COMMUNICATION),
            ("action_type", ActionType.COMMS_EXTERNAL),
            ("name", "email_sender"),
        ],
        ids=["category", "action_type", "name"],
    )
    def test_tool_attributes(
        self,
        comm_config: CommunicationToolsConfig,
        attr: str,
        expected: object,
    ) -> None:
        tool = EmailSenderTool(config=comm_config)
        assert getattr(tool, attr) == expected

    async def test_execute_no_email_config_returns_error(
        self,
        comm_config_no_email: CommunicationToolsConfig,
    ) -> None:
        tool = EmailSenderTool(config=comm_config_no_email)
        result = await tool.execute(
            arguments={
                "to": ["user@example.com"],
                "subject": "Test",
            }
        )
        assert result.is_error
        assert "SMTP configuration" in result.content

    async def test_execute_empty_recipients_returns_error(
        self,
        comm_config: CommunicationToolsConfig,
    ) -> None:
        tool = EmailSenderTool(config=comm_config)
        result = await tool.execute(arguments={"to": [], "subject": "Test"})
        assert result.is_error
        assert "At least one recipient" in result.content

    async def test_execute_too_many_recipients(self) -> None:
        config = CommunicationToolsConfig(
            email=EmailConfig(
                host="smtp.example.com",
                from_address="test@example.com",
            ),
            max_recipients=2,
        )
        tool = EmailSenderTool(config=config)
        result = await tool.execute(
            arguments={
                "to": ["a@ex.com", "b@ex.com", "c@ex.com"],
                "subject": "Test",
            }
        )
        assert result.is_error
        assert "Too many recipients" in result.content

    @patch.object(EmailSenderTool, "_send_sync")
    async def test_execute_success(
        self,
        mock_send: MagicMock,
        comm_config: CommunicationToolsConfig,
    ) -> None:
        tool = EmailSenderTool(config=comm_config)
        result = await tool.execute(
            arguments={
                "to": ["user@example.com"],
                "subject": "Hello",
                "body": "World",
            }
        )
        assert not result.is_error
        assert "sent successfully" in result.content
        assert result.metadata["to"] == ["user@example.com"]
        mock_send.assert_called_once()

    @patch.object(
        EmailSenderTool,
        "_send_sync",
        side_effect=RuntimeError("SMTP error"),
    )
    async def test_execute_smtp_error(
        self,
        mock_send: MagicMock,
        comm_config: CommunicationToolsConfig,
    ) -> None:
        tool = EmailSenderTool(config=comm_config)
        result = await tool.execute(
            arguments={
                "to": ["user@example.com"],
                "subject": "Test",
            }
        )
        assert result.is_error
        assert "Email sending failed" in result.content
        mock_send.assert_called_once()

    @patch.object(EmailSenderTool, "_send_sync")
    async def test_execute_with_cc_and_bcc(
        self,
        mock_send: MagicMock,
        comm_config: CommunicationToolsConfig,
    ) -> None:
        tool = EmailSenderTool(config=comm_config)
        result = await tool.execute(
            arguments={
                "to": ["a@ex.com"],
                "cc": ["b@ex.com"],
                "bcc": ["c@ex.com"],
                "subject": "Test",
            }
        )
        assert not result.is_error
        assert "3 recipient(s)" in result.content
        mock_send.assert_called_once()

    @patch.object(EmailSenderTool, "_send_sync")
    async def test_execute_accepts_tuple_recipients(
        self,
        mock_send: MagicMock,
        comm_config: CommunicationToolsConfig,
    ) -> None:
        """Accept tuple ``to``/``cc``/``bcc`` recipient values.

        ``ToolInvoker`` passes ``args_model.model_dump(mode="python")``,
        whose recipient fields are tuples; the handler must accept them
        rather than rejecting with "must be a list".
        """
        tool = EmailSenderTool(config=comm_config)
        result = await tool.execute(
            arguments={
                "to": ("a@ex.com",),
                "cc": ("b@ex.com",),
                "subject": "Hi",
                "body": "x",
            },
        )
        assert not result.is_error
        assert "2 recipient(s)" in result.content
        mock_send.assert_called_once()

    @patch.object(EmailSenderTool, "_send_sync")
    async def test_execute_rejects_newline_in_address(
        self,
        mock_send: MagicMock,
        comm_config: CommunicationToolsConfig,
    ) -> None:
        tool = EmailSenderTool(config=comm_config)
        result = await tool.execute(
            arguments={
                "to": ["attacker@ex.com\nBcc: victim@ex.com"],
                "subject": "Test",
            }
        )
        assert result.is_error
        assert "invalid characters" in result.content
        mock_send.assert_not_called()

    def test_parameters_schema_requires_to_and_subject(
        self,
        comm_config: CommunicationToolsConfig,
    ) -> None:
        tool = EmailSenderTool(config=comm_config)
        schema = tool.parameters_schema
        assert schema is not None
        required = cast("dict[str, Any]", schema)["required"]
        assert "to" in required
        assert "subject" in required
