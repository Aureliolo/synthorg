"""Generic HTTP connection type."""

from synthorg.integrations.connections.models import ConnectionType
from synthorg.integrations.errors import InvalidConnectionAuthError
from synthorg.observability import get_logger
from synthorg.observability.events.integrations import (
    CONNECTION_VALIDATION_FAILED,
)

logger = get_logger(__name__)


class GenericHttpAuthenticator:
    """Validates generic HTTP connection credentials.

    Required fields: ``base_url``.
    Optional fields: ``token``, ``api_key``, ``username``,
    ``password``, ``header_name``, ``header_value``.
    """

    @property
    def connection_type(self) -> ConnectionType:
        """The connection type this authenticator handles."""
        return ConnectionType.GENERIC_HTTP

    def validate_credentials(
        self,
        credentials: dict[str, str],
    ) -> None:
        """Validate credential fields.

        Raises:
            InvalidConnectionAuthError: If ``base_url`` is missing,
                non-string, or blank.
        """
        base_url = credentials.get("base_url")
        if not isinstance(base_url, str) or not base_url.strip():
            logger.warning(
                CONNECTION_VALIDATION_FAILED,
                connection_type=ConnectionType.GENERIC_HTTP.value,
                field="base_url",
                error="missing, non-string, or blank",
            )
            msg = "Generic HTTP connection requires a 'base_url' field"
            raise InvalidConnectionAuthError(msg)

    def required_fields(self) -> tuple[str, ...]:
        """Return required credential field names."""
        return ("base_url",)
