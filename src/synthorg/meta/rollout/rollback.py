"""Rollback executor with pluggable inverse-action dispatch.

Iterates a proposal's ``RollbackPlan`` and dispatches each
``RollbackOperation`` to the matching ``RollbackHandler``. Unknown
operation types fail loudly; per-operation failures stop the loop
immediately rather than silently partial-applying.
"""

from types import MappingProxyType
from typing import TYPE_CHECKING

from synthorg.meta.models import (
    ApplyResult,
    ImprovementProposal,
)
from synthorg.meta.rollout.inverse_dispatch import (
    UnknownRollbackOperationError,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from synthorg.core.types import NotBlankStr
    from synthorg.meta.rollout.inverse_dispatch import RollbackHandler
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

    async def execute(
        self,
        proposal: ImprovementProposal,
    ) -> ApplyResult:
        """Execute the rollback plan for ``proposal``.

        Dispatches each ``RollbackOperation`` to the handler keyed by
        ``operation_type``. Stops immediately on the first failure
        and returns a failure ``ApplyResult`` so the caller never
        sees a silently partial rollback.

        Returns:
            ``ApplyResult`` instance.

        Raises:
            UnknownRollbackOperationError: Raised on the corresponding failure path.
            MemoryError: Raised on the corresponding failure path.
            RecursionError: Raised on the corresponding failure path.
        """
        plan = proposal.rollback_plan
        total_changes = 0
        for operation in plan.operations:
            handler = self._handlers.get(operation.operation_type)
            if handler is None:
                logger.warning(
                    META_ROLLBACK_OPERATION_FAILED,
                    proposal_id=str(proposal.id),
                    operation_type=operation.operation_type,
                    reason="unknown_operation_type",
                )
                msg = (
                    f"no handler registered for "
                    f"operation_type={operation.operation_type!r}"
                )
                raise UnknownRollbackOperationError(msg)
            try:
                changes = await handler.revert(operation)
            except (MemoryError, RecursionError) as exc:
                log_exception_redacted(
                    logger,
                    META_ROLLBACK_OPERATION_FAILED,
                    exc,
                    proposal_id=str(proposal.id),
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
                    proposal_id=str(proposal.id),
                    operation_type=operation.operation_type,
                    target=operation.target,
                )
                return _fail(
                    proposal,
                    safe_error_description(exc),
                    total_changes,
                    error_type=type(exc).__name__,
                )
            total_changes += changes
            logger.info(
                META_ROLLBACK_OPERATION_APPLIED,
                proposal_id=str(proposal.id),
                operation_type=operation.operation_type,
                target=operation.target,
                changes=changes,
            )
        logger.info(
            META_ROLLBACK_COMPLETED,
            proposal_id=str(proposal.id),
            operations=len(plan.operations),
            changes_applied=total_changes,
            validation=plan.validation_check,
        )
        return ApplyResult(success=True, changes_applied=total_changes)


def _fail(
    proposal: ImprovementProposal,
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
        "proposal_id": str(proposal.id),
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
