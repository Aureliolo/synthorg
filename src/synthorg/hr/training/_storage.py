"""Memory-backend storage helpers for the training pipeline.

Runs the post-guard storage stage of training: each content type's
guard-approved items are committed to the recipient agent's memory
backend, in parallel per content type and per item, with per-item
failures isolated from sibling stores so a single bad item does not
abort the whole batch.

The helpers are pure functions parameterised on the persistence
dependencies they need (``memory_backend``, ``training_namespace``,
``training_tags``), so they can be exercised without instantiating
the full :class:`TrainingService` graph.
"""

import asyncio
import copy
from collections.abc import Mapping
from types import MappingProxyType

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.memory_enums import MemoryCategory
from synthorg.hr.training.models import (
    ContentType,
    TrainingItem,
    TrainingPlan,
)
from synthorg.memory.models import MemoryMetadata, MemoryStoreRequest
from synthorg.memory.protocol import MemoryBackend
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.training import HR_TRAINING_STORE_FAILED

logger = get_logger(__name__)

# Map content types to memory categories for storage.  Module-private
# because callers should pass the resolved ``MemoryCategory`` if they
# want to override; the mapping is the single canonical source. The
# lookup at the call site below uses direct ``[ct]`` access so a new
# ``ContentType`` value added in the future fails fast with
# ``KeyError`` rather than being silently routed to ``PROCEDURAL``;
# storing semantic items under the procedural category would surface
# only at retrieval time, which is far too late.
#
# Wrapped in ``MappingProxyType`` over a ``copy.deepcopy`` per the
# project rule for non-Pydantic registries: a misbehaving import-site
# cannot mutate the canonical content-type-to-category routing at
# runtime, and the deepcopy guarantees the registry holds its own
# entries even if a future caller passes the dict literal here as a
# reference.
_CONTENT_TYPE_TO_CATEGORY: Mapping[ContentType, MemoryCategory] = MappingProxyType(
    copy.deepcopy(
        {
            ContentType.PROCEDURAL: MemoryCategory.PROCEDURAL,
            ContentType.SEMANTIC: MemoryCategory.SEMANTIC,
            ContentType.TOOL_PATTERNS: MemoryCategory.PROCEDURAL,
        },
    ),
)


async def store_guarded_items(
    plan: TrainingPlan,
    guarded_items: dict[ContentType, tuple[TrainingItem, ...]],
    *,
    memory_backend: MemoryBackend,
    training_namespace: str,
    training_tags: tuple[str, ...],
) -> tuple[tuple[ContentType, int], ...]:
    """Store approved items to memory backend in parallel per type.

    Args:
        plan: The training plan that produced the items.
        guarded_items: Per-content-type approved items.
        memory_backend: Memory backend to write to.
        training_namespace: Namespace for stored items.
        training_tags: Default tags applied to every item.

    Returns:
        Tuple of ``(content_type, stored_count)`` pairs in
        deterministic content-type order.
    """
    stored_counts: list[tuple[ContentType, int]] = []

    for ct in sorted(guarded_items.keys(), key=lambda c: c.value):
        items = guarded_items[ct]
        stored = await _store_items_for_type(
            plan,
            ct,
            items,
            memory_backend=memory_backend,
            training_namespace=training_namespace,
            training_tags=training_tags,
        )
        stored_counts.append((ct, stored))

    return tuple(stored_counts)


async def _store_items_for_type(  # noqa: PLR0913
    plan: TrainingPlan,
    ct: ContentType,
    items: tuple[TrainingItem, ...],
    *,
    memory_backend: MemoryBackend,
    training_namespace: str,
    training_tags: tuple[str, ...],
) -> int:
    """Store a single content type's items concurrently.

    Returns:
        Result of type ``int``.
    """
    if not items:
        return 0

    category = _CONTENT_TYPE_TO_CATEGORY[ct]

    async with asyncio.TaskGroup() as tg:
        store_tasks = [
            tg.create_task(
                _store_one_item(
                    plan,
                    ct,
                    category,
                    item,
                    memory_backend=memory_backend,
                    training_namespace=training_namespace,
                    training_tags=training_tags,
                ),
            )
            for item in items
        ]

    return sum(1 for task in store_tasks if task.result())


async def _store_one_item(  # noqa: PLR0913
    plan: TrainingPlan,
    ct: ContentType,
    category: MemoryCategory,
    item: TrainingItem,
    *,
    memory_backend: MemoryBackend,
    training_namespace: str,
    training_tags: tuple[str, ...],
) -> bool:
    """Store a single training item, logging any store failure.

    Returns:
        ``True`` if the operation succeeds, ``False`` otherwise.

    Raises:
        MemoryError: If the related operation fails.
        RecursionError: If the related operation fails.
    """
    tags = (
        *training_tags,
        f"training:{plan.id}",
        f"source:{item.source_agent_id}",
    )
    request = MemoryStoreRequest(
        category=category,
        namespace=training_namespace,
        content=item.content,
        metadata=MemoryMetadata(
            source=f"training:{plan.id}",
            confidence=item.relevance_score,
            tags=tags,
        ),
    )
    # Catch broadly so a single misbehaving backend, validator, or
    # request-shape mismatch on one item does not unwind the parent
    # ``asyncio.TaskGroup`` and cancel its sibling stores. Re-raise
    # only ``MemoryError`` / ``RecursionError`` per the project async
    # convention; everything else is logged and converted to a
    # ``False`` return so the per-content-type aggregator can record
    # a partial-store outcome instead of failing the whole pipeline.
    try:
        await memory_backend.store(plan.new_agent_id, request)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            HR_TRAINING_STORE_FAILED,
            plan_id=str(plan.id),
            item_id=str(item.id),
            content_type=ct.value,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return False
    return True
