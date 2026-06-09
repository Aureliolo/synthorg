"""Email sender tool -- send emails via SMTP.

Uses stdlib ``smtplib`` wrapped in ``asyncio.to_thread`` to avoid
blocking the event loop, following the same pattern as
``EmailNotificationSink``.
"""

import asyncio
import re
import smtplib
import ssl
from email.message import EmailMessage
from typing import ClassVar, Final, cast, override

from pydantic import BaseModel, JsonValue

from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger
from synthorg.observability.events.communication import (
    COMM_TOOL_EMAIL_SEND_FAILED,
    COMM_TOOL_EMAIL_SEND_START,
    COMM_TOOL_EMAIL_SEND_SUCCESS,
    COMM_TOOL_EMAIL_VALIDATION_FAILED,
)
from synthorg.security.autonomy.enums import ActionType
from synthorg.tools.base import ToolExecutionResult
from synthorg.tools.communication._args import EmailSenderArgs
from synthorg.tools.communication.base_communication_tool import (
    BaseCommunicationTool,
)
from synthorg.tools.communication.config import (
    CommunicationToolsConfig,
    EmailConfig,
)

logger = get_logger(__name__)

_CONTROL_CHAR_RE: Final[re.Pattern[str]] = re.compile(r"[\x00-\x1f\x7f]")

# Reject addresses with newlines/carriage returns (header injection).
_UNSAFE_ADDR_RE: Final[re.Pattern[str]] = re.compile(r"[\r\n]")


