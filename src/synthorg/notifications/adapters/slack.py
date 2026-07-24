"""Slack notification sink: bot-token Web API (chat.postMessage).

Shares the bound-connection Slack Web API client the agent chat tools
use: the sink resolves a ``SLACK`` connection's bot token from the
connection catalog and posts to a configured channel via
``chat.postMessage``. The connection + client are resolved lazily on the
first send, so a Slack connection created after boot starts working on
the next notification without a restart. Egress is pinned to slack.com by
the chat client factory, so no separate SSRF policy is needed here.
"""

import asyncio
import math
from types import TracebackType
from typing import Final, Self

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.registry.errors import StrategyFactoryNotFoundError
from synthorg.core.types import NotBlankStr
from synthorg.integrations.chat_api import ChatApiClient, build_chat_api_client
from synthorg.integrations.chat_api.inbound import InboundThreadRegistry
from synthorg.integrations.connections.catalog import ConnectionCatalog
from synthorg.integrations.errors import ChatApiError
from synthorg.notifications.models import Notification, NotificationCategory
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.notification import (
    NOTIFICATION_SLACK_DELIVERED,
    NOTIFICATION_SLACK_FAILED,
)

logger = get_logger(__name__)
_DEFAULT_TIMEOUT_SECONDS: Final[float] = 10.0


def _escape_mrkdwn(text: str) -> str:
    """Escape text for Slack mrkdwn to prevent injection of mentions.

    Returns:
        The text with ``&``, ``<`` and ``>`` HTML-escaped.
    """
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _format_message(notification: Notification) -> str:
    """Render a notification as a Slack mrkdwn message body.

    Returns:
        A single mrkdwn string: a severity-tagged header, the optional
        body, and a category/source context line.
    """
    header = (
        f"*[{notification.severity.value.upper()}]* "
        f"{_escape_mrkdwn(notification.title)}"
    )
    parts = [header]
    if notification.body:
        parts.append(_escape_mrkdwn(notification.body))
    parts.append(
        f"Category: {_escape_mrkdwn(notification.category)} | "
        f"Source: {_escape_mrkdwn(notification.source)}"
    )
    return "\n".join(parts)


