"""Typed argument models for communication tools.

Tools wired to consume these models:

* :class:`~synthorg.tools.communication.email_sender.EmailSenderTool`
  -> :class:`EmailSenderArgs`
* :class:`~synthorg.tools.communication.notification_sender.NotificationSenderTool`
  -> :class:`NotificationSenderArgs`
* :class:`~synthorg.tools.communication.template_formatter.TemplateFormatterTool`
  -> :class:`TemplateFormatterArgs`
* :class:`~synthorg.tools.communication.async_task_tools.StartAsyncTaskTool`
  -> :class:`StartAsyncTaskArgs`
* :class:`~synthorg.tools.communication.async_task_tools.CheckAsyncTaskTool`
  -> :class:`CheckAsyncTaskArgs`
* :class:`~synthorg.tools.communication.async_task_tools.UpdateAsyncTaskTool`
  -> :class:`UpdateAsyncTaskArgs`
* :class:`~synthorg.tools.communication.async_task_tools.CancelAsyncTaskTool`
  -> :class:`CancelAsyncTaskArgs`
* :class:`~synthorg.tools.communication.async_task_tools.ListAsyncTasksTool`
  -> :class:`ListAsyncTasksArgs`
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr  # noqa: TC001 -- Pydantic field type
from synthorg.notifications.models import (
    NotificationCategory,  # noqa: TC001 -- Pydantic field type
    NotificationSeverity,  # noqa: TC001 -- Pydantic field type
)

_ARGS_CONFIG = ConfigDict(
    frozen=True,
    allow_inf_nan=False,
    extra="forbid",
)


# ── Email ───────────────────────────────────────────────────────────


class EmailSenderArgs(BaseModel):
    """Args for ``email_sender``.

    Recipient-count limits and SMTP-header injection guards stay inside
    the tool body because they depend on per-instance ``EmailConfig``;
    the model only enforces non-blank, control-char-free strings on
    each address (the regex check moves to a ``model_validator`` if
    desired in a follow-up).
    """

    model_config = _ARGS_CONFIG

    to: tuple[NotBlankStr, ...] = Field(
        min_length=1,
        description="Recipient email addresses",
    )
    subject: NotBlankStr = Field(description="Email subject line")
    cc: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="CC email addresses",
    )
    bcc: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="BCC email addresses",
    )
    body: str = Field(default="", description="Email body content")
    body_is_html: bool = Field(
        default=False,
        description="Whether body is HTML (default: plain text)",
    )


# ── Notification ────────────────────────────────────────────────────


class NotificationSenderArgs(BaseModel):
    """Args for ``notification_sender``."""

    model_config = _ARGS_CONFIG

    category: NotificationCategory = Field(description="Notification category")
    severity: NotificationSeverity = Field(description="Notification severity level")
    title: NotBlankStr = Field(description="Notification title")
    source: NotBlankStr = Field(description="Source subsystem or agent name")
    body: str = Field(default="", description="Detailed notification body")


# ── Template formatter ──────────────────────────────────────────────


TemplateOutputFormat = Literal["text", "html", "markdown"]


class TemplateFormatterArgs(BaseModel):
    """Args for ``template_formatter``."""

    model_config = _ARGS_CONFIG

    template: NotBlankStr = Field(description="Inline Jinja2 template string")
    variables: dict[str, object] = Field(
        description="Variable bindings for template rendering",
    )
    format: TemplateOutputFormat = Field(
        default="text",
        description="Output format",
    )


# ── Async-task tools ────────────────────────────────────────────────


class StartAsyncTaskArgs(BaseModel):
    """Args for ``start_async_task``."""

    model_config = _ARGS_CONFIG

    agent_id: NotBlankStr = Field(description="Target agent ID")
    title: NotBlankStr = Field(description="Short task title")
    description: NotBlankStr = Field(description="Detailed task description")


class CheckAsyncTaskArgs(BaseModel):
    """Args for ``check_async_task``."""

    model_config = _ARGS_CONFIG

    task_id: NotBlankStr = Field(description="Task ID to check")


class UpdateAsyncTaskArgs(BaseModel):
    """Args for ``update_async_task``."""

    model_config = _ARGS_CONFIG

    task_id: NotBlankStr = Field(description="Task ID to update")
    instructions: NotBlankStr = Field(
        description="New instructions for the executing agent",
    )


class CancelAsyncTaskArgs(BaseModel):
    """Args for ``cancel_async_task``."""

    model_config = _ARGS_CONFIG

    task_id: NotBlankStr = Field(description="Task ID to cancel")


class ListAsyncTasksArgs(BaseModel):
    """Args for ``list_async_tasks``: optional supervisor scope."""

    model_config = _ARGS_CONFIG

    supervisor_task_id: NotBlankStr | None = Field(
        default=None,
        description="Supervisor task ID (optional, uses default if omitted)",
    )


__all__ = [
    "CancelAsyncTaskArgs",
    "CheckAsyncTaskArgs",
    "EmailSenderArgs",
    "ListAsyncTasksArgs",
    "NotificationSenderArgs",
    "StartAsyncTaskArgs",
    "TemplateFormatterArgs",
    "TemplateOutputFormat",
    "UpdateAsyncTaskArgs",
]
