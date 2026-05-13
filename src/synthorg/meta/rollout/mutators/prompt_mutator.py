"""PromptMutator backed by :class:`PrincipleOverrideRepository`.

Persists a restored principle as an override row keyed by ``scope``.
``synthorg.engine.strategy.principles.load_pack`` consults the same
repository on principle resolution, so subsequent reads see the
override without rewriting the YAML packs.
"""

from typing import TYPE_CHECKING

from synthorg.core.types import NotBlankStr
from synthorg.meta.errors import RollbackMutationDeniedError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.meta import META_ROLLBACK_OPERATION_FAILED

if TYPE_CHECKING:
    from synthorg.persistence.principle_override_protocol import (
        PrincipleOverrideRepository,
    )

logger = get_logger(__name__)


class PrincipleOverridePromptMutator:
    """Concrete ``PromptMutator`` backed by the override repository."""

    def __init__(
        self,
        *,
        override_repo: PrincipleOverrideRepository,
    ) -> None:
        self._repo = override_repo

    async def restore_principle(self, *, scope: str, text: str) -> None:
        """Persist the override at ``scope``.

        Raises:
            RollbackMutationDeniedError: If the underlying write fails
                or the inputs are not non-blank strings.
        """
        if not scope or not scope.strip():
            msg = "restore_principle scope must be non-blank"
            raise RollbackMutationDeniedError(msg)
        if not text or not text.strip():
            msg = "restore_principle text must be non-blank"
            raise RollbackMutationDeniedError(msg)
        try:
            await self._repo.save(
                NotBlankStr(scope),
                NotBlankStr(text),
                restored_from=NotBlankStr("rollback"),
            )
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            logger.warning(
                META_ROLLBACK_OPERATION_FAILED,
                operation_type="restore_prompt",
                target=scope,
                reason="override_save_failed",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = f"restore_principle rejected: override save failed for {scope!r}"
            raise RollbackMutationDeniedError(msg) from exc