class SlackNotificationSink:
    """Notification sink that posts via the Slack Web API.

    Args:
        connection_catalog: Catalog used to resolve the ``SLACK``
            connection's bot token at first send.
        connection_name: Name of the bound ``SLACK`` connection.
        channel: Target channel id (or name) to post to.
        timeout_seconds: Per-request HTTP timeout. Must be positive.
        thread_registry: Optional thread correlation. When wired, an
            APPROVAL notification's posted message root is registered
            against its approval id, so the inbound Socket-Mode consumer
            can resolve a threaded human reply back to the parked task.
            ``None`` leaves the sink send-only.

    The client is built lazily on the first send and reused; ``close()``
    releases it. Both lifecycle methods are idempotent under the
    ``_lifecycle_lock``.

    Raises:
        ValueError: If *timeout_seconds* is not a finite number > 0.
    """

    __slots__ = (
        "_catalog",
        "_channel",
        "_client",
        "_connection_name",
        "_lifecycle_lock",
        "_thread_registry",
        "_timeout_seconds",
    )

    def __init__(
        self,
        *,
        connection_catalog: ConnectionCatalog,
        connection_name: str,
        channel: str,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        thread_registry: InboundThreadRegistry | None = None,
    ) -> None:
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            msg = f"timeout_seconds must be a finite number > 0, got {timeout_seconds}"
            raise ValueError(msg)
        self._catalog = connection_catalog
        self._connection_name = connection_name
        self._channel = channel
        self._timeout_seconds = timeout_seconds
        # When wired, an APPROVAL notification's posted message roots a
        # thread the inbound Socket-Mode consumer can resolve back to this
        # approval, so a human's threaded reply resumes the parked task.
        self._thread_registry = thread_registry
        self._client: ChatApiClient | None = None
        self._lifecycle_lock = asyncio.Lock()  # lint-allow: loop-bound-init

    @property
    def sink_name(self) -> str:
        """Return the sink identifier."""
        return "slack"

    async def start(self) -> None:
        """No-op: the Web API client is built lazily on first send."""

    async def close(self) -> None:
        """Release the underlying chat client if it was built (idempotent)."""
        async with self._lifecycle_lock:
            if self._client is None:
                return
            client = self._client
            # Drop the reference first: a failed aclose() must not leave a
            # half-closed client cached for the next send() to reuse.
            self._client = None
            try:
                await client.aclose()
            except Exception as exc:
                reraise_critical(exc)
                logger.warning(
                    NOTIFICATION_SLACK_FAILED,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                    detail="close_failed",
                )
                raise

    async def __aenter__(self) -> Self:
        """Start the sink; return self for ``async with`` callers.

        Returns:
            This sink instance.
        """
        await self.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        _tb: TracebackType | None,
    ) -> None:
        """Close the sink on ``async with`` exit (ignores exception args)."""
        await self.close()

    async def send(self, notification: Notification) -> None:
        """Post the notification to Slack via ``chat.postMessage``.

        Args:
            notification: The notification to deliver.

        Raises:
            ChatApiError: On any Web API / transport failure (logged
                first; the dispatcher swallows it as best-effort).
        """
        client = await self._ensure_client()
        if client is None:
            return
        try:
            ref = await client.send_message(
                channel=NotBlankStr(self._channel),
                text=NotBlankStr(_format_message(notification)),
            )
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                NOTIFICATION_SLACK_FAILED,
                notification_id=str(notification.id),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise
        self._register_approval_thread(notification, ref.channel, ref.ts)
        logger.info(NOTIFICATION_SLACK_DELIVERED, notification_id=str(notification.id))

    def _register_approval_thread(
        self, notification: Notification, channel: str, ts: str
    ) -> None:
        """Correlate an approval notification's posted message for resume.

        The message just posted is the thread root a human replies under;
        registering ``(channel, ts) -> approval_id`` lets the inbound
        consumer route that reply back to the parked approval.
        """
        if (
            self._thread_registry is None
            or notification.category is not NotificationCategory.APPROVAL
        ):
            return
        approval_id = notification.metadata.get("approval_id")
        if isinstance(approval_id, str) and approval_id:
            self._thread_registry.register(
                channel=channel, thread_ts=ts, approval_id=approval_id
            )

    async def _ensure_client(self) -> ChatApiClient | None:
        """Resolve the connection + token and build the client once.

        Returns:
            The chat client, or ``None`` when the connection is absent /
            lacks a token (logged; the sink degrades to a no-op until the
            connection is configured).
        """
        async with self._lifecycle_lock:
            if self._client is not None:
                return self._client
            conn = await self._catalog.get(self._connection_name)
            if conn is None:
                logger.warning(
                    NOTIFICATION_SLACK_FAILED,
                    detail="connection_not_found",
                    connection=self._connection_name,
                )
                return None
            try:
                credentials = await self._catalog.get_credentials(self._connection_name)
            except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                reraise_critical(exc)
                logger.warning(
                    NOTIFICATION_SLACK_FAILED,
                    detail="credential_resolution_failed",
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                return None
            token = credentials.get("token")
            if not token:
                logger.warning(
                    NOTIFICATION_SLACK_FAILED,
                    detail="missing_token",
                    connection=self._connection_name,
                )
                return None
            try:
                self._client = build_chat_api_client(
                    connection_type=conn.connection_type,
                    base_url=str(conn.base_url or ""),
                    token=token,
                    timeout=self._timeout_seconds,
                )
            except (ChatApiError, StrategyFactoryNotFoundError) as exc:
                # A misconfigured base_url or a non-chat connection type
                # degrades to a no-op like the other branches, keeping the
                # sink's documented contract (never crash the dispatcher).
                logger.warning(
                    NOTIFICATION_SLACK_FAILED,
                    detail="client_build_failed",
                    connection=self._connection_name,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                return None
            return self._client
