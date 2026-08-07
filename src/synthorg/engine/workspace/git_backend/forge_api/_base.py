"""Shared httpx lifecycle for the per-forge REST clients."""

import ssl
from collections.abc import Mapping
from typing import Self

import httpx

from synthorg.core.http_trust_client import TrustFollowingClient
from synthorg.core.normalization import normalize_base_url
from synthorg.engine.errors import GitBackendForgeApiError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.workspace import FORGE_API_REQUEST_FAILED

logger = get_logger(__name__)


class BaseForgeClient:
    """Owns a lazily-created authenticated ``httpx.AsyncClient``.

    Subclasses set the forge-specific auth + accept headers and
    implement the ``repo_exists`` / ``create_repo`` surface. The client
    is created on first use so the object stays cheap to construct and
    deep-copy (the git-backend deps bundle is copied at boot).
    """

    def __init__(
        self,
        *,
        api_base_url: str,
        headers: Mapping[str, str],
        timeout: float,
    ) -> None:
        # Trailing slash is load-bearing: httpx resolves a relative
        # request URL against the base_url's *path*, so without it a
        # forge hosted under a path prefix (``/api/v3``, ``/api/v4``)
        # would lose that prefix when joining the relative endpoint.
        self._api_base_url = normalize_base_url(api_base_url)
        # Copy-on-store so a later mutation of the caller's header map
        # cannot retroactively change this client's auth/headers.
        self._headers: dict[str, str] = dict(headers)
        self._timeout = timeout
        self.__clients = TrustFollowingClient(self.__build_client)

    def __build_client(self, *, verify: ssl.SSLContext | bool) -> httpx.AsyncClient:
        """Build a client against the trust the holder resolved.

        Returns:
            A client for the pinned forge API base.
        """
        return httpx.AsyncClient(
            base_url=self._api_base_url,
            headers=self._headers,
            timeout=self._timeout,
            # The same trust the git half of this backend uses, so a
            # self-hosted forge behind an internal CA is not reachable
            # over one transport and refused over the other.
            verify=verify,
        )

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

        Status-code mapping is the caller's job (via
        ``raise_for_forge_status``); this only guards transport-level
        failures so a connection reset surfaces as a retryable
        ``GitBackendForgeApiError`` rather than a bare ``httpx`` error.

        Args:
            method: HTTP verb.
            url: Endpoint path (a leading slash is stripped so it
                resolves against the base_url path prefix).
            action: Human-readable action for the error message.
            json: Optional JSON request body.
            params: Optional query-string parameters.

        Returns:
            The raw :class:`httpx.Response`; status mapping is the
            caller's responsibility.

        Raises:
            GitBackendForgeApiError: When the transport raises (the
                wrapped error is logged and reclassified as
                retryable).
        """
        try:
            async with self.__clients.borrow() as client:
                # Strip any leading slash so the endpoint resolves *against*
                # the base_url path prefix; a leading slash would make httpx
                # treat it as host-absolute and discard the prefix.
                return await client.request(
                    method, url.lstrip("/"), json=json, params=params
                )
        except httpx.HTTPError as exc:
            logger.warning(
                FORGE_API_REQUEST_FAILED,
                action=action,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = f"forge API transport error while attempting to {action}"
            raise GitBackendForgeApiError(msg) from exc

    async def aclose(self) -> None:
        """Close every client this object built."""
        await self.__clients.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()


__all__ = ["BaseForgeClient"]
