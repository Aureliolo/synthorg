"""Memory-backend storage helpers for the training pipeline.

Extracted from :mod:`synthorg.hr.training.service` to keep that module
under the 800-line file ceiling. These helpers run after the guard
chain has approved items: each content type's surviving items are
stored to the recipient agent's memory backend in parallel.

The helpers are pure functions taking the dependencies they need as
arguments (``memory_backend``, ``training_namespace``, ``training_tags``)
so they can be tested without instantiating the full
:class:`TrainingService` graph.
"""

import asyncio
from typing import TYPE_CHECKING

from synthorg.core.enums import MemoryCategory
from synthorg.hr.training.models import (
    ContentType,
    TrainingItem,
    TrainingPlan,
)
from synthorg.memory.errors import MemoryError as _MemoryError
from synthorg.memory.models import MemoryMetadata, MemoryStoreRequest
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.training import HR_TRAINING_STORE_FAILED

if TYPE_CHECKING:
    from synthorg.memory.protocol import MemoryBackend

logger = get_logger(__name__)

# Map content types to memory categories for storage.  Module-private
# because callers should pass the resolved ``MemoryCategory`` if they
# want to override; the mapping is the single canonical source. The
# lookup at the call site below uses direct ``[ct]`` access so a new
# ``ContentType`` value added in the future fails fast with
# ``KeyError`` rather than being silently routed to ``PROCEDURAL``;
# storing semantic items under the procedural category would surface
# only at retrieval time, which is far too late.
_CONTENT_TYPE_TO_CATEGORY: dict[ContentType, MemoryCategory] = {
    ContentType.PROCEDURAL: MemoryCategory.PROCEDURAL,
    ContentType.SEMANTIC: MemoryCategory.SEMANTIC,
    ContentType.TOOL_PATTERNS: MemoryCategory.PROCEDURAL,
}


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
    """Store a single content type's items concurrently."""
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
    """Store a single training item, logging any store failure."""
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
    try:
        await memory_backend.store(plan.new_agent_id, request)
    except _MemoryError as exc:
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
