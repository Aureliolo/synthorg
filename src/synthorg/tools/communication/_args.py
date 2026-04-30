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

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr
from synthorg.notifications.models import (
    NotificationCategory,  # noqa: TC001
    NotificationSeverity,  # noqa: TC001
)

_ARGS_CONFIG = ConfigDict(
    frozen=True,
    allow_inf_nan=False,
    extra="forbid",
)


# ── Email ───────────────────────────────────────────────────────────

# Loose RFC 5322 sanity-check at the wire boundary: must contain a
# single ``@`` between two non-empty groups and must not contain any
# control characters that would smuggle into SMTP headers.  Strict
# RFC compliance is the SMTP backend's job; this guard catches obvious
# garbage at the LLM-facing surface.
_EMAIL_RE = r"^[^@\r\n\t \"]+@[^@\r\n\t \"]+\.[^@\r\n\t \"]+$"

EmailAddress = Annotated[
    NotBlankStr,
    Field(
        pattern=_EMAIL_RE,
        description="Email address (loose RFC 5322 shape)",
    ),
]


class EmailSenderArgs(BaseModel):
    """Args for ``email_sender``.

    Recipient-count limits and full SMTP-header injection guards stay
    inside the tool body because they depend on per-instance
    ``EmailConfig``.  The model enforces non-blank addresses with a
    loose RFC 5322 shape regex (``user@host.tld``) on every recipient
    so obvious garbage is rejected at the LLM-facing surface.
    """

    model_config = _ARGS_CONFIG

    to: tuple[EmailAddress, ...] = Field(
        min_length=1,
        description="Recipient email addresses",
    )
    subject: NotBlankStr = Field(description="Email subject line")
    cc: tuple[EmailAddress, ...] = Field(
        default=(),
        description="CC email addresses",
    )
    bcc: tuple[EmailAddress, ...] = Field(
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
        default_factory=dict,
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
