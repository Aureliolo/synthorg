"""Shared httpx lifecycle + OCI auth flow for the registry API clients.

The egress pin holds because the client is constructed with a fixed
``base_url`` and every call site passes a *code-defined* relative path plus a
validated reference (no scheme, no ``/``). Redirects are never followed.

The one place a request can leave the pinned host is the OCI bearer-token
exchange: a registry answers an unauthenticated call with a
``WWW-Authenticate`` challenge naming a ``realm`` to fetch a token from, and
the client sends the brokered credential there. A compromised registry could
name an attacker host, so the realm is validated against the registry's own
host plus an optional operator-declared ``auth_host``, HTTPS only, before any
credential leaves the process.
"""

import base64
from collections.abc import Mapping
from typing import Final, Self
from urllib.parse import urlsplit

import httpx

from synthorg.core.normalization import (
    normalize_base_url,
    normalize_identifier,
    reject_unsafe_url_segment,
)
from synthorg.core.types import NotBlankStr
from synthorg.integrations.errors import (
    RegistryApiAuthError,
    RegistryApiClientError,
    RegistryApiError,
)
from synthorg.integrations.registry_api._http import (
    parse_bearer_challenge,
    raise_for_registry_status,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.integrations import (
    REGISTRY_API_AUTH_CHALLENGE_FAILED,
    REGISTRY_API_REQUEST_FAILED,
)

logger = get_logger(__name__)

_UNAUTHORIZED: int = 401
_HTTPS_SCHEME: str = "https"
_HTTPS_PORT: Final[int] = 443


class BaseRegistryClient:
    """Owns a lazily-created ``httpx.AsyncClient`` and the OCI auth flow.

    Subclasses implement the registry surface on top of ``_request``, which
    performs one authentication retry (bearer-token exchange, else Basic) on
    a 401 and caches the bearer for subsequent calls.
    """

    def __init__(  # noqa: PLR0913 -- connection facts threaded into one client
        self,
        *,
        api_base_url: str,
        repository: NotBlankStr,
        username: str,
        token: str,
        timeout: float,
        auth_host: str = "",
    ) -> None:
        self._api_base_url = normalize_base_url(api_base_url)
        self._repository = repository
        self._username = username
        self._token = token
        self._timeout = timeout
        base_parts = urlsplit(self._api_base_url)
        self._base_host = normalize_identifier(base_parts.hostname or "")
        # Pin the port too, not just the host: a compromised registry could
        # otherwise redirect a credential or a blob to another service on the
        # same host by naming a different port.
        self._base_port = base_parts.port or _HTTPS_PORT
        self._auth_host = normalize_identifier(auth_host)
        self._bearer: str | None = None
        self.__client: httpx.AsyncClient | None = None

    @property
    def repository(self) -> NotBlankStr:
        """The operator-configured repository this client is bound to."""
        return self._repository

    @property
    def _client(self) -> httpx.AsyncClient:
        if self.__client is None:
            self.__client = httpx.AsyncClient(
                base_url=self._api_base_url,
                timeout=self._timeout,
                # The pin only covers the first hop: a 3xx to another host
                # would carry the credential off the pinned origin, so
                # redirects are never followed.
                follow_redirects=False,
            )
        return self.__client

    def _basic_header(self) -> str:
        """Build the ``Authorization: Basic`` header for the credential.

        Returns:
            The Basic-auth header value over the connection username + token.
        """
        raw = f"{self._username}:{self._token}".encode()
        return f"Basic {base64.b64encode(raw).decode('ascii')}"

    @staticmethod
    def _safe_segment(value: str, *, field: str) -> str:
        """Validate a value before it becomes part of a request path.

        Args:
            value: The candidate path segment (a tag or a digest).
            field: The field name, for the error message.

        Returns:
            The validated value, unchanged.

        Raises:
            RegistryApiClientError: When the value could rewrite the path.
        """
        try:
            return reject_unsafe_url_segment(value, field=field)
        except ValueError as exc:
            logger.warning(
                REGISTRY_API_REQUEST_FAILED,
                action="build a request path",
                error_type=type(exc).__name__,
                detail=f"unsafe {field}",
            )
            msg = f"registry {field} is not a usable path segment"
            raise RegistryApiClientError(msg) from exc

    async def _send(  # noqa: PLR0913 -- one HTTP request surface, threaded whole
        self,
        method: str,
        url: str,
        *,
        action: str,
        headers: Mapping[str, str],
        content: bytes | None,
        params: Mapping[str, str | int] | None,
    ) -> httpx.Response:
        """Issue one request, mapping transport errors to a typed error.

        Returns:
            The raw :class:`httpx.Response`.

        Raises:
            RegistryApiError: When the transport raises.
        """
        try:
            return await self._client.request(
                method,
                url.lstrip("/") if not url.startswith(_HTTPS_SCHEME) else url,
                headers=dict(headers),
                content=content,
                params=params,
            )
        except httpx.HTTPError as exc:
            logger.warning(
                REGISTRY_API_REQUEST_FAILED,
                action=action,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = f"registry transport error while attempting to {action}"
            raise RegistryApiError(msg) from exc

    async def _request(  # noqa: PLR0913 -- one HTTP request surface, threaded whole
        self,
        method: str,
        url: str,
        *,
        action: str,
        headers: Mapping[str, str] | None = None,
        content: bytes | None = None,
        params: Mapping[str, str | int] | None = None,
    ) -> httpx.Response:
        """Issue a request, authenticating with one retry on a 401.

        The cached bearer (if any) is attached up front; a 401 that carries a
        bearer challenge triggers one token exchange and retry, otherwise a
        single Basic-auth retry is attempted.

        Returns:
            The authenticated response (which may still be a non-2xx the
            caller maps via :func:`raise_for_registry_status`).
        """
        base_headers = dict(headers or {})
        first_headers = dict(base_headers)
        if self._bearer is not None:
            first_headers["Authorization"] = f"Bearer {self._bearer}"
        resp = await self._send(
            method,
            url,
            action=action,
            headers=first_headers,
            content=content,
            params=params,
        )
        if resp.status_code != _UNAUTHORIZED:
            return resp
        retry_headers = dict(base_headers)
        challenge = parse_bearer_challenge(resp.headers)
        if challenge is not None:
            self._bearer = await self._exchange_token(challenge, action=action)
            retry_headers["Authorization"] = f"Bearer {self._bearer}"
        else:
            retry_headers["Authorization"] = self._basic_header()
        return await self._send(
            method,
            url,
            action=action,
            headers=retry_headers,
            content=content,
            params=params,
        )

    async def _exchange_token(self, challenge: dict[str, str], *, action: str) -> str:
        """Exchange the Basic credential for a bearer token at the realm.

        Args:
            challenge: The parsed ``WWW-Authenticate: Bearer`` parameters.
            action: The originating action, for the error message.

        Returns:
            The bearer token string.

        Raises:
            RegistryApiAuthError: The realm is missing, not HTTPS, on a
                disallowed host, or the response carried no token.
        """
        realm = challenge.get("realm", "")
        self._validate_realm(realm)
        params: dict[str, str] = {}
        if service := challenge.get("service"):
            params["service"] = service
        if scope := challenge.get("scope"):
            params["scope"] = scope
        resp = await self._send(
            "GET",
            realm,
            action="exchange a registry token",
            headers={"Authorization": self._basic_header()},
            content=None,
            params=params,
        )
        raise_for_registry_status(resp, action="exchange a registry token")
        payload = self._json_or_raise(resp, action="exchange a registry token")
        token = None
        if isinstance(payload, dict):
            raw = payload.get("token") or payload.get("access_token")
            token = raw if isinstance(raw, str) and raw else None
        if token is None:
            logger.warning(
                REGISTRY_API_AUTH_CHALLENGE_FAILED,
                action=action,
                detail="token endpoint returned no token",
            )
            msg = "registry token endpoint returned no usable token"
            raise RegistryApiAuthError(msg)
        return token

    def _validate_realm(self, realm: str) -> None:
        """Reject a token-exchange realm off the approved hosts.

        Raises:
            RegistryApiAuthError: The realm is blank, not HTTPS, or on a host
                or port that is neither the registry origin nor the
                operator-declared ``auth_host`` (on the default HTTPS port).
                The credential never leaves the process for an unapproved
                origin.
        """
        parsed = urlsplit(realm)
        host = normalize_identifier(parsed.hostname or "")
        port = parsed.port or _HTTPS_PORT
        on_registry_origin = host == self._base_host and port == self._base_port
        # An operator-declared auth_host is portless, so it is only approved on
        # the default HTTPS port.
        on_auth_host = (
            bool(self._auth_host) and host == self._auth_host and (port == _HTTPS_PORT)
        )
        approved = host and (on_registry_origin or on_auth_host)
        if parsed.scheme != _HTTPS_SCHEME or not approved:
            logger.warning(
                REGISTRY_API_AUTH_CHALLENGE_FAILED,
                action="exchange a registry token",
                detail="token endpoint host not allowed",
            )
            msg = (
                "registry token endpoint is not an approved https host; "
                "set the connection auth_host if the registry authenticates "
                "on a different host"
            )
            raise RegistryApiAuthError(msg)

    def _json_or_raise(self, resp: httpx.Response, *, action: str) -> object:
        """Parse a 2xx body as JSON, mapping a malformed body to a typed error.

        Returns:
            The decoded JSON value.

        Raises:
            RegistryApiError: When the body is not valid JSON.
        """
        try:
            return resp.json()
        except ValueError as exc:
            logger.warning(
                REGISTRY_API_REQUEST_FAILED,
                action=action,
                error_type=type(exc).__name__,
                detail="non-JSON body on a 2xx response",
            )
            msg = f"registry returned a non-JSON body while trying to {action}"
            raise RegistryApiError(msg) from exc

    def _same_host_upload_url(self, location: str) -> str:
        """Validate a blob-upload ``Location`` stays on the pinned host.

        A registry answers ``POST .../blobs/uploads/`` with a ``Location`` to
        ``PUT`` the blob to. It may be absolute; if so it must stay on the
        pinned host, or the blob (and its upload session) would leave the
        approved origin.

        Args:
            location: The ``Location`` header value.

        Returns:
            The upload URL to use (relative locations are returned as-is).

        Raises:
            RegistryApiError: The location is blank, or absolute and not on the
                pinned HTTPS origin (a different host, port, or scheme).
        """
        if not location:
            msg = "registry did not return a blob upload location"
            raise RegistryApiError(msg)
        parsed = urlsplit(location)
        is_absolute = bool(parsed.scheme or parsed.netloc)
        off_origin = (
            parsed.scheme != _HTTPS_SCHEME
            or normalize_identifier(parsed.hostname or "") != self._base_host
            or (parsed.port or _HTTPS_PORT) != self._base_port
        )
        if is_absolute and off_origin:
            logger.warning(
                REGISTRY_API_REQUEST_FAILED,
                action="upload a blob",
                detail="upload location left the pinned origin",
            )
            msg = "registry blob upload location is off the pinned https origin"
            raise RegistryApiError(msg)
        return location

    async def aclose(self) -> None:
        """Close the underlying httpx client if it was created."""
        if self.__client is not None:
            await self.__client.aclose()
            self.__client = None

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()


__all__ = ["BaseRegistryClient"]
