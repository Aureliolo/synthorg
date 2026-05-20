"""Shared httpx lifecycle for the per-forge REST clients."""

from typing import Self

import httpx

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
        headers: dict[str, str],
        timeout: float,
    ) -> None:
        self._api_base_url = api_base_url.rstrip("/")
        self._headers = headers
        self._timeout = timeout
        self.__client: httpx.AsyncClient | None = None

    @property
    def _client(self) -> httpx.AsyncClient:
        if self.__client is None:
            self.__client = httpx.AsyncClient(
                base_url=self._api_base_url,
                headers=self._headers,
                timeout=self._timeout,
            )
        return self.__client

    async def _request(
        self,
        method: str,
        url: str,
        *,
        action: str,
        json: dict[str, object] | None = None,
    ) -> httpx.Response:
        """Issue a request, mapping transport errors to a typed error.

        Status-code mapping is the caller's job (via
        ``raise_for_forge_status``); this only guards transport-level
        failures so a connection reset surfaces as a retryable
        ``GitBackendForgeApiError`` rather than a bare ``httpx`` error.
        """
        try:
            return await self._client.request(method, url, json=json)
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
        """Close the underlying httpx client if it was created."""
        if self.__client is not None:
            await self.__client.aclose()
            self.__client = None

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()


__all__ = ["BaseForgeClient"]
