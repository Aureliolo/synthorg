"""Slack notification sink -- webhook POST."""

import asyncio
import math
from typing import TYPE_CHECKING, Final, Self

import httpx

from synthorg.notifications.adapters.ntfy import _validate_outbound_url
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.notification import (
    NOTIFICATION_SLACK_DELIVERED,
    NOTIFICATION_SLACK_FAILED,
)

if TYPE_CHECKING:
    from types import TracebackType

    from synthorg.notifications.models import Notification

logger = get_logger(__name__)
_DEFAULT_WEBHOOK_TIMEOUT_SECONDS: Final[float] = 10.0


def _escape_mrkdwn(text: str) -> str:
    """Escape text for Slack mrkdwn to prevent injection of mentions."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _build_slack_payload(notification: Notification) -> dict[str, object]:
    """Build the Slack Block Kit payload for a notification."""
    safe_title = _escape_mrkdwn(notification.title)
    safe_body = _escape_mrkdwn(notification.body) if notification.body else ""
    safe_category = _escape_mrkdwn(notification.category)
    safe_source = _escape_mrkdwn(notification.source)
    header = f"*[{notification.severity.value.upper()}]* {safe_title}"
    body_text = f"{header}\n{safe_body}" if safe_body else header
    return {
        "text": header,
        "blocks": [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": body_text},
            },
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": (f"Category: {safe_category} | Source: {safe_source}"),
                    },
                ],
            },
        ],
    }


class SlackNotificationSink:
    """Notification sink that posts to a Slack incoming webhook.

    The ``httpx.AsyncClient`` is created lazily inside ``start()``
    and closed inside ``close()`` so a never-started sink leaks
    nothing. Both methods are idempotent under the
    ``_lifecycle_lock``: a second ``start()`` is a no-op, a
    ``close()`` before ``start()`` is a no-op, concurrent calls
    converge on a single client instance.

    Args:
        webhook_url: Slack incoming webhook URL.
        webhook_timeout_seconds: HTTP timeout for webhook POST calls,
            in seconds. Mirrors the
            ``notifications.slack_webhook_timeout_seconds`` setting;
            the notification factory threads the resolved value in at
            construction so operator tuning takes effect on restart.
            Must be positive.

    Raises:
        ValueError: If *webhook_url* targets a private/loopback host,
            or if *webhook_timeout_seconds* is not positive.
    """

    __slots__ = (
        "_client",
        "_lifecycle_lock",
        "_webhook_timeout_seconds",
        "_webhook_url",
    )

    def __init__(
        self,
        *,
        webhook_url: str,
        webhook_timeout_seconds: float = _DEFAULT_WEBHOOK_TIMEOUT_SECONDS,
    ) -> None:
        _validate_outbound_url(webhook_url, "webhook_url")
        if not math.isfinite(webhook_timeout_seconds) or webhook_timeout_seconds <= 0:
            msg = (
                "webhook_timeout_seconds must be a finite number > 0, got "
                f"{webhook_timeout_seconds}"
            )
            raise ValueError(msg)
        self._webhook_url = webhook_url
        self._webhook_timeout_seconds = webhook_timeout_seconds
        self._client: httpx.AsyncClient | None = None
        # Eager init: stop() must be safe before any start() call,
        # so the lock is created here rather than lazily in start().
        self._lifecycle_lock = asyncio.Lock()  # lint-allow: loop-bound-init

    @property
    def sink_name(self) -> str:
        """Return the sink identifier."""
        return "slack"

    async def start(self) -> None:
        """Create the underlying HTTP client (idempotent)."""
        async with self._lifecycle_lock:
            if self._client is not None:
                return
            self._client = httpx.AsyncClient(
                timeout=self._webhook_timeout_seconds,
                follow_redirects=False,
            )

    async def close(self) -> None:
        """Close the underlying HTTP client (idempotent).

        ``self._client`` is cleared only after ``aclose()`` succeeds:
        if the close raises (network error, cancellation, ...), the
        reference stays so a subsequent ``close()`` can retry. Without
        that, an exception or cancellation would silently leak the
        still-open HTTP client and break the idempotency contract.
        Failures are logged before re-raising so standalone
        ``async with`` users see them too -- ``NotificationDispatcher``
        only sees the post-raise log path via ``_safe_close``.
        """
        async with self._lifecycle_lock:
            if self._client is None:
                return
            client = self._client
            try:
                await client.aclose()
            except MemoryError, RecursionError:
                raise
            except Exception as exc:
                logger.warning(
                    NOTIFICATION_SLACK_FAILED,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                    detail="close_failed",
                )
                raise
            self._client = None

    async def __aenter__(self) -> Self:
        """Start the sink; return self for ``async with`` callers."""
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
        """Post the notification to Slack.

        Raises:
            RuntimeError: If called before ``start()``.

        Args:
            notification: The notification to deliver.
        """
        client = self._client
        if client is None:
            logger.warning(
                NOTIFICATION_SLACK_FAILED,
                notification_id=notification.id,
                error_type="RuntimeError",
                detail="send_called_before_start",
            )
            msg = "SlackNotificationSink.send called before start()"
            raise RuntimeError(msg)
        payload = _build_slack_payload(notification)
        try:
            response = await client.post(
                self._webhook_url,
                json=payload,
            )
            response.raise_for_status()
            logger.info(
                NOTIFICATION_SLACK_DELIVERED,
                notification_id=notification.id,
            )
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            logger.warning(
                NOTIFICATION_SLACK_FAILED,
                notification_id=notification.id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise
