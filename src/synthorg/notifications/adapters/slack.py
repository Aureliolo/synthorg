"""Slack notification sink: bot-token Web API (chat.postMessage).

Unified onto the same bound-connection Slack Web API the agent chat tools
use: the sink resolves a ``SLACK`` connection's bot token from the
connection catalog and posts to a configured channel via
``chat.postMessage``. The connection + client are resolved lazily on the
first send, so a Slack connection created after boot starts working on
the next notification without a restart. Egress is pinned to slack.com by
the chat client factory (no separate SSRF policy needed).
"""

import asyncio
import math
from types import TracebackType
from typing import Final, Self

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.integrations.chat_api import ChatApiClient, build_chat_api_client
from synthorg.integrations.connections.catalog import ConnectionCatalog
from synthorg.notifications.models import Notification
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
        "_timeout_seconds",
    )

    def __init__(
        self,
        *,
        connection_catalog: ConnectionCatalog,
        connection_name: str,
        channel: str,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            msg = f"timeout_seconds must be a finite number > 0, got {timeout_seconds}"
            raise ValueError(msg)
        self._catalog = connection_catalog
        self._connection_name = connection_name
        self._channel = channel
        self._timeout_seconds = timeout_seconds
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
            self._client = None

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
            await client.send_message(
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
        logger.info(NOTIFICATION_SLACK_DELIVERED, notification_id=str(notification.id))

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
            self._client = build_chat_api_client(
                connection_type=conn.connection_type,
                base_url=str(conn.base_url or ""),
                token=token,
                timeout=self._timeout_seconds,
            )
            return self._client
