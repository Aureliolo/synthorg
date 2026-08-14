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

    async def dispatch(self, notification: Notification) -> int:
        """Dispatch a notification to all registered sinks.

        Returns:
            How many sinks accepted it.
        """
        ...


logger = get_logger(__name__)


def _build_notification(arguments: dict[str, object]) -> Notification | None:
    """Validate *arguments* at the typed boundary and build the notification.

    ``parse_typed`` validates ``category`` / ``severity`` against the
    notification enums and enforces non-blank ``title`` / ``source``, so every
    membership and isinstance question is settled once, here. The
    tool-specific failure event is emitted before re-raising so a validation
    failure is observable under this tool's event rather than only the generic
    invoker error, while the invoker boundary still owns ``ValidationError``.

    Args:
        arguments: The raw tool arguments.

    Returns:
        The notification, or ``None`` when its fields were rejected at
        construction. The caller supplies the agent-facing wording; the
        diagnosis stays in the log, because a Pydantic ``ValidationError`` can
        echo entire input dicts including secret-bearing fields.

    Raises:
        ValidationError: If the arguments fail the typed boundary.
    """
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

    try:
        return Notification(
            category=args.category,
            severity=args.severity,
            title=args.title,
            body=args.body,
            source=args.source,
            timestamp=datetime.now(UTC),
        )
    except (ValueError, TypeError, ValidationError) as exc:
        logger.warning(
            COMM_TOOL_NOTIFICATION_SEND_FAILED,
            reason="invalid_notification_fields",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None


async def _deliver(
    dispatcher: NotificationDispatcherProtocol,
    notification: Notification,
) -> ToolExecutionResult:
    """Hand *notification* to *dispatcher* and report what the sinks did.

    Args:
        dispatcher: The fan-out the notification is handed to.
        notification: What to deliver.

    Returns:
        The tool result. An error result covers both ways delivery can fail:
        the dispatch raised, or it returned cleanly having reached nobody.
    """
    try:
        accepted = await dispatcher.dispatch(notification)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            COMM_TOOL_NOTIFICATION_SEND_FAILED,
            notification_id=str(notification.id),
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        # Generic content -- ``ToolExecutionResult.content`` reaches the LLM,
        # so ``exc`` text would leak sink/provider internals past the log
        # scrub above.
        return ToolExecutionResult(
            content="Notification dispatch failed",
            is_error=True,
        )

    if accepted == 0:
        # The dispatcher returns cleanly when notifications are switched off,
        # the severity floor filtered this one, no sink is registered or it is
        # shutting down. Reporting success on any of those tells the agent its
        # message reached a person when nothing did.
        logger.warning(
            COMM_TOOL_NOTIFICATION_SEND_FAILED,
            notification_id=str(notification.id),
            reason="no_sink_accepted",
        )
        return ToolExecutionResult(
            content="Notification was not accepted by any sink",
            is_error=True,
        )

    logger.info(
        COMM_TOOL_NOTIFICATION_SEND_SUCCESS,
        notification_id=str(notification.id),
        accepted_sinks=accepted,
    )
    severity = notification.severity.value
    return ToolExecutionResult(
        content=f"Notification dispatched: [{severity}] {notification.title}",
        metadata={
            "notification_id": str(notification.id),
            "category": notification.category.value,
            "severity": severity,
        },
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

        notification = _build_notification(arguments)
        if notification is None:
            return ToolExecutionResult(
                content="Invalid notification fields",
                is_error=True,
            )

        logger.info(
            COMM_TOOL_NOTIFICATION_SEND_START,
            notification_id=str(notification.id),
            category=notification.category.value,
            severity=notification.severity.value,
        )
        return await _deliver(self._dispatcher, notification)
