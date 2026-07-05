"""Tunnel-credential connection type.

Backs a tunnel provider's auth token, minted by the tunnel manager
when the operator pastes a token on the dashboard tunnel card. No
``base_url``: the tunnel target is the local API itself.

A device-login provider (e.g. Dev Tunnels) stores no token: it seeds a
no-secret ``tunnel-<provider>`` row so it still appears in the Connections
list, health-checked through the tunnel status lookup. An empty credential
set is therefore a valid tunnel connection; a supplied ``auth_token`` is
still validated strictly.
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

    A token-backed provider supplies ``auth_token``; a device-login
    provider (e.g. Dev Tunnels) supplies no credentials. No field is
    universally required, but a supplied ``auth_token`` is validated
    strictly.
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

        An empty credential set is the no-secret device-login connection
        and is accepted. When ``auth_token`` is supplied it must be a
        non-blank string.

        Raises:
            InvalidConnectionAuthError: If ``auth_token`` is present but
                non-string or blank.
        """
        if not credentials:
            return
        auth_token = credentials.get("auth_token")
        if not isinstance(auth_token, str) or not auth_token.strip():
            logger.warning(
                CONNECTION_VALIDATION_FAILED,
                connection_type=ConnectionType.TUNNEL.value,
                field="auth_token",
                error="non-string or blank",
            )
            msg = "Tunnel connection 'auth_token' must be a non-blank string"
            raise InvalidConnectionAuthError(msg)

    def required_fields(self) -> tuple[str, ...]:
        """Return required credential field names.

        Empty: a token-backed tunnel supplies ``auth_token`` and a
        device-login tunnel supplies nothing, so no field is universally
        required.
        """
        return ()
