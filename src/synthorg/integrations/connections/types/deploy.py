"""Deploy-target connection type.

A deploy connection brokers the API token for one hosting platform and one
environment. Validation here covers the credential only; the platform and
environment ride in the connection ``metadata`` and are resolved by
:mod:`synthorg.integrations.connections.deploy_target`.
"""

from synthorg.integrations.connections.models import ConnectionType
from synthorg.integrations.errors import InvalidConnectionAuthError
from synthorg.observability import get_logger
from synthorg.observability.events.integrations import (
    CONNECTION_VALIDATION_FAILED,
)

logger = get_logger(__name__)


class DeployAuthenticator:
    """Validates deploy-target connection credentials.

    Required fields: ``token`` (platform API token).
    """

    @property
    def connection_type(self) -> ConnectionType:
        """The connection type this authenticator handles."""
        return ConnectionType.DEPLOY

    def validate_credentials(self, credentials: dict[str, str]) -> None:
        """Validate credential fields.

        Args:
            credentials: The submitted credential mapping.

        Raises:
            InvalidConnectionAuthError: If ``token`` is missing,
                non-string, or blank.
        """
        token = credentials.get("token")
        if not isinstance(token, str) or not token.strip():
            logger.warning(
                CONNECTION_VALIDATION_FAILED,
                connection_type=self.connection_type.value,
                field="token",
                error="missing, non-string, or blank",
            )
            msg = "Deploy connection requires a 'token' field"
            raise InvalidConnectionAuthError(msg)

    def required_fields(self) -> tuple[str, ...]:
        """Return required credential field names.

        Returns:
            The required credential field names.
        """
        return ("token",)
