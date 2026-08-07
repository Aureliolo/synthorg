"""Shared httpx lifecycle for the chat-platform Web API clients."""

from collections.abc import Mapping
from typing import Self

import httpx

from synthorg.core.normalization import normalize_base_url
from synthorg.core.tls_trust import httpx_verify, trust_revision
from synthorg.integrations.errors import ChatApiError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.integrations import CHAT_API_REQUEST_FAILED

logger = get_logger(__name__)


class BaseChatClient:
    """Owns a lazily-created authenticated ``httpx.AsyncClient``.

    Subclasses set the bearer auth header and implement the two-way chat
    surface. The client is created on first use so the object stays cheap
    to construct.
    """

    def __init__(
        self,
        *,
        api_base_url: str,
        headers: Mapping[str, str],
        timeout: float,
    ) -> None:
        # Trailing slash is load-bearing: httpx resolves a relative
        # request URL against the base_url path, so without it the
        # ``/api`` prefix would be dropped when joining a method name.
        self._api_base_url = normalize_base_url(api_base_url)
        self._headers: dict[str, str] = dict(headers)
        self._timeout = timeout
        self.__client: httpx.AsyncClient | None = None
        self.__trust_revision = -1

    @property
    def _client(self) -> httpx.AsyncClient:
        # Rebuilt on a trust change, not only when absent: TLS is fixed at
        # construction, so a cached client would keep verifying (or not)
        # the way it did when it was built.
        if self.__client is None or self.__trust_revision != trust_revision():
            self.__trust_revision = trust_revision()
            self.__client = httpx.AsyncClient(
                base_url=self._api_base_url,
                headers=self._headers,
                timeout=self._timeout,
                verify=httpx_verify(),
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

        Status-code and API-envelope mapping is the caller's job; this
        only guards transport-level failures.

        Args:
            method: HTTP verb.
            url: Endpoint path relative to the ``/api`` base.
            action: Human-readable action for the error message.
            json: Optional JSON request body.
            params: Optional query-string parameters.

        Returns:
            The raw :class:`httpx.Response`.

        Raises:
            ChatApiError: When the transport raises.
        """
        try:
            return await self._client.request(
                method, url.lstrip("/"), json=json, params=params
            )
        except httpx.HTTPError as exc:
            logger.warning(
                CHAT_API_REQUEST_FAILED,
                action=action,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = f"chat API transport error while attempting to {action}"
            raise ChatApiError(msg) from exc

    async def aclose(self) -> None:
        """Close the underlying httpx client if it was created."""
        if self.__client is not None:
            await self.__client.aclose()
            self.__client = None

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()


__all__ = ["BaseChatClient"]
