"""Slack connection type."""

from synthorg.integrations.connections.models import ConnectionType
from synthorg.integrations.errors import InvalidConnectionAuthError
from synthorg.observability import get_logger
from synthorg.observability.events.integrations import (
    CONNECTION_VALIDATION_FAILED,
)

logger = get_logger(__name__)


class SlackAuthenticator:
    """Validates Slack connection credentials.

    Required fields: ``token``.
    Optional fields: ``signing_secret`` (for webhook verification),
    ``team_id``.
    """

    @property
    def connection_type(self) -> ConnectionType:
        """The connection type this authenticator handles."""
        return ConnectionType.SLACK

    def validate_credentials(
        self,
        credentials: dict[str, str],
    ) -> None:
        """Validate credential fields.

        Raises:
            InvalidConnectionAuthError: If ``token`` is absent or blank.
        """
        if "token" not in credentials or not credentials["token"].strip():
            logger.warning(
                CONNECTION_VALIDATION_FAILED,
                connection_type=str(ConnectionType.SLACK),
                field="token",
                error="missing or blank",
            )
            msg = "Slack connection requires a 'token' field"
            raise InvalidConnectionAuthError(msg)

    def required_fields(self) -> tuple[str, ...]:
        """Return required credential field names."""
        return ("token",)
