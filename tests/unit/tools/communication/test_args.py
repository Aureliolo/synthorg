"""Tests for typed communication tool argument models."""

import pytest
from pydantic import ValidationError

from synthorg.notifications.models import (
    NotificationCategory,
    NotificationSeverity,
)
from synthorg.tools.communication._args import (
    CancelAsyncTaskArgs,
    CheckAsyncTaskArgs,
    EmailSenderArgs,
    ListAsyncTasksArgs,
    NotificationSenderArgs,
    StartAsyncTaskArgs,
    TemplateFormatterArgs,
    UpdateAsyncTaskArgs,
)


class TestEmailSenderArgs:
    @pytest.mark.unit
    def test_minimal(self) -> None:
        args = EmailSenderArgs(to=("a@b.com",), subject="hi")
        assert args.body == ""
        assert args.body_is_html is False
        assert args.cc == ()
        assert args.bcc == ()

    @pytest.mark.unit
    def test_empty_recipient_list_rejected(self) -> None:
        with pytest.raises(ValidationError):
            EmailSenderArgs(to=(), subject="hi")

    @pytest.mark.unit
    def test_blank_address_rejected(self) -> None:
        with pytest.raises(ValidationError):
            EmailSenderArgs(to=("   ",), subject="hi")


class TestNotificationSenderArgs:
    @pytest.mark.unit
    def test_construction(self) -> None:
        args = NotificationSenderArgs(
            category=NotificationCategory.SYSTEM,
            severity=NotificationSeverity.INFO,
            title="Deploy complete",
            source="deploy-agent",
        )
        assert args.body == ""

    @pytest.mark.unit
    def test_invalid_category_rejected(self) -> None:
        with pytest.raises(ValidationError):
            NotificationSenderArgs.model_validate(
                {
                    "category": "not_a_category",
                    "severity": "info",
                    "title": "x",
                    "source": "y",
                },
            )


class TestTemplateFormatterArgs:
    @pytest.mark.unit
    def test_default_format(self) -> None:
        args = TemplateFormatterArgs(
            template="Hello {{ name }}",
            variables={"name": "Alice"},
        )
        assert args.format == "text"

    @pytest.mark.unit
    def test_format_is_closed_literal(self) -> None:
        for fmt in ("text", "html", "markdown"):
            args = TemplateFormatterArgs.model_validate(
                {"template": "x", "variables": {}, "format": fmt},
            )
            assert args.format == fmt
        with pytest.raises(ValidationError):
            TemplateFormatterArgs.model_validate(
                {"template": "x", "variables": {}, "format": "rst"},
            )


class TestAsyncTaskArgs:
    @pytest.mark.unit
    def test_start(self) -> None:
        args = StartAsyncTaskArgs(
            agent_id="a1",
            title="t",
            description="d",
        )
        assert args.agent_id == "a1"

    @pytest.mark.unit
    def test_check(self) -> None:
        args = CheckAsyncTaskArgs(task_id="t1")
        assert args.task_id == "t1"

    @pytest.mark.unit
    def test_update_requires_instructions(self) -> None:
        with pytest.raises(ValidationError):
            UpdateAsyncTaskArgs.model_validate({"task_id": "t1"})

    @pytest.mark.unit
    def test_cancel(self) -> None:
        args = CancelAsyncTaskArgs(task_id="t1")
        assert args.task_id == "t1"

    @pytest.mark.unit
    def test_list_optional_scope(self) -> None:
        args = ListAsyncTasksArgs()
        assert args.supervisor_task_id is None
