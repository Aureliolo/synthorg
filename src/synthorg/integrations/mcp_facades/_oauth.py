# ruff: noqa: PLR0913
# module-kind: service
"""In-process OAuth provider registry facade."""

import asyncio
import copy
from collections.abc import Sequence
from datetime import UTC, datetime

from synthorg.core.types import NotBlankStr
from synthorg.integrations.oauth.token_manager import OAuthTokenManager
from synthorg.observability import get_logger
from synthorg.observability.events.integrations import (
    OAUTH_PROVIDER_CONFIGURED_VIA_MCP,
    OAUTH_PROVIDER_REMOVED_VIA_MCP,
)

logger = get_logger(__name__)


class _OAuthProviderRecord:
    """In-memory record of one registered OAuth provider."""

    __slots__ = (
        "authorize_url",
        "client_id",
        "created_at",
        "name",
        "scopes",
        "token_url",
    )

    def __init__(
        self,
        *,
        name: str,
        client_id: str,
        authorize_url: str,
        token_url: str,
        scopes: tuple[str, ...],
        created_at: datetime,
    ) -> None:
        self.name = name
        self.client_id = client_id
        self.authorize_url = authorize_url
        self.token_url = token_url
        self.scopes = scopes
        self.created_at = created_at

    def to_dict(self) -> dict[str, object]:
        """Serialise the provider record to a JSON-safe dict.

        Returns:
            A dict of the provider's name, client ID, URLs, scopes, and
            ISO-formatted creation timestamp.
        """
        return {
            "name": self.name,
            "client_id": self.client_id,
            "authorize_url": self.authorize_url,
            "token_url": self.token_url,
            "scopes": list(self.scopes),
            "created_at": self.created_at.isoformat(),
        }


class OAuthFacadeService:
    """In-process OAuth provider registry.

    Mutations are serialised through a single :class:`asyncio.Lock` so
    concurrent MCP handler calls cannot race on the in-memory dict
    (check-then-act in :meth:`remove_provider`, unsynchronised writes
    in :meth:`configure_provider`).
    """

    def __init__(
        self,
        *,
        token_manager: OAuthTokenManager | None = None,
    ) -> None:
        self._token_manager = token_manager
        self._providers: dict[str, _OAuthProviderRecord] = {}
        self._lock = asyncio.Lock()

    async def list_providers(self) -> Sequence[_OAuthProviderRecord]:
        """List registered OAuth providers, newest-first.

        Returns:
            A tuple of deep-copied provider records ordered by creation
            time (most recent first).
        """
        async with self._lock:
            snapshot = tuple(copy.deepcopy(p) for p in self._providers.values())
        return tuple(sorted(snapshot, key=lambda p: p.created_at, reverse=True))

    async def configure_provider(
        self,
        *,
        name: NotBlankStr,
        client_id: NotBlankStr,
        authorize_url: NotBlankStr,
        token_url: NotBlankStr,
        scopes: Sequence[str],
        actor_id: NotBlankStr,
    ) -> _OAuthProviderRecord:
        """Register or overwrite an OAuth provider, auditing the event.

        Returns:
            A deep copy of the stored ``_OAuthProviderRecord``.
        """
        record = _OAuthProviderRecord(
            name=name,
            client_id=client_id,
            authorize_url=authorize_url,
            token_url=token_url,
            scopes=tuple(scopes),
            created_at=datetime.now(UTC),
        )
        async with self._lock:
            self._providers[record.name] = record
        logger.info(
            OAUTH_PROVIDER_CONFIGURED_VIA_MCP,
            provider_name=name,
            actor_id=actor_id,
        )
        return copy.deepcopy(record)

    async def remove_provider(
        self,
        *,
        name: NotBlankStr,
        actor_id: NotBlankStr,
        reason: NotBlankStr,
    ) -> bool:
        """Remove an OAuth provider, auditing on a successful removal.

        Returns:
            ``True`` when a provider was removed; ``False`` on a miss.
        """
        async with self._lock:
            removed = self._providers.pop(name, None) is not None
        if removed:
            logger.info(
                OAUTH_PROVIDER_REMOVED_VIA_MCP,
                provider_name=name,
                actor_id=actor_id,
                reason=reason,
                removed=removed,
            )
        return removed
