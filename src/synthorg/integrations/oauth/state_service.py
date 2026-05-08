"""OAuthStateService -- audit-aware facade over the OAuth state repo.

The :class:`OAuthController` routes the ``initiate_flow`` durable
save through this service rather than calling
``persistence.oauth_states.save(...)`` directly.  Centralising the
save here pairs the durable write with the audit-grade
:data:`SECURITY_OAUTH_STATE_PERSISTED` event so audit logging cannot
silently regress when a new field or write path is added; every other
persistence-layer mutation in the controller stack already follows the
same shape.

This service is the minimum surface required to cover that one write.
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
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.security import (
    SECURITY_OAUTH_STATE_PERSIST_FAILED,
    SECURITY_OAUTH_STATE_PERSISTED,
)

if TYPE_CHECKING:
    from synthorg.persistence.connection_protocol import OAuthStateRepository

logger = get_logger(__name__)

# Audit-safe correlation prefix length: long enough to deduplicate state
# tokens across the audit chain without exposing the full secret.
_STATE_TOKEN_PREFIX_LENGTH = 8  # lint-allow: magic-numbers -- audit prefix width


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
        try:
            await self._repo.save(bound)
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            logger.warning(
                SECURITY_OAUTH_STATE_PERSIST_FAILED,
                connection_name=str(connection_name),
                state_token_prefix=str(bound.state_token)[:_STATE_TOKEN_PREFIX_LENGTH],
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise
        logger.info(
            SECURITY_OAUTH_STATE_PERSISTED,
            connection_name=str(connection_name),
            state_token_prefix=str(bound.state_token)[:_STATE_TOKEN_PREFIX_LENGTH],
        )
        return bound


__all__ = ["OAuthStateService"]
