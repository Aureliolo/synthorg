"""Generic HTTP connection type."""

from typing import Final, NoReturn

from synthorg.integrations.connections.models import AuthMethod, ConnectionType
from synthorg.integrations.connections.protocol import AUTH_METHOD_VIEW_KEY
from synthorg.integrations.errors import InvalidConnectionAuthError
from synthorg.observability import get_logger
from synthorg.observability.events.integrations import (
    CONNECTION_VALIDATION_FAILED,
)

logger = get_logger(__name__)


# Any one of these carries a key the request signs with; which one a given
# auth method reads is that method's business.
_KEY_FIELDS: Final[tuple[str, ...]] = ("token", "api_key", "access_token")

#: The material each declared method actually consumes at request time.
#: A connection whose credentials satisfy some *other* method still fails
#: every call it makes, so accepting it only moves the discovery from
#: create-time to first use.
_SHAPE_FOR_METHOD: Final[dict[AuthMethod, str]] = {
    AuthMethod.API_KEY: "key",
    AuthMethod.BEARER_TOKEN: "key",
    AuthMethod.OAUTH2: "key",
    AuthMethod.BASIC_AUTH: "basic",
}

_SHAPE_REMEDY: Final[dict[str, str]] = {
    "key": "a token, api_key, or access_token",
    "basic": "a username plus password",
}

_ANY_SHAPE_REMEDY: Final[str] = (
    "a token/api_key, a header_name plus header_value, or a username plus password"
)


def _shape_for(declared: str) -> str | None:
    """Return the material shape a declared auth method consumes.

    Returns:
        The shape name, or ``None`` when the method is absent, custom, or
        one this build does not recognise -- in which case any material
        counts, since nothing here can say which is right.
    """
    try:
        method = AuthMethod(declared)
    except ValueError:
        return None
    return _SHAPE_FOR_METHOD.get(method)


def _reject(reason: str, qualifier: str, remedy: str) -> NoReturn:
    """Report and raise a credential-shape rejection.

    Raises:
        InvalidConnectionAuthError: Always; the caller has already decided
            the credentials are unusable.
    """
    logger.warning(
        CONNECTION_VALIDATION_FAILED,
        connection_type=ConnectionType.GENERIC_HTTP.value,
        field="credentials",
        error=reason,
    )
    msg = f"Generic HTTP connection requires credential material{qualifier}: {remedy}"
    raise InvalidConnectionAuthError(msg)


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
        """Refuse a connection that cannot authenticate as it says it will.

        A vendor preset now supplies the base URL, so without this the one
        field this type enforced has become optional and a credential-less
        connection is created with no friction at all. It would then read as
        configured everywhere except at the moment of use.

        Where the declared auth method is known, the material is held to
        that method's shape rather than to "some shape": a basic-auth
        connection carrying only a bearer token is as unusable as one
        carrying nothing, and equally silent until the first call.

        Raises:
            InvalidConnectionAuthError: If no credential field is present,
                or none matching the declared auth method.
        """
        shapes = {
            "key": any(str(credentials.get(f, "")).strip() for f in _KEY_FIELDS),
            "basic": bool(
                str(credentials.get("username", "")).strip()
                and str(credentials.get("password", "")).strip()
            ),
        }
        # A custom header is the escape hatch for a service none of the
        # standard methods describes, so it satisfies any declared method.
        if (
            str(credentials.get("header_name", "")).strip()
            and str(credentials.get("header_value", "")).strip()
        ):
            return
        required = _shape_for(credentials.get(AUTH_METHOD_VIEW_KEY, ""))
        if required is None:
            if any(shapes.values()):
                return
            _reject("no credential material supplied", "", _ANY_SHAPE_REMEDY)
        if shapes[required]:
            return
        _reject(
            "credential material does not match the declared auth method",
            " for the declared auth method",
            _SHAPE_REMEDY[required],
        )

    def required_fields(self) -> tuple[str, ...]:
        """Return required credential field names."""
        return ("base_url",)
