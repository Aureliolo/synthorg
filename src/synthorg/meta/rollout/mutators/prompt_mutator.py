"""PromptMutator backed by :class:`PrincipleOverrideRepository`.

Persists a restored principle as an override row keyed by ``scope``.
``synthorg.engine.strategy.principles.load_pack`` consults the same
repository on principle resolution, so subsequent reads see the
override without rewriting the YAML packs.
"""

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from synthorg.core.types import NotBlankStr
from synthorg.meta.errors import RollbackMutationDeniedError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.meta import META_ROLLBACK_OPERATION_FAILED

if TYPE_CHECKING:
    from synthorg.persistence.principle_override_protocol import (
        PrincipleOverrideRepository,
    )

from synthorg.persistence.principle_override_protocol import PrincipleOverride

logger = get_logger(__name__)


class PrincipleOverridePromptMutator:
    """Concrete ``PromptMutator`` backed by the override repository."""

    def __init__(
        self,
        *,
        override_repo: PrincipleOverrideRepository,
    ) -> None:
        self._repo = override_repo

    async def restore_principle(
        self,
        *,
        scope: str,
        text: str,
        operation_id: str | None = None,
    ) -> None:
        """Persist the override at ``scope``.

        Args:
            scope: Principle scope identifier (validated non-blank).
            text: Override principle text (validated non-blank).
            operation_id: Identifier of the rollback operation producing
                this override. When supplied, ``restored_from`` is
                persisted as ``"rollback:<operation_id>"`` so the audit
                trail can correlate the override back to the operation;
                when omitted, falls back to the literal ``"rollback"``.

        Raises:
            RollbackMutationDeniedError: If the underlying write fails
                or the inputs are not non-blank strings.
        """
        # ``NotBlankStr`` is a Pydantic ``Annotated`` type: constructing
        # it directly does not run the AfterValidator, so we check for
        # blank/whitespace-only inputs ourselves before treating them
        # as ``NotBlankStr`` further down.
        if not scope or not scope.strip():
            msg = "restore_principle scope must be non-blank"
            raise RollbackMutationDeniedError(msg)
        if not text or not text.strip():
            msg = "restore_principle text must be non-blank"
            raise RollbackMutationDeniedError(msg)
        typed_scope = NotBlankStr(scope)
        typed_text = NotBlankStr(text)
        provenance = (
            NotBlankStr(f"rollback:{operation_id}")
            if operation_id
            else NotBlankStr("rollback")
        )
        now = datetime.now(UTC)
        entity = PrincipleOverride(
            scope=typed_scope,
            text=typed_text,
            restored_from=provenance,
            created_at=now,
            updated_at=now,
        )
        try:
            await self._repo.save(entity)
        except MemoryError, RecursionError:
            raise
        except asyncio.CancelledError:
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
