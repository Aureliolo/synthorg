"""ntfy notification sink -- HTTP POST to an ntfy server."""

import asyncio
import ipaddress
import math
import re
from typing import TYPE_CHECKING, Self
from urllib.parse import urlparse

import httpx

if TYPE_CHECKING:
    from types import TracebackType

from synthorg.notifications.models import (
    Notification,
    NotificationSeverity,
)
from synthorg.observability import get_logger
from synthorg.observability.events.notification import (
    NOTIFICATION_NTFY_DELIVERED,
    NOTIFICATION_NTFY_FAILED,
)

logger = get_logger(__name__)

_SEVERITY_TO_PRIORITY: dict[NotificationSeverity, str] = {
    NotificationSeverity.INFO: "default",
    NotificationSeverity.WARNING: "high",
    NotificationSeverity.ERROR: "urgent",
    NotificationSeverity.CRITICAL: "max",
}

_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f]")
_ALLOWED_SCHEMES = frozenset({"http", "https"})
_BLOCKED_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def _validate_outbound_url(url: str, field: str) -> None:
    """Reject URLs that target internal/loopback hosts or non-HTTP schemes."""
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        msg = f"{field} must use http or https scheme, got {parsed.scheme!r}"
        raise ValueError(msg)
    host = parsed.hostname or ""
    if host in _BLOCKED_HOSTS:
        msg = f"{field} must not target loopback address"
        raise ValueError(msg)
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        # Not a literal IP -- hostname like "ntfy.example.com".
        # Already checked against _BLOCKED_HOSTS above.
        return
    if addr.is_private or addr.is_link_local or addr.is_loopback:
        msg = f"{field} must not target private/internal IP"
        raise ValueError(msg)


class NtfyNotificationSink:
    """Notification sink that posts to an ntfy server.

    The ``httpx.AsyncClient`` is created lazily inside ``start()``
    and closed inside ``close()`` so a never-started sink leaks
    nothing (#1600). Both methods are idempotent under the
    ``_lifecycle_lock``: a second ``start()`` is a no-op, a
    ``close()`` before ``start()`` is a no-op, concurrent calls
    converge on a single client instance.

    Args:
        server_url: ntfy server base URL (e.g. ``"https://ntfy.sh"``).
        topic: ntfy topic name.
        token: Optional authentication token.
        webhook_timeout_seconds: HTTP timeout for ntfy POST calls, in
            seconds. Mirrors the
            ``notifications.ntfy_webhook_timeout_seconds`` setting;
            the notification factory threads the resolved value in at
            construction so operator tuning takes effect on restart.
            Must be positive.

    Raises:
        ValueError: If *server_url* targets a private/loopback host,
            or if *webhook_timeout_seconds* is not positive.
    """

    __slots__ = (
        "_client",
        "_lifecycle_lock",
        "_server_url",
        "_token",
        "_topic",
        "_webhook_timeout_seconds",
    )

    def __init__(
        self,
        *,
        server_url: str,
        topic: str,
        token: str | None = None,
        webhook_timeout_seconds: float = 10.0,
    ) -> None:
        _validate_outbound_url(server_url, "server_url")
        if not math.isfinite(webhook_timeout_seconds) or webhook_timeout_seconds <= 0:
            msg = (
                "webhook_timeout_seconds must be a finite number > 0, got "
                f"{webhook_timeout_seconds}"
            )
            raise ValueError(msg)
        self._server_url = server_url.rstrip("/")
        self._topic = topic
        self._token = token
        self._webhook_timeout_seconds = webhook_timeout_seconds
        self._client: httpx.AsyncClient | None = None
        self._lifecycle_lock = asyncio.Lock()

    @property
    def sink_name(self) -> str:
        """Return the sink identifier."""
        return "ntfy"

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
        """Close the underlying HTTP client (idempotent)."""
        async with self._lifecycle_lock:
            if self._client is None:
                return
            client = self._client
            self._client = None
            await client.aclose()

    async def __aenter__(self) -> Self:
        """Start the sink; return self for ``async with`` callers."""
        await self.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Close the sink on ``async with`` exit (ignores exception args)."""
        await self.close()

    async def send(self, notification: Notification) -> None:
        """Post the notification to the ntfy server.

        Raises:
            RuntimeError: If called before ``start()``.

        Args:
            notification: The notification to deliver.
        """
        client = self._client
        if client is None:
            msg = "NtfyNotificationSink.send called before start()"
            raise RuntimeError(msg)
        safe_title = _CONTROL_CHAR_RE.sub("", notification.title)
        url = f"{self._server_url}/{self._topic}"
        headers: dict[str, str] = {
            "Title": safe_title,
            "Priority": _SEVERITY_TO_PRIORITY.get(
                notification.severity,
                "default",
            ),
            "Tags": notification.category,
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"

        try:
            response = await client.post(
                url,
                content=notification.body or notification.title,
                headers=headers,
            )
            response.raise_for_status()
            logger.info(
                NOTIFICATION_NTFY_DELIVERED,
                notification_id=notification.id,
                status_code=response.status_code,
            )
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            logger.warning(
                NOTIFICATION_NTFY_FAILED,
                notification_id=notification.id,
                error=str(exc),
            )
            raise