class EmailSenderTool(BaseCommunicationTool):
    """Send emails via SMTP.

    Requires ``EmailConfig`` in the communication tools config.
    Uses stdlib ``smtplib`` with ``asyncio.to_thread`` for
    non-blocking execution.

    Examples:
        Send a plain text email::

            tool = EmailSenderTool(config=comm_config)
            result = await tool.execute(
                arguments={
                    "to": ["user@example.com"],
                    "subject": "Hello",
                    "body": "World",
                }
            )
    """

    args_model: ClassVar[type[BaseModel] | None] = EmailSenderArgs

    def __init__(
        self,
        *,
        config: CommunicationToolsConfig | None = None,
    ) -> None:
        """Initialize the email sender tool.

        Args:
            config: Communication tool configuration with email/SMTP
                settings, recipient-count caps, and allowlist.
                ``None`` falls back to defaults.
        """
        super().__init__(
            name="email_sender",
            description=(
                "Send emails via SMTP. Supports plain text and HTML body content."
            ),
            parameters_schema=EmailSenderArgs.model_json_schema(),
            action_type=ActionType.COMMS_EXTERNAL,
            config=config,
        )

    @override
    async def execute(
        self,
        *,
        arguments: dict[str, object],
    ) -> ToolExecutionResult:
        """Send an email.

        Args:
            arguments: Must contain ``to`` and ``subject``;
                optionally ``cc``, ``bcc``, ``body``,
                ``body_is_html``.

        Returns:
            A ``ToolExecutionResult`` with delivery status.
        """
        email_config = self._config.email
        if email_config is None:
            logger.warning(
                COMM_TOOL_EMAIL_SEND_FAILED,
                error="email_not_configured",
            )
            return ToolExecutionResult(
                content=(
                    "Email sending requires SMTP configuration. "
                    "Set 'email' in CommunicationToolsConfig."
                ),
                is_error=True,
            )

        to_raw = arguments.get("to")
        if not isinstance(to_raw, (list, tuple)) or any(
            not isinstance(addr, str) for addr in to_raw
        ):
            logger.warning(
                COMM_TOOL_EMAIL_VALIDATION_FAILED,
                reason="invalid_to",
            )
            return ToolExecutionResult(
                content="'to' must be a list of email addresses.",
                is_error=True,
            )
        # Direct callers (bypassing the invoker's args_model validation)
        # can still send non-list cc/bcc, which would raise TypeError on
        # the recipient concatenation below.  Reject the container shape
        # AND the element types here; otherwise the
        # ``_UNSAFE_ADDR_RE.search(addr)`` call later would raise
        # ``TypeError`` and bypass the structured envelope.
        cc_raw = arguments.get("cc")
        if cc_raw is not None and (
            not isinstance(cc_raw, (list, tuple))
            or any(not isinstance(addr, str) for addr in cc_raw)
        ):
            logger.warning(
                COMM_TOOL_EMAIL_VALIDATION_FAILED,
                reason="invalid_cc",
            )
            return ToolExecutionResult(
                content="'cc' must be a list of email addresses.",
                is_error=True,
            )
        bcc_raw = arguments.get("bcc")
        if bcc_raw is not None and (
            not isinstance(bcc_raw, (list, tuple))
            or any(not isinstance(addr, str) for addr in bcc_raw)
        ):
            logger.warning(
                COMM_TOOL_EMAIL_VALIDATION_FAILED,
                reason="invalid_bcc",
            )
            return ToolExecutionResult(
                content="'bcc' must be a list of email addresses.",
                is_error=True,
            )
        to_addrs = [addr for addr in to_raw if isinstance(addr, str)]
        cc_addrs = [addr for addr in (cc_raw or []) if isinstance(addr, str)]
        bcc_addrs = [addr for addr in (bcc_raw or []) if isinstance(addr, str)]
        subject = arguments.get("subject")
        if not isinstance(subject, str):
            logger.warning(
                COMM_TOOL_EMAIL_VALIDATION_FAILED,
                reason="invalid_subject",
            )
            return ToolExecutionResult(
                content="'subject' must be a string.",
                is_error=True,
            )
        body = cast("str", arguments.get("body", ""))
        body_is_html = cast("bool", arguments.get("body_is_html", False))

        all_recipients = to_addrs + cc_addrs + bcc_addrs
        if not all_recipients:
            logger.warning(
                COMM_TOOL_EMAIL_VALIDATION_FAILED,
                reason="no_recipients",
            )
            return ToolExecutionResult(
                content="At least one recipient is required.",
                is_error=True,
            )

        if len(all_recipients) > self._config.max_recipients:
            logger.warning(
                COMM_TOOL_EMAIL_VALIDATION_FAILED,
                reason="too_many_recipients",
                count=len(all_recipients),
                limit=self._config.max_recipients,
            )
            return ToolExecutionResult(
                content=(
                    f"Too many recipients: {len(all_recipients)} "
                    f"(max {self._config.max_recipients})"
                ),
                is_error=True,
            )

        # Reject addresses with newlines (header injection prevention).
        for addr in [*all_recipients, email_config.from_address]:
            if _UNSAFE_ADDR_RE.search(addr):
                logger.warning(
                    COMM_TOOL_EMAIL_VALIDATION_FAILED,
                    reason="unsafe_address",
                )
                return ToolExecutionResult(
                    content="Email address contains invalid characters.",
                    is_error=True,
                )

        logger.info(
            COMM_TOOL_EMAIL_SEND_START,
            to_count=len(to_addrs),
            cc_count=len(cc_addrs),
            bcc_count=len(bcc_addrs),
            subject_length=len(subject),
        )

        try:
            await asyncio.to_thread(
                self._send_sync,
                email_config=email_config,
                to_addrs=to_addrs,
                cc_addrs=cc_addrs,
                all_recipients=all_recipients,
                subject=subject,
                body=body,
                body_is_html=body_is_html,
            )
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                COMM_TOOL_EMAIL_SEND_FAILED,
                error="smtp_error",
                recipient_count=len(all_recipients),
            )
            return ToolExecutionResult(
                content="Email sending failed.",
                is_error=True,
            )

        logger.info(
            COMM_TOOL_EMAIL_SEND_SUCCESS,
            recipient_count=len(all_recipients),
        )

        to_meta = cast("list[JsonValue]", to_addrs)
        cc_meta = cast("list[JsonValue]", cc_addrs)
        return ToolExecutionResult(
            content=(f"Email sent successfully to {len(all_recipients)} recipient(s)."),
            metadata={
                "to": to_meta,
                "cc": cc_meta,
                "bcc_count": len(bcc_addrs),
                "subject": subject,
            },
        )

    @staticmethod
    def _send_sync(  # noqa: PLR0913
        *,
        email_config: EmailConfig,
        to_addrs: list[str],
        cc_addrs: list[str],
        all_recipients: list[str],
        subject: str,
        body: str,
        body_is_html: bool,
    ) -> None:
        """Synchronous SMTP send (runs in a thread).

        Args:
            email_config: EmailConfig with SMTP settings.
            to_addrs: Primary recipients.
            cc_addrs: CC recipients.
            all_recipients: Combined recipient list for envelope.
            subject: Email subject.
            body: Email body.
            body_is_html: Whether body is HTML.
        """
        safe_subject = _CONTROL_CHAR_RE.sub("", subject)
        msg = EmailMessage()
        msg["Subject"] = safe_subject
        msg["From"] = email_config.from_address
        msg["To"] = ", ".join(to_addrs)
        if cc_addrs:
            msg["Cc"] = ", ".join(cc_addrs)

        if body_is_html:
            msg.set_content(body, subtype="html")
        else:
            msg.set_content(body)

        timeout = email_config.smtp_timeout
        context = ssl.create_default_context()
        smtp_conn: smtplib.SMTP
        if email_config.use_implicit_tls:
            smtp_conn = smtplib.SMTP_SSL(
                email_config.host,
                email_config.port,
                timeout=timeout,
                context=context,
            )
        else:
            smtp_conn = smtplib.SMTP(
                email_config.host, email_config.port, timeout=timeout
            )
        with smtp_conn as smtp:
            if not email_config.use_implicit_tls and email_config.use_tls:
                smtp.starttls(context=context)
            if email_config.username and email_config.password:
                smtp.login(email_config.username, email_config.password)
            smtp.send_message(msg, to_addrs=all_recipients)
