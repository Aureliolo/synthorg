"""OAuthStateService -- audit-aware facade over the OAuth state repo.

The :class:`OAuthController` previously called
``persistence.oauth_states.save(...)`` directly from the
``initiate_flow`` handler.  Audit ``68-state-mutation-leaks`` flagged
that direct write because every other persistence-layer mutation in
the controller stack routes through a service so audit logging cannot
silently regress when a new field or write path is added.

This service is the minimum surface required to centralise the one
flagged write and the audit-grade
:data:`SECURITY_OAUTH_STATE_PERSISTED` event that accompanies it.
The OAuth callback path (which deletes the consumed state token)
already routes through ``handle_oauth_callback`` in
:mod:`synthorg.integrations.oauth.callback_handler`, so it is out of
this service's scope by design.
"""

from typing import TYPE_CHECKING

from synthorg.core.types import NotBlankStr  # noqa: TC001 -- runtime annotation
from synthorg.integrations.connections.models import (
    OAuthState,  # noqa: TC001 -- runtime annotation
)
from synthorg.observability import get_logger
from synthorg.observability.events.security import SECURITY_OAUTH_STATE_PERSISTED

if TYPE_CHECKING:
    from synthorg.persistence.connection_protocol import OAuthStateRepository

logger = get_logger(__name__)


class OAuthStateService:
    """Audit-aware facade over the OAuth state repository.

    Wraps the single ``save(...)`` call the OAuth-initiate handler
    needs.  Each call emits one structured INFO event so the durable
    write is observable in the audit chain (the
    ``security.oauth.*`` prefix is the
    :class:`AuditChainSink`'s allowlist filter).

    Args:
        repo: Repository handling :class:`OAuthState` rows.
    """

    __slots__ = ("_repo",)

    def __init__(self, *, repo: OAuthStateRepository) -> None:
        self._repo = repo

    async def persist_initiation(
        self,
        state: OAuthState,
        *,
        connection_name: NotBlankStr,
    ) -> OAuthState:
        """Bind ``state`` to ``connection_name``, persist, and audit.

        Returns the bound state (with ``connection_name`` set) so the
        caller can return its ``state_token`` to the client without
        re-fetching.
        """
        bound = state.model_copy(update={"connection_name": connection_name})
        await self._repo.save(bound)
        logger.info(
            SECURITY_OAUTH_STATE_PERSISTED,
            connection_name=str(connection_name),
            state_token_prefix=str(bound.state_token)[:8],
        )
        return bound


__all__ = ["OAuthStateService"]
