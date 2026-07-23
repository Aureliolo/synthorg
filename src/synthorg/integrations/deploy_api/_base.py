"""Shared httpx lifecycle for the deploy-platform API clients.

The egress pin holds because the client is constructed with a fixed
``base_url`` and every call site passes a *code-defined* relative path plus
a validated segment (no scheme, no ``/``). Stripping the leading slash here
defeats a scheme-relative ``//host`` value, and redirects are never
followed; combined with the call-site discipline (constant paths, args
validated by :func:`_reject_unsafe_segment`), a request can only ever reach
the pinned host. The guarantee is that discipline plus this mechanism, not
``lstrip`` alone: a literal absolute URL would bypass ``base_url``, so no
call site is allowed to build one.
"""

from collections.abc import Mapping
from typing import Self

import httpx

from synthorg.core.normalization import normalize_base_url
from synthorg.integrations.errors import DeployApiError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.integrations import DEPLOY_API_REQUEST_FAILED

logger = get_logger(__name__)


class BaseDeployClient:
    """Owns a lazily-created authenticated ``httpx.AsyncClient``.

    Subclasses set the bearer auth header and implement the deploy
    surface. The client is created on first use so the object stays
    cheap to construct.
    """

    def __init__(
        self,
        *,
        api_base_url: str,
        headers: Mapping[str, str],
        timeout: float,
    ) -> None:
        # Normalise to a trailing slash so a self-hosted control plane
        # served under a path prefix keeps that prefix when a relative
        # endpoint is joined. (httpx also normalises base_url this way, so
        # this is belt-and-braces against a future transport swap.)
        self._api_base_url = normalize_base_url(api_base_url)
        self._headers: dict[str, str] = dict(headers)
        self._timeout = timeout
        self.__client: httpx.AsyncClient | None = None

    @property
    def _client(self) -> httpx.AsyncClient:
        if self.__client is None:
            self.__client = httpx.AsyncClient(
                base_url=self._api_base_url,
                headers=self._headers,
                timeout=self._timeout,
                # The pin only covers the first hop: a 3xx to another
                # host would carry the Authorization header off the
                # pinned origin, so redirects are never followed.
                follow_redirects=False,
            )
        return self.__client

    async def _request(
        self,
        method: str,
        url: str,
        *,
        action: str,
        json: dict[str, object] | None = None,
        params: Mapping[str, str | int] | None = None,
    ) -> httpx.Response:
        """Issue a request, mapping transport errors to a typed error.

        Status-code mapping is the caller's job; this only guards
        transport-level failures.

        Args:
            method: HTTP verb.
            url: Endpoint path relative to the pinned API base.
            action: Human-readable action for the error message.
            json: Optional JSON request body.
            params: Optional query-string parameters.

        Returns:
            The raw :class:`httpx.Response`.

        Raises:
            DeployApiError: When the transport raises.
        """
        try:
            return await self._client.request(
                method, url.lstrip("/"), json=json, params=params
            )
        except httpx.HTTPError as exc:
            logger.warning(
                DEPLOY_API_REQUEST_FAILED,
                action=action,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = f"deploy API transport error while attempting to {action}"
            raise DeployApiError(msg) from exc

    def _json_or_raise(self, resp: httpx.Response, *, action: str) -> object:
        """Parse a 2xx body as JSON, mapping a malformed body to a typed error.

        A non-JSON 2xx body (a misbehaving self-hosted control plane, a proxy
        error page returned with a 200) would otherwise raise a raw
        ``ValueError`` that bypasses the whole ``Deploy*`` hierarchy and its
        logging. Route it through the same typed path as every other failure.

        Args:
            resp: The (already status-checked) response.
            action: Human-readable action for the error message.

        Returns:
            The decoded JSON value.

        Raises:
            DeployApiError: When the body is not valid JSON.
        """
        try:
            return resp.json()
        except ValueError as exc:
            logger.warning(
                DEPLOY_API_REQUEST_FAILED,
                action=action,
                error_type=type(exc).__name__,
                detail="non-JSON body on a 2xx response",
            )
            msg = f"deploy platform returned a non-JSON body while trying to {action}"
            raise DeployApiError(msg) from exc

    async def aclose(self) -> None:
        """Close the underlying httpx client if it was created."""
        if self.__client is not None:
            await self.__client.aclose()
            self.__client = None

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()


__all__ = ["BaseDeployClient"]
