"""Rollback executor with pluggable inverse-action dispatch.

Iterates a proposal's ``RollbackPlan`` and dispatches each
``RollbackOperation`` to the matching ``RollbackHandler``. Unknown
operation types fail loudly; per-operation failures stop the loop
immediately rather than silently partial-applying.
"""

from collections.abc import Mapping, Sequence
from types import MappingProxyType
from uuid import UUID

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.meta.models import (
    ApplyResult,
    ImprovementProposal,
    RollbackOperation,
)
from synthorg.meta.rollout.inverse_dispatch import (
    RollbackHandler,
    UnknownRollbackOperationError,
)
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.events.meta import (
    META_ROLLBACK_COMPLETED,
    META_ROLLBACK_FAILED,
    META_ROLLBACK_OPERATION_APPLIED,
    META_ROLLBACK_OPERATION_FAILED,
)

logger = get_logger(__name__)


class RollbackExecutor:
    """Executes rollback plans by dispatching inverse actions.

    Args:
        handlers: Mapping from ``operation_type`` to the handler that
            applies the inverse action. Unknown ``operation_type``
            values raise ``UnknownRollbackOperationError``. Pass an
            empty mapping only in tests that do not exercise real
            rollback dispatch.
    """

    def __init__(
        self,
        *,
        handlers: Mapping[NotBlankStr, RollbackHandler] | None = None,
    ) -> None:
        # Shallow copy of the dispatch table + read-only wrapper:
        # callers can't swap entries after construction, but handler
        # instances stay identity-stable so their mutable state
        # (counters, caches) remains observable to owners and tests.
        snapshot: dict[NotBlankStr, RollbackHandler] = (
            dict(handlers) if handlers else {}
        )
        self._handlers: Mapping[NotBlankStr, RollbackHandler] = MappingProxyType(
            snapshot,
        )

    async def aclose(self) -> None:
        """Close any handler that owns a closeable resource.

        The ``revert_branch`` handler wraps a GitHub HTTP client whose
        connection pool would otherwise leak past the self-improvement
        service lifecycle. Iterates every handler and closes the ones that
        expose ``aclose`` (best-effort: a failed close on one handler does
        not stop the others).
        """
        for handler in self._handlers.values():
            close = getattr(handler, "aclose", None)
            if close is None:
                continue
            try:
                await close()
            except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                reraise_critical(exc)
                logger.warning(
                    META_ROLLBACK_FAILED,
                    reason="handler_close_failed",
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )

    async def execute(
        self,
        proposal: ImprovementProposal,
    ) -> ApplyResult:
        """Execute the rollback plan declared on ``proposal``.

        Returns:
            ``ApplyResult`` instance.

        Raises:
            UnknownRollbackOperationError: Raised on the corresponding failure path.
            MemoryError: Raised on the corresponding failure path.
            RecursionError: Raised on the corresponding failure path.
        """
        plan = proposal.rollback_plan
        return await self.execute_operations(
            plan.operations,
            proposal_id=proposal.id,
            validation_check=plan.validation_check,
        )

    async def execute_operations(
        self,
        operations: Sequence[RollbackOperation],
        *,
        proposal_id: UUID,
        validation_check: str | None = None,
    ) -> ApplyResult:
        """Dispatch each ``RollbackOperation`` to its registered handler.

        Stops immediately on the first failure and returns a failure
        ``ApplyResult`` so the caller never sees a silently partial
        rollback. Used directly by the meta-loop auto-rollback, which
        dispatches the applier-materialised inverse operations rather
        than a proposal's static plan.

        Returns:
            ``ApplyResult`` instance.

        Raises:
            UnknownRollbackOperationError: Raised on the corresponding failure path.
            MemoryError: Raised on the corresponding failure path.
            RecursionError: Raised on the corresponding failure path.
        """
        total_changes = 0
        for operation in operations:
            try:
                total_changes += await self._revert_one(operation, proposal_id)
            except MemoryError, RecursionError, UnknownRollbackOperationError:
                raise
            except Exception as exc:  # noqa: BLE001 -- best-effort: convert to failure
                return _fail(
                    proposal_id,
                    safe_error_description(exc),
                    total_changes,
                    error_type=type(exc).__name__,
                )
        logger.info(
            META_ROLLBACK_COMPLETED,
            proposal_id=str(proposal_id),
            operations=len(operations),
            changes_applied=total_changes,
            validation=validation_check,
        )
        return ApplyResult(success=True, changes_applied=total_changes)

    async def _revert_one(
        self,
        operation: RollbackOperation,
        proposal_id: UUID,
    ) -> int:
        """Dispatch one operation to its handler and log the outcome.

        Returns:
            The number of changes the handler reverted.

        Raises:
            UnknownRollbackOperationError: No handler is registered for
                ``operation.operation_type``.
            MemoryError: Re-raised after a redacted log (catastrophic).
            RecursionError: Re-raised after a redacted log (catastrophic).
        """
        handler = self._handlers.get(operation.operation_type)
        if handler is None:
            logger.warning(
                META_ROLLBACK_OPERATION_FAILED,
                proposal_id=str(proposal_id),
                operation_type=operation.operation_type,
                reason="unknown_operation_type",
            )
            msg = (
                f"no handler registered for operation_type={operation.operation_type!r}"
            )
            raise UnknownRollbackOperationError(msg)
        try:
            changes = await handler.revert(operation)
        except (MemoryError, RecursionError) as exc:
            log_exception_redacted(
                logger,
                META_ROLLBACK_OPERATION_FAILED,
                exc,
                proposal_id=str(proposal_id),
                operation_type=operation.operation_type,
                target=operation.target,
                reason="catastrophic_error",
            )
            raise
        except Exception as exc:
            log_exception_redacted(
                logger,
                META_ROLLBACK_OPERATION_FAILED,
                exc,
                proposal_id=str(proposal_id),
                operation_type=operation.operation_type,
                target=operation.target,
            )
            raise
        logger.info(
            META_ROLLBACK_OPERATION_APPLIED,
            proposal_id=str(proposal_id),
            operation_type=operation.operation_type,
            target=operation.target,
            changes=changes,
        )
        return changes


def _fail(
    proposal_id: UUID,
    error_message: str,
    changes_applied: int,
    *,
    error_type: str | None = None,
) -> ApplyResult:
    """Log and return a failure ``ApplyResult`` preserving partial count.

    Callers from a typed-exception path supply ``error_type`` so the
    structured log carries both the redacted message and the exception
    class. The keyword is optional for legacy / validation callers that
    do not have an exception in hand.

    Returns:
        ``ApplyResult`` instance.
    """
    log_kwargs: dict[str, object] = {
        "proposal_id": str(proposal_id),
        "error": error_message,
        "changes_applied": changes_applied,
    }
    if error_type is not None:
        log_kwargs["error_type"] = error_type
    logger.warning(META_ROLLBACK_FAILED, **log_kwargs)
    return ApplyResult(
        success=False,
        error_message=error_message,
        changes_applied=changes_applied,
    )
