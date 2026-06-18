"""A2A peer connection type authenticator."""

from synthorg.integrations.connections.models import ConnectionType
from synthorg.integrations.errors import InvalidConnectionAuthError
from synthorg.observability import get_logger
from synthorg.observability.events.integrations import (
    CONNECTION_VALIDATION_FAILED,
)

logger = get_logger(__name__)


_SCHEME_REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "api_key": ("api_key",),
    "bearer": ("access_token",),
    "oauth2": ("client_id", "client_secret"),
    "mtls": ("cert_path", "key_path"),
}

_VALID_SCHEMES = frozenset((*_SCHEME_REQUIRED_FIELDS, "none"))


class A2APeerAuthenticator:
    """Validates A2A peer connection credentials.

    Required fields depend on the configured auth scheme (defaults
    to ``api_key`` when no ``auth_scheme`` key is present).
    Optional fields: ``signing_secret`` (for push notification
    verification).
    """

    @property
    def connection_type(self) -> ConnectionType:
        """The connection type this authenticator handles."""
        return ConnectionType.A2A_PEER

    def validate_credentials(
        self,
        credentials: dict[str, str],
    ) -> None:
        """Validate credentials based on the declared auth scheme.

        Raises:
            InvalidConnectionAuthError: If ``auth_scheme`` is not a valid
                scheme, or a required credential field for the declared
                scheme is missing, non-string, or blank.
        """
        scheme = credentials.get("auth_scheme", "api_key")
        if scheme not in _VALID_SCHEMES:
            msg = (
                f"Unsupported A2A auth scheme '{scheme}'. "
                f"Valid schemes: {', '.join(sorted(_VALID_SCHEMES))}"
            )
            logger.warning(
                CONNECTION_VALIDATION_FAILED,
                scheme=scheme,
                reason="unsupported_auth_scheme",
                error_type=InvalidConnectionAuthError.__name__,
            )
            raise InvalidConnectionAuthError(msg)
        required = _SCHEME_REQUIRED_FIELDS.get(scheme, ())
        for field in required:
            value = credentials.get(field)
            if not isinstance(value, str) or not value.strip():
                logger.warning(
                    CONNECTION_VALIDATION_FAILED,
                    connection_type=ConnectionType.A2A_PEER.value,
                    field=field,
                    auth_scheme=scheme,
                    error="missing, non-string, or blank",
                )
                msg = (
                    f"A2A peer connection (scheme={scheme}) requires a '{field}' field"
                )
                raise InvalidConnectionAuthError(msg)

    def required_fields(self) -> tuple[str, ...]:
        """Return required credential field names.

        Returns the default (api_key) required fields.  Scheme-aware
        validation is performed by ``validate_credentials`` which
        reads the scheme from the credentials dict.
        """
        return ("api_key",)
