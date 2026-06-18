"""Notification sender tool -- dispatch notifications via the existing subsystem.

Delegates to the ``NotificationDispatcher`` from
``synthorg.notifications``, which fans out to all configured
sinks (console, email, Slack, ntfy, etc.).
"""

from datetime import UTC, datetime
from typing import ClassVar, Protocol, override, runtime_checkable

from pydantic import BaseModel, ValidationError

from synthorg.core.boundary import parse_typed
from synthorg.core.critical_errors import reraise_critical
from synthorg.notifications.models import Notification
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.communication import (
    COMM_TOOL_NOTIFICATION_SEND_FAILED,
    COMM_TOOL_NOTIFICATION_SEND_START,
    COMM_TOOL_NOTIFICATION_SEND_SUCCESS,
)
from synthorg.security.autonomy.enums import ActionType
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
        arguments: dict[str, object],
    ) -> ToolExecutionResult:
        """Send a notification.

        Args:
            arguments: Must contain ``category``, ``severity``,
                ``title``, and ``source``; optionally ``body``.

        Returns:
            A ``ToolExecutionResult`` with dispatch status.

        Raises:
            ValidationError: If the arguments fail typed-boundary
                validation (the invoker boundary handles it).
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

        # ``parse_typed`` validates ``category`` / ``severity`` against
        # the notification enums and enforces non-blank ``title`` /
        # ``source``, so the membership and isinstance checks below are
        # handled once at the typed boundary. Emit the tool-specific
        # failure event before re-raising so a validation failure is
        # observable under this tool's event (not only the generic invoker
        # error), while the invoker boundary still owns ValidationError.
        try:
            args = parse_typed(
                "tool.notification_sender", arguments, NotificationSenderArgs
            )
        except ValidationError as exc:
            logger.warning(
                COMM_TOOL_NOTIFICATION_SEND_FAILED,
                reason="invalid_arguments",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise
        category_str = args.category.value
        severity_str = args.severity.value
        title = args.title

        try:
            notification = Notification(
                category=args.category,
                severity=args.severity,
                title=args.title,
                body=args.body,
                source=args.source,
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
            notification_id=str(notification.id),
            category=category_str,
            severity=severity_str,
        )

        try:
            await self._dispatcher.dispatch(notification)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                COMM_TOOL_NOTIFICATION_SEND_FAILED,
                notification_id=str(notification.id),
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
            notification_id=str(notification.id),
        )

        return ToolExecutionResult(
            content=(f"Notification dispatched: [{severity_str}] {title}"),
            metadata={
                "notification_id": str(notification.id),
                "category": category_str,
                "severity": severity_str,
            },
        )
