# module-kind: code
"""Deliverable-receipt seam for the review gate.

Houses the structural ``DeliverableReceiptSeam`` protocol and the
best-effort ``emit_receipt`` helper the review gate calls after a
COMPLETED transition.  Kept out of ``review_gate.py`` so the gate module
stays within its size budget and does not import the feature package.
"""

from typing import Protocol, runtime_checkable

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.task import Task
from synthorg.core.task_enums import TaskStatus
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.deliverable_receipts import RECEIPT_BUILD_FAILED

logger = get_logger(__name__)


@runtime_checkable
class DeliverableReceiptSeam(Protocol):
    """Minimal seam the review gate uses to emit a deliverable receipt.

    Structurally satisfied by ``DeliverableReceiptService`` and declared
    here so the engine does not import the feature package directly.
    """

    async def build_and_store(self, *, task: Task) -> object:
        """Build, persist, and render the receipt for a completed task."""
        ...


async def emit_receipt(
    receipt_service: DeliverableReceiptSeam | None,
    task: Task,
    target: TaskStatus,
) -> None:
    """Build the deliverable receipt on completion (best-effort).

    The transition has already committed, so a receipt-build failure is
    logged but never propagated: a half-built provenance bundle must not
    roll back a completed deliverable.
    """
    if target is not TaskStatus.COMPLETED or receipt_service is None:
        return
    try:
        await receipt_service.build_and_store(task=task)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            RECEIPT_BUILD_FAILED,
            task_id=task.id,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
