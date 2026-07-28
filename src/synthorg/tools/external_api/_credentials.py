"""Map brokered connection credentials to request auth headers.

Runs in-process inside the tool; the returned headers carry secrets and MUST
NOT be logged. Credential field names follow the generic-HTTP connection
convention (``token``, ``api_key``, ``username``, ``password``,
``header_name``, ``header_value``).
"""

import base64

from synthorg.integrations.connections.http_vendor import resolve_vendor
from synthorg.integrations.connections.models import AuthMethod, Connection
from synthorg.observability import get_logger
from synthorg.observability.events.external_api import EXTERNAL_API_CREDENTIAL_UNUSABLE
from synthorg.tools.external_api.errors import ExternalApiCredentialError

logger = get_logger(__name__)


def _api_key_of(credentials: dict[str, str]) -> str | None:
    """Return the key credential under any of its accepted field names.

    Returns:
        The first non-empty key field, or ``None`` when none is stored.
    """
    return (
        credentials.get("api_key")
        or credentials.get("token")
        or credentials.get("access_token")
    )


def _log_unusable(auth_method: AuthMethod, msg: str, **context: str) -> None:
    """Report a credential that cannot produce an auth header.

    The message names the missing field, never its value, so it is safe to
    log verbatim.
    """
    logger.warning(
        EXTERNAL_API_CREDENTIAL_UNUSABLE,
        auth_method=auth_method.value,
        reason=msg,
        **context,
    )


def build_auth_headers(
    auth_method: AuthMethod,
    credentials: dict[str, str],
) -> dict[str, str]:
    """Return the auth headers for *auth_method* from *credentials*.

    Args:
        auth_method: The connection's configured authentication method.
        credentials: Decrypted credential fields from the secret backend.

    Returns:
        Header name/value pairs to merge into the request.

    Raises:
        ExternalApiCredentialError: If the method's required credential
            field is absent (a misconfigured connection).
    """
    match auth_method:
        case AuthMethod.BEARER_TOKEN | AuthMethod.OAUTH2:
            token = credentials.get("token") or credentials.get("access_token")
            if not token:
                msg = f"{auth_method.value} connection has no 'token'"
                _log_unusable(auth_method, msg)
                raise ExternalApiCredentialError(msg)
            return {"Authorization": f"Bearer {token}"}
        case AuthMethod.API_KEY:
            header_name = credentials.get("header_name")
            header_value = credentials.get("header_value")
            if header_name and header_value:
                return {header_name: header_value}
            # The generic-HTTP form stores the secret as ``token``; accepting
            # only ``api_key`` here would reject every connection the
            # dashboard produces while its health probe reported healthy.
            api_key = _api_key_of(credentials)
            if not api_key:
                msg = "api_key connection has no key credential or header pair"
                _log_unusable(auth_method, msg)
                raise ExternalApiCredentialError(msg)
            return {"X-API-Key": api_key}
        case AuthMethod.BASIC_AUTH:
            username = credentials.get("username")
            password = credentials.get("password")
            if not username or not password:
                msg = "basic_auth connection requires 'username' and 'password'"
                _log_unusable(auth_method, msg)
                raise ExternalApiCredentialError(msg)
            token = base64.b64encode(f"{username}:{password}".encode()).decode("ascii")
            return {"Authorization": f"Basic {token}"}
        case AuthMethod.CUSTOM:
            header_name = credentials.get("header_name")
            header_value = credentials.get("header_value")
            if header_name and header_value:
                return {header_name: header_value}
            return {}


def build_connection_auth_headers(
    connection: Connection,
    credentials: dict[str, str],
) -> dict[str, str]:
    """Return the auth headers for *connection*, honouring its vendor preset.

    A vendor names the header its API actually accepts, which is rarely the
    generic ``X-API-Key`` guess: sending the wrong one reads as an invalid
    credential, so a correctly-configured connection would report unhealthy.
    An explicit ``header_name``/``header_value`` pair still wins, since an
    operator who spelled the header out means it.

    Args:
        connection: The connection whose metadata may name a vendor preset.
        credentials: Decrypted credential fields.

    Returns:
        Header name/value pairs to merge into the request.

    Raises:
        ExternalApiCredentialError: If no usable credential is present.
    """
    header_name = credentials.get("header_name")
    header_value = credentials.get("header_value")
    if header_name and header_value:
        # Returned verbatim rather than routed through the auth method:
        # every method other than CUSTOM builds its own scheme and ignores
        # the pair, so deferring here would drop the override the operator
        # spelled out (and, for a method whose own field is absent, raise).
        return {header_name: header_value}
    preset = resolve_vendor(connection.metadata)
    if preset is None:
        return build_auth_headers(connection.auth_method, credentials)
    key = _api_key_of(credentials)
    if not key:
        msg = f"{preset.label} connection has no api_key/token credential"
        _log_unusable(connection.auth_method, msg, vendor=preset.id)
        raise ExternalApiCredentialError(msg)
    return preset.auth_headers(key)
