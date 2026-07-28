"""Generic HTTP connection type."""

from typing import Final

from synthorg.integrations.connections.models import ConnectionType
from synthorg.integrations.errors import InvalidConnectionAuthError
from synthorg.observability import get_logger
from synthorg.observability.events.integrations import (
    CONNECTION_VALIDATION_FAILED,
)

logger = get_logger(__name__)


# Any one of these proves the operator supplied auth material; which one
# applies is the auth method's business, not this type's.
_KEY_FIELDS: Final[tuple[str, ...]] = ("token", "api_key", "access_token")


class GenericHttpAuthenticator:
    """Validates generic HTTP connection credentials.

    Required fields: ``base_url``, plus some credential material.
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
                non-string, or blank, or if no credential material was
                supplied at all.
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
        self._require_auth_material(credentials)

    @staticmethod
    def _require_auth_material(credentials: dict[str, str]) -> None:
        """Refuse a connection carrying no way to authenticate.

        A vendor preset now supplies the base URL, so without this the one
        field this type enforced has become optional and a credential-less
        connection is created with no friction at all. It would then read as
        configured everywhere except at the moment of use.

        Raises:
            InvalidConnectionAuthError: If no credential field is present.
        """
        has_key = any(str(credentials.get(f, "")).strip() for f in _KEY_FIELDS)
        has_header = bool(
            str(credentials.get("header_name", "")).strip()
            and str(credentials.get("header_value", "")).strip()
        )
        has_basic = bool(
            str(credentials.get("username", "")).strip()
            and str(credentials.get("password", "")).strip()
        )
        if has_key or has_header or has_basic:
            return
        logger.warning(
            CONNECTION_VALIDATION_FAILED,
            connection_type=ConnectionType.GENERIC_HTTP.value,
            field="credentials",
            error="no credential material supplied",
        )
        msg = (
            "Generic HTTP connection requires credential material: a "
            "token/api_key, a header_name plus header_value, or a "
            "username plus password"
        )
        raise InvalidConnectionAuthError(msg)

    def required_fields(self) -> tuple[str, ...]:
        """Return required credential field names."""
        return ("base_url",)
