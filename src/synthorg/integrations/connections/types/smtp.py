"""SMTP connection type."""

from synthorg.integrations.connections.models import ConnectionType
from synthorg.integrations.errors import InvalidConnectionAuthError
from synthorg.observability import get_logger
from synthorg.observability.events.integrations import (
    CONNECTION_VALIDATION_FAILED,
)

logger = get_logger(__name__)


class SmtpAuthenticator:
    """Validates SMTP connection credentials.

    Required fields: ``host``.
    Optional fields: ``port``, ``username``, ``password``,
    ``use_tls``, ``from_addr``.
    """

    @property
    def connection_type(self) -> ConnectionType:
        """The connection type this authenticator handles."""
        return ConnectionType.SMTP

    def validate_credentials(
        self,
        credentials: dict[str, str],
    ) -> None:
        """Validate credential fields.

        Raises:
            InvalidConnectionAuthError: If ``host`` is missing,
                non-string, or blank; ``username``/``password`` is a
                non-string value; or exactly one of
                ``username``/``password`` is supplied without the other.
        """
        host = credentials.get("host")
        if not isinstance(host, str) or not host.strip():
            logger.warning(
                CONNECTION_VALIDATION_FAILED,
                connection_type=ConnectionType.SMTP.value,
                field="host",
                error="missing, non-string, or blank",
            )
            msg = "SMTP connection requires a 'host' field"
            raise InvalidConnectionAuthError(msg)
        # Pull raw values as ``object`` so the runtime isinstance
        # checks below are meaningful to mypy. The ``dict[str, str]``
        # annotation on the protocol describes the *intended* shape;
        # users can still hand us non-strings at runtime and the
        # validator has to reject them instead of trusting the
        # declared type.
        raw_username: object = credentials.get("username", "")
        raw_password: object = credentials.get("password", "")
        # Reject non-string values outright instead of silently
        # normalizing them to "" below -- that could let a non-string
        # username pair with a real-string password (or vice versa)
        # slip past the "both or neither" check.
        if ("username" in credentials and not isinstance(raw_username, str)) or (
            "password" in credentials and not isinstance(raw_password, str)
        ):
            logger.warning(
                CONNECTION_VALIDATION_FAILED,
                connection_type=ConnectionType.SMTP.value,
                field="username/password",
                error="non-string value",
            )
            msg = "SMTP 'username' and 'password' must be strings when provided"
            raise InvalidConnectionAuthError(msg)
        # Normalize whitespace so pure-whitespace values do not look
        # valid to ``bool()``.
        username_s = raw_username.strip() if isinstance(raw_username, str) else ""
        password_s = raw_password.strip() if isinstance(raw_password, str) else ""
        if bool(username_s) != bool(password_s):
            logger.warning(
                CONNECTION_VALIDATION_FAILED,
                connection_type=ConnectionType.SMTP.value,
                field="username/password",
                error="must provide both or neither",
            )
            msg = "SMTP connection requires both 'username' and 'password', or neither"
            raise InvalidConnectionAuthError(msg)

    def required_fields(self) -> tuple[str, ...]:
        """Return required credential field names."""
        return ("host",)
