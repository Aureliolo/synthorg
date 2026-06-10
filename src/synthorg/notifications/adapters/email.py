"""Email notification sink -- SMTP via asyncio.to_thread."""

import asyncio
import math
import re
import smtplib
import ssl
from email.message import EmailMessage
from typing import Final

from synthorg.core.critical_errors import reraise_critical
from synthorg.notifications.models import Notification
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.notification import (
    NOTIFICATION_EMAIL_DELIVERED,
    NOTIFICATION_EMAIL_FAILED,
    NOTIFICATION_EMAIL_PARTIAL_CREDENTIALS,
)

logger = get_logger(__name__)
_DEFAULT_PORT: Final[int] = 587
_DEFAULT_SMTP_TIMEOUT_SECONDS: Final[float] = 10.0

_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f]")


class EmailNotificationSink:
    """Notification sink that sends email via SMTP.

    Uses stdlib ``smtplib`` wrapped in ``asyncio.to_thread`` to
    avoid blocking the event loop and avoid adding ``aiosmtplib``
    as a dependency.

    Args:
        host: SMTP server host.
        port: SMTP server port.
        username: SMTP authentication username (optional).
        password: SMTP authentication password (optional).
        from_addr: Sender email address.
        to_addrs: Recipient email addresses.
        use_tls: Whether to use STARTTLS.
        smtp_timeout_seconds: SMTP connection timeout in seconds.
            Mirrors the ``notifications.email_smtp_timeout_seconds``
            setting; the notification factory threads the resolved
            value in at construction so operator tuning takes effect
            on restart. Must be positive.

    Raises:
        ValueError: If *smtp_timeout_seconds* is not positive.
    """

    __slots__ = (
        "_from_addr",
        "_host",
        "_password",
        "_port",
        "_smtp_timeout_seconds",
        "_to_addrs",
        "_use_tls",
        "_username",
    )

    def __init__(  # noqa: PLR0913
        self,
        *,
        host: str,
        port: int = _DEFAULT_PORT,
        username: str | None = None,
        password: str | None = None,
        from_addr: str,
        to_addrs: tuple[str, ...],
        use_tls: bool = True,
        smtp_timeout_seconds: float = _DEFAULT_SMTP_TIMEOUT_SECONDS,
    ) -> None:
        if not math.isfinite(smtp_timeout_seconds) or smtp_timeout_seconds <= 0:
            msg = (
                "smtp_timeout_seconds must be a finite number > 0, got "
                f"{smtp_timeout_seconds}"
            )
            raise ValueError(msg)
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._from_addr = from_addr
        self._to_addrs = to_addrs
        self._use_tls = use_tls
        self._smtp_timeout_seconds = smtp_timeout_seconds

    @property
    def sink_name(self) -> str:
        """Return the sink identifier."""
        return "email"

    async def start(self) -> None:
        """No-op: SMTP connections are opened per ``send`` call."""

    async def close(self) -> None:
        """No-op: SMTP connections are closed per ``send`` call."""

    async def send(self, notification: Notification) -> None:
        """Send the notification via SMTP.

        Args:
            notification: The notification to deliver.
        """
        try:
            await asyncio.to_thread(self._send_sync, notification)
            logger.info(
                NOTIFICATION_EMAIL_DELIVERED,
                notification_id=str(notification.id),
                to_count=len(self._to_addrs),
            )
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                NOTIFICATION_EMAIL_FAILED,
                notification_id=str(notification.id),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise

    def _send_sync(self, notification: Notification) -> None:
        """Synchronous SMTP send (runs in a thread)."""
        safe_title = _CONTROL_CHAR_RE.sub("", notification.title)
        msg = EmailMessage()
        msg["Subject"] = (
            f"[SynthOrg {notification.severity.value.upper()}] {safe_title}"
        )
        msg["From"] = self._from_addr
        msg["To"] = ", ".join(self._to_addrs)
        msg.set_content(
            f"{notification.title}\n\n"
            f"{notification.body}\n\n"
            f"Category: {notification.category}\n"
            f"Source: {notification.source}\n"
            f"Timestamp: {notification.timestamp.isoformat()}"
        )

        with smtplib.SMTP(
            self._host, self._port, timeout=self._smtp_timeout_seconds
        ) as smtp:
            if self._use_tls:
                context = ssl.create_default_context()
                smtp.starttls(context=context)
            self._login_if_configured(smtp)
            smtp.send_message(msg)

    def _login_if_configured(self, smtp: smtplib.SMTP) -> None:
        """Log in to SMTP if both username and password are set."""
        has_user = bool(self._username)
        has_pass = bool(self._password)
        if has_user != has_pass:
            logger.warning(
                NOTIFICATION_EMAIL_PARTIAL_CREDENTIALS,
                has_username=has_user,
                has_password=has_pass,
            )
        if self._username and self._password:
            smtp.login(self._username, self._password)
