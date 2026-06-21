"""Active-principle removal mutator for the rollback executor.

Deletes an active principle that a prompt-tuning apply created: the
inverse of an ADD (distinct from :class:`PrincipleOverridePromptMutator`,
which overlays restored TEXT onto an existing pack principle). Backed by
:class:`ActivePrincipleRepository` keyed by the principle id. An optional
``on_principle_removed`` hook refreshes the cached active-principle
provider so the next prompt build drops the removed principle without a
restart.
"""

import asyncio
from collections.abc import Awaitable, Callable

from synthorg.core.types import NotBlankStr
from synthorg.meta.errors import RollbackMutationDeniedError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.meta import META_ROLLBACK_OPERATION_FAILED
from synthorg.persistence.active_principle_protocol import ActivePrincipleRepository

logger = get_logger(__name__)


class ActivePrincipleRemovalMutator:
    """Concrete principle-removal mutator backed by the active-principle store."""

    def __init__(
        self,
        *,
        repo: ActivePrincipleRepository,
        on_principle_removed: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._repo = repo
        self._on_principle_removed = on_principle_removed

    async def remove(self, *, principle_id: str) -> None:
        """Delete the active principle at ``principle_id``.

        Args:
            principle_id: The created principle's id (validated non-blank).

        Raises:
            RollbackMutationDeniedError: If ``principle_id`` is blank or
                the underlying delete fails.
            MemoryError: Raised on the corresponding failure path.
            RecursionError: Raised on the corresponding failure path.
            CancelledError: Raised on the corresponding failure path.
        """
        if not principle_id or not principle_id.strip():
            msg = "remove_principle principle_id must be non-blank"
            raise RollbackMutationDeniedError(msg)
        try:
            await self._repo.delete(NotBlankStr(principle_id))
        except MemoryError, RecursionError, asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                META_ROLLBACK_OPERATION_FAILED,
                operation_type="remove_principle",
                target=principle_id,
                reason="principle_delete_failed",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = f"remove_principle rejected: delete failed for {principle_id!r}"
            raise RollbackMutationDeniedError(msg) from exc

        # Refresh the cached active-principle snapshot so the next prompt build
        # drops the removed principle without a restart. Best-effort: a refresh
        # failure must not undo the durable delete, so it is logged and swallowed.
        if self._on_principle_removed is not None:
            try:
                await self._on_principle_removed()
            except MemoryError, RecursionError, asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 -- best-effort refresh, delete is durable
                logger.warning(
                    META_ROLLBACK_OPERATION_FAILED,
                    operation_type="remove_principle",
                    target=principle_id,
                    reason="active_principle_snapshot_refresh_failed",
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
