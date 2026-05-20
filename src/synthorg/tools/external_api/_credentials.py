"""Map brokered connection credentials to request auth headers.

Runs in-process inside the tool; the returned headers carry secrets and MUST
NOT be logged (SEC-1). Credential field names follow the generic-HTTP
connection convention (``token``, ``api_key``, ``username``, ``password``,
``header_name``, ``header_value``).
"""

import base64

from synthorg.integrations.connections.models import AuthMethod
from synthorg.tools.external_api.errors import ExternalApiCredentialError


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
                raise ExternalApiCredentialError(msg)
            return {"Authorization": f"Bearer {token}"}
        case AuthMethod.API_KEY:
            header_name = credentials.get("header_name")
            header_value = credentials.get("header_value")
            if header_name and header_value:
                return {header_name: header_value}
            api_key = credentials.get("api_key")
            if not api_key:
                msg = "api_key connection has no 'api_key' or header pair"
                raise ExternalApiCredentialError(msg)
            return {"X-API-Key": api_key}
        case AuthMethod.BASIC_AUTH:
            username = credentials.get("username")
            password = credentials.get("password")
            if not username or not password:
                msg = "basic_auth connection requires 'username' and 'password'"
                raise ExternalApiCredentialError(msg)
            token = base64.b64encode(f"{username}:{password}".encode()).decode("ascii")
            return {"Authorization": f"Basic {token}"}
        case AuthMethod.CUSTOM:
            header_name = credentials.get("header_name")
            header_value = credentials.get("header_value")
            if header_name and header_value:
                return {header_name: header_value}
            return {}
