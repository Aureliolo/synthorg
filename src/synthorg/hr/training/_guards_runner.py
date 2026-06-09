"""Guard-chain runner for the training pipeline.

Applies the configured training guards sequentially per content type;
the service supplies its guard chain and curated items.
"""

from typing import TYPE_CHECKING

from synthorg.hr.training.models import (
    ContentType,
    TrainingApprovalHandle,
    TrainingGuardDecision,
    TrainingItem,
    TrainingPlan,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.training import (
    HR_TRAINING_GUARD_EVALUATION,
    HR_TRAINING_GUARD_FAILED,
    HR_TRAINING_REVIEW_PENDING,
)

if TYPE_CHECKING:
    # Collaborator protocol stays TYPE_CHECKING: tests pass partial
    # guard fakes; a runtime import would make typeguard reject them.
    from synthorg.hr.training.protocol import TrainingGuard

logger = get_logger(__name__)

# Curated items map passed through the pipeline (mirrors the alias in
# ``service``; structurally identical so it crosses the boundary cleanly).
_CuratedMap = dict[ContentType, tuple[TrainingItem, ...]]


async def apply_guards(
    plan: TrainingPlan,
    curated_items: _CuratedMap,
    *,
    guards: tuple[TrainingGuard, ...],
) -> tuple[
    tuple[tuple[ContentType, int], ...],
    tuple[str, ...],
    _CuratedMap,
    str | None,
    tuple[TrainingApprovalHandle, ...],
]:
    """Apply guard chain sequentially per content type.

    Returns guarded counts, errors, guarded items map, the first
    approval item id (the single-id field consumed by callers that
    expect one approval), and the full tuple of pending approval
    handles so no ID is lost when multiple content types trigger review.

    Returns:
        Tuple ``(tuple[tuple[ContentType, int], ...], tuple[str, ...], _CuratedMap,
        str | None, tuple[TrainingApprovalHandle, ...])``.
    """
    guarded_counts: list[tuple[ContentType, int]] = []
    all_errors: list[str] = []
    guarded_items: _CuratedMap = {}
    approval_handles: list[TrainingApprovalHandle] = []

    for ct in sorted(curated_items.keys(), key=lambda c: c.value):
        items = curated_items[ct]
        current_items, errors, handle = await run_guards_for_type(
            plan,
            ct,
            items,
            guards=guards,
        )
        all_errors.extend(errors)
        if handle is not None:
            approval_handles.append(handle)
        guarded_counts.append((ct, len(current_items)))
        guarded_items[ct] = current_items

    approval_id = (
        str(approval_handles[0].approval_item_id) if approval_handles else None
    )

    if approval_handles:
        logger.info(
            HR_TRAINING_REVIEW_PENDING,
            plan_id=str(plan.id),
            approval_count=len(approval_handles),
            content_types=[h.content_type.value for h in approval_handles],
        )

    return (
        tuple(guarded_counts),
        tuple(all_errors),
        guarded_items,
        approval_id,
        tuple(approval_handles),
    )


async def run_guards_for_type(
    plan: TrainingPlan,
    ct: ContentType,
    items: tuple[TrainingItem, ...],
    *,
    guards: tuple[TrainingGuard, ...],
) -> tuple[
    tuple[TrainingItem, ...],
    list[str],
    TrainingApprovalHandle | None,
]:
    """Apply the guard chain to a single content type.

    Returns:
        Tuple ``(tuple[TrainingItem, ...], list[str], TrainingApprovalHandle |
        None)``.

    Raises:
        Exception: Raised when the relevant invariant fails.
    """
    current_items = items
    errors: list[str] = []
    handle: TrainingApprovalHandle | None = None

    for guard in guards:
        try:
            decision: TrainingGuardDecision = await guard.evaluate(
                current_items,
                content_type=ct,
                plan=plan,
            )
        except Exception as exc:
            # See comment at the top of ``service._execute_locked``.
            logger.warning(
                HR_TRAINING_GUARD_FAILED,
                plan_id=str(plan.id),
                guard=guard.name,
                content_type=ct.value,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise

        logger.debug(
            HR_TRAINING_GUARD_EVALUATION,
            guard=guard.name,
            content_type=ct.value,
            approved=len(decision.approved_items),
            rejected=decision.rejected_count,
        )

        current_items = decision.approved_items
        errors.extend(decision.rejection_reasons)

        if decision.approval_item_id is not None:
            handle = TrainingApprovalHandle(
                approval_item_id=decision.approval_item_id,
                content_type=ct,
                item_count=decision.rejected_count,
            )

    return current_items, errors, handle
