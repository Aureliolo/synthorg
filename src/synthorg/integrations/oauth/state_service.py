"""OAuthStateService -- audit-aware facade over the OAuth state repo.

The :class:`OAuthController` routes the ``initiate_flow`` durable
save through this service rather than calling
``persistence.oauth_states.save(...)`` directly.  Centralising the
save here pairs the durable write with the audit-grade
:data:`SECURITY_OAUTH_STATE_PERSISTED` event so audit logging cannot
silently regress when a new field or write path is added; every other
persistence-layer mutation in the controller stack already follows the
same shape.

The OAuth callback path routes its state reads and writes
(``get`` / ``expire`` / ``mark_consumed``) through this same service
so both halves of the flow respect the persistence-layer boundary;
``handle_oauth_callback`` receives an :class:`OAuthStateService`, not
a bare repository.
"""

from datetime import datetime
from typing import TYPE_CHECKING, Final

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.integrations.connections.models import (
    OAuthState,
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
_STATE_TOKEN_PREFIX_LENGTH: Final[int] = 8


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

        Returns:
            The bound ``OAuthState`` (with ``connection_name`` set) after
            it has been persisted.
        """
        bound = state.model_copy(update={"connection_name": connection_name})
        try:
            await self._repo.save(bound)
        except Exception as exc:
            reraise_critical(exc)
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

    async def get(self, state_token: NotBlankStr) -> OAuthState | None:
        """Fetch the OAuth state for *state_token* on the callback path.

        Returns ``None`` when no row matches (invalid/unknown token).

        Returns:
            The ``OAuthState`` for the token, or ``None`` when no row
            matches (invalid or unknown token).
        """
        return await self._repo.get(state_token)

    async def expire(self, state_token: NotBlankStr) -> bool:
        """Delete an expired/invalid state row; ``True`` if one was removed.

        The callback handler logs the ``OAUTH_STATE_INVALID`` event for
        the expiry decision; this method is the persistence-boundary
        delegate so the handler never touches the repo directly.

        Returns:
            ``True`` when the state row was found and deleted; ``False``
            when no row matched the token.
        """
        return await self._repo.delete(state_token)

    async def mark_consumed(
        self,
        state_token: NotBlankStr,
        *,
        connection_name: NotBlankStr,
        consumed_at: datetime,
    ) -> bool:
        """Compare-and-set the consumed marker (single-use callback).

        Returns ``True`` for the winning write, ``False`` when a
        concurrent callback already stamped the row (the handler
        routes that case through its replay branch).

        Returns:
            ``True`` when this call won the compare-and-set and stamped
            the consumed marker; ``False`` when a concurrent callback
            already stamped the row.
        """
        return await self._repo.mark_consumed(
            state_token,
            connection_name=connection_name,
            consumed_at=consumed_at,
        )


__all__ = ["OAuthStateService"]
