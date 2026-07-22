"""Shared httpx lifecycle for the deploy-platform API clients.

The egress pin is structural here, not a policy check: the client is
constructed with a fixed ``base_url`` and every request path is stripped
of its leading slash before httpx resolves it, so a relative path can
only ever join onto the pinned host. No caller, agent or operator,
supplies an absolute URL.
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
        # Trailing slash is load-bearing: httpx resolves a relative
        # request URL against the base_url path, so without it any path
        # prefix would be dropped when joining an endpoint.
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
