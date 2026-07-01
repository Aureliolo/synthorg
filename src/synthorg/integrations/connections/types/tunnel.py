"""Tunnel-credential connection type.

Backs a tunnel provider's auth token, minted by the tunnel manager
when the operator pastes a token on the dashboard tunnel card. No
``base_url``: the tunnel target is the local API itself.
"""

from synthorg.integrations.connections.models import ConnectionType
from synthorg.integrations.errors import InvalidConnectionAuthError
from synthorg.observability import get_logger
from synthorg.observability.events.integrations import (
    CONNECTION_VALIDATION_FAILED,
)

logger = get_logger(__name__)


class TunnelAuthenticator:
    """Validates tunnel-credential connections.

    Required fields: ``auth_token``.
    """

    @property
    def connection_type(self) -> ConnectionType:
        """The connection type this authenticator handles."""
        return ConnectionType.TUNNEL

    def validate_credentials(
        self,
        credentials: dict[str, str],
    ) -> None:
        """Validate credential fields.

        Raises:
            InvalidConnectionAuthError: If ``auth_token`` is missing,
                non-string, or blank.
        """
        auth_token = credentials.get("auth_token")
        if not isinstance(auth_token, str) or not auth_token.strip():
            logger.warning(
                CONNECTION_VALIDATION_FAILED,
                connection_type=ConnectionType.TUNNEL.value,
                field="auth_token",
                error="missing, non-string, or blank",
            )
            msg = "Tunnel connection requires an 'auth_token' field"
            raise InvalidConnectionAuthError(msg)

    def required_fields(self) -> tuple[str, ...]:
        """Return required credential field names."""
        return ("auth_token",)
