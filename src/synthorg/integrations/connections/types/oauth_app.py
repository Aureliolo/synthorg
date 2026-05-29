"""OAuth app connection type."""

from urllib.parse import urlparse

from synthorg.integrations.connections.models import ConnectionType
from synthorg.integrations.errors import InvalidConnectionAuthError
from synthorg.observability import get_logger
from synthorg.observability.events.integrations import (
    CONNECTION_VALIDATION_FAILED,
)

logger = get_logger(__name__)

_URL_FIELDS = ("auth_url", "token_url")
# Default: HTTPS only. Plain HTTP is permitted for loopback hosts so
# local OAuth mock servers still work in dev without compromising
# real-world deployments where client_secret / authorization_code /
# access_token would otherwise leak over cleartext transport.
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


class OAuthAppAuthenticator:
    """Validates OAuth app registration credentials.

    Required fields: ``client_id``, ``client_secret``,
    ``auth_url``, ``token_url``. Optional fields: ``scopes``.
    URL fields must use HTTPS with a non-empty hostname; plain
    HTTP is only permitted for loopback hosts (``localhost``,
    ``127.0.0.1``, ``::1``) so local OAuth mock servers still
    work in dev without exposing real deployments to cleartext
    client_secret / authorization_code / access_token transport.
    """

    @property
    def connection_type(self) -> ConnectionType:
        """The connection type this authenticator handles."""
        return ConnectionType.OAUTH_APP

    def validate_credentials(
        self,
        credentials: dict[str, str],
    ) -> None:
        """Validate credential fields (presence + URL format).

        Raises:
            InvalidConnectionAuthError: If ``client_id``,
                ``client_secret``, ``auth_url``, or ``token_url`` is
                missing/blank, or ``auth_url``/``token_url`` has no host
                or uses a non-HTTPS scheme on a non-loopback host.
        """
        for field in ("client_id", "client_secret", "auth_url", "token_url"):
            value = credentials.get(field)
            if not isinstance(value, str) or not value.strip():
                logger.warning(
                    CONNECTION_VALIDATION_FAILED,
                    connection_type=str(ConnectionType.OAUTH_APP),
                    field=field,
                    error="missing or blank",
                )
                msg = f"OAuth app connection requires a '{field}' field"
                raise InvalidConnectionAuthError(msg)

        for url_field in _URL_FIELDS:
            parsed = urlparse(credentials[url_field].strip())
            # Check ``parsed.hostname`` rather than ``parsed.netloc`` so
            # malformed URLs like ``https://:443/token`` (which have a
            # netloc but no hostname) are rejected instead of slipping
            # through as "valid".
            hostname = (parsed.hostname or "").lower()
            if not hostname:
                logger.warning(
                    CONNECTION_VALIDATION_FAILED,
                    connection_type=ConnectionType.OAUTH_APP.value,
                    field=url_field,
                    error="URL has no hostname",
                )
                msg = f"OAuth app '{url_field}' must have a valid hostname"
                raise InvalidConnectionAuthError(msg)
            is_loopback_http = parsed.scheme == "http" and hostname in _LOOPBACK_HOSTS
            if parsed.scheme != "https" and not is_loopback_http:
                logger.warning(
                    CONNECTION_VALIDATION_FAILED,
                    connection_type=ConnectionType.OAUTH_APP.value,
                    field=url_field,
                    error="non-HTTPS URL not permitted outside loopback",
                    scheme=parsed.scheme,
                    hostname=hostname,
                )
                msg = (
                    f"OAuth app '{url_field}' must use HTTPS "
                    "(plain http is only allowed for loopback hosts)"
                )
                raise InvalidConnectionAuthError(msg)

    def required_fields(self) -> tuple[str, ...]:
        """Return required credential field names."""
        return ("client_id", "client_secret", "auth_url", "token_url")
