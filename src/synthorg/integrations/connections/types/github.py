"""GitHub connection type."""

from synthorg.integrations.connections.models import ConnectionType
from synthorg.integrations.errors import InvalidConnectionAuthError
from synthorg.observability import get_logger
from synthorg.observability.events.integrations import (
    CONNECTION_VALIDATION_FAILED,
)

logger = get_logger(__name__)


class GitHubAuthenticator:
    """Validates GitHub connection credentials.

    Required fields: ``token``.
    Optional fields: ``api_url`` (defaults to https://api.github.com).
    """

    @property
    def connection_type(self) -> ConnectionType:
        """The connection type this authenticator handles."""
        return ConnectionType.GITHUB

    def validate_credentials(
        self,
        credentials: dict[str, str],
    ) -> None:
        """Validate credential fields.

        Raises:
            InvalidConnectionAuthError: If ``token`` is missing,
                non-string, or blank.
        """
        token = credentials.get("token")
        if not isinstance(token, str) or not token.strip():
            logger.warning(
                CONNECTION_VALIDATION_FAILED,
                connection_type=ConnectionType.GITHUB.value,
                field="token",
                error="missing, non-string, or blank",
            )
            msg = "GitHub connection requires a 'token' field"
            raise InvalidConnectionAuthError(msg)

    def required_fields(self) -> tuple[str, ...]:
        """Return required credential field names."""
        return ("token",)
