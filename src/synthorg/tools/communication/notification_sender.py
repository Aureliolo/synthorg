"""Notification sender tool -- dispatch notifications via the existing subsystem.

Delegates to the ``NotificationDispatcher`` from
``synthorg.notifications``, which fans out to all configured
sinks (console, email, Slack, ntfy, etc.).
"""

from datetime import UTC, datetime
from typing import ClassVar, Final, Protocol, override, runtime_checkable

from pydantic import BaseModel, JsonValue, ValidationError

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.enums import ActionType
from synthorg.notifications.models import (
    Notification,
    NotificationCategory,
    NotificationSeverity,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.communication import (
    COMM_TOOL_NOTIFICATION_SEND_FAILED,
    COMM_TOOL_NOTIFICATION_SEND_START,
    COMM_TOOL_NOTIFICATION_SEND_SUCCESS,
)
from synthorg.tools.base import ToolExecutionResult
from synthorg.tools.communication._args import NotificationSenderArgs
from synthorg.tools.communication.base_communication_tool import (
    BaseCommunicationTool,
)
from synthorg.tools.communication.config import (
    CommunicationToolsConfig,
)


@runtime_checkable
class NotificationDispatcherProtocol(Protocol):
    """Protocol for notification dispatch -- matches ``NotificationDispatcher``."""

    async def dispatch(self, notification: Notification) -> None:
        """Dispatch a notification to all registered sinks."""
        ...


logger = get_logger(__name__)

_VALID_CATEGORIES: Final[frozenset[str]] = frozenset(
    m.value for m in NotificationCategory
)
_VALID_SEVERITIES: Final[frozenset[str]] = frozenset(
    m.value for m in NotificationSeverity
)


class NotificationSenderTool(BaseCommunicationTool):
    """Send notifications via the existing notification subsystem.

    Delegates to the ``NotificationDispatcher`` which fans out
    to all registered sinks (console, ntfy, Slack, email).

    Examples:
        Send a notification::

            tool = NotificationSenderTool(dispatcher=my_dispatcher)
            result = await tool.execute(
                arguments={
                    "category": "system",
                    "severity": "info",
                    "title": "Deployment complete",
                    "source": "deploy-agent",
                }
            )
    """

    args_model: ClassVar[type[BaseModel] | None] = NotificationSenderArgs

    def __init__(
        self,
        *,
        dispatcher: NotificationDispatcherProtocol | None = None,
        config: CommunicationToolsConfig | None = None,
    ) -> None:
        """Initialize the notification sender tool.

        Args:
            dispatcher: Notification dispatcher fanning out to
                registered sinks (console / email / Slack / ntfy).
                ``None`` makes ``execute`` return a configuration error.
            config: Communication tool configuration. ``None`` falls
                back to defaults.
        """
        super().__init__(
            name="notification_sender",
            description=(
                "Send notifications to registered sinks (console, email, Slack, ntfy)."
            ),
            parameters_schema=NotificationSenderArgs.model_json_schema(),
            action_type=ActionType.COMMS_INTERNAL,
            config=config,
        )
        self._dispatcher = dispatcher

    @override
    async def execute(
        self,
        *,
        arguments: dict[str, JsonValue],
    ) -> ToolExecutionResult:
        """Send a notification.

        Args:
            arguments: Must contain ``category``, ``severity``,
                ``title``, and ``source``; optionally ``body``.

        Returns:
            A ``ToolExecutionResult`` with dispatch status.
        """
        if self._dispatcher is None:
            logger.warning(
                COMM_TOOL_NOTIFICATION_SEND_FAILED,
                error="dispatcher_not_configured",
            )
            return ToolExecutionResult(
                content=(
                    "Notification sending requires a configured "
                    "NotificationDispatcher. None was provided."
                ),
                is_error=True,
            )

        raw_body = arguments.get("body", "")
        body = raw_body if isinstance(raw_body, str) else ""
        required_fields = {
            "category": arguments.get("category"),
            "severity": arguments.get("severity"),
            "title": arguments.get("title"),
            "source": arguments.get("source"),
        }
        for field_name, field_val in required_fields.items():
            if not isinstance(field_val, str) or not field_val:
                logger.warning(
                    COMM_TOOL_NOTIFICATION_SEND_FAILED,
                    error="missing_field",
                    field=field_name,
                )
                return ToolExecutionResult(
                    content=(
                        f"'{field_name}' is required and must be a non-empty string."
                    ),
                    is_error=True,
                )

        category_str: str = required_fields["category"]  # type: ignore[assignment]
        severity_str: str = required_fields["severity"]  # type: ignore[assignment]
        title: str = required_fields["title"]  # type: ignore[assignment]
        source: str = required_fields["source"]  # type: ignore[assignment]

        if category_str not in _VALID_CATEGORIES:
            logger.warning(
                COMM_TOOL_NOTIFICATION_SEND_FAILED,
                error="invalid_category",
                category=category_str,
            )
            return ToolExecutionResult(
                content=(
                    f"Invalid category: {category_str!r}. "
                    f"Must be one of: {sorted(_VALID_CATEGORIES)}"
                ),
                is_error=True,
            )

        if severity_str not in _VALID_SEVERITIES:
            logger.warning(
                COMM_TOOL_NOTIFICATION_SEND_FAILED,
                error="invalid_severity",
                severity=severity_str,
            )
            return ToolExecutionResult(
                content=(
                    f"Invalid severity: {severity_str!r}. "
                    f"Must be one of: {sorted(_VALID_SEVERITIES)}"
                ),
                is_error=True,
            )

        try:
            notification = Notification(
                category=NotificationCategory(category_str),
                severity=NotificationSeverity(severity_str),
                title=title,
                body=body,
                source=source,
                timestamp=datetime.now(UTC),
            )
        except (ValueError, TypeError, ValidationError) as exc:
            # Scrub the exception payload before logging or returning
            # -- Pydantic's ValidationError can echo entire input
            # dicts including secret-bearing fields.
            logger.warning(
                COMM_TOOL_NOTIFICATION_SEND_FAILED,
                reason="invalid_notification_fields",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return ToolExecutionResult(
                content="Invalid notification fields",
                is_error=True,
            )

        logger.info(
            COMM_TOOL_NOTIFICATION_SEND_START,
            notification_id=notification.id,
            category=category_str,
            severity=severity_str,
        )

        try:
            await self._dispatcher.dispatch(notification)
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                COMM_TOOL_NOTIFICATION_SEND_FAILED,
                notification_id=notification.id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            # Generic content -- ``ToolExecutionResult.content`` reaches
            # the LLM, so ``exc`` text would leak sink/provider
            # internals past the log scrub above.
            return ToolExecutionResult(
                content="Notification dispatch failed",
                is_error=True,
            )

        logger.info(
            COMM_TOOL_NOTIFICATION_SEND_SUCCESS,
            notification_id=notification.id,
        )

        return ToolExecutionResult(
            content=(f"Notification dispatched: [{severity_str}] {title}"),
            metadata={
                "notification_id": notification.id,
                "category": category_str,
                "severity": severity_str,
            },
        )
