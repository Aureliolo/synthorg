"""Backend fetch helpers for the context-injection retriever.

Isolates the error-isolated, parallel backend reads (dense personal +
shared, and sparse) from the pipeline orchestration in ``retriever``.
``builtins.MemoryError`` and ``RecursionError`` always propagate;
domain and generic failures degrade to empty results.
"""

import asyncio
import builtins
from collections.abc import Awaitable

import synthorg.memory.errors as memory_errors
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.memory.models import MemoryEntry, MemoryQuery
from synthorg.memory.protocol import MemoryBackend
from synthorg.memory.shared import SharedKnowledgeStore
from synthorg.observability import get_logger
from synthorg.observability.events.memory import MEMORY_RETRIEVAL_DEGRADED

logger = get_logger(__name__)


async def _safe_call(
    coro: Awaitable[tuple[MemoryEntry, ...]],
    *,
    source: str,
    agent_id: NotBlankStr,
) -> tuple[MemoryEntry, ...]:
    """Await *coro* and return ``()`` on domain/generic failure.

    Re-raises ``builtins.MemoryError`` and ``RecursionError``
    (system-level).  Catches ``memory_errors.MemoryError`` (domain
    base) as a warning and any other ``Exception`` as an error.

    Args:
        coro: Awaitable returning a tuple of memory entries.
        source: Label for log messages (e.g. ``"personal"``).
        agent_id: Agent identifier for log context.

    Returns:
        Tuple of entries, or empty on failure.

    Raises:
        builtins.MemoryError: Re-raised (system-level). Domain
            ``memory_errors.MemoryError`` is caught and yields ``()``.
        RecursionError: Re-raised (system-level).
    """
    try:
        return await coro
    except builtins.MemoryError, RecursionError:
        logger.error(
            MEMORY_RETRIEVAL_DEGRADED,
            source=source,
            agent_id=agent_id,
            error_type="system",
        )
        raise
    except memory_errors.MemoryError as exc:
        logger.warning(
            MEMORY_RETRIEVAL_DEGRADED,
            source=source,
            agent_id=agent_id,
            error_type=type(exc).__qualname__,
        )
        return ()
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.error(
            MEMORY_RETRIEVAL_DEGRADED,
            source=source,
            agent_id=agent_id,
            error_type=type(exc).__qualname__,
        )
        return ()


async def fetch_memories(
    *,
    backend: MemoryBackend,
    shared_store: SharedKnowledgeStore | None,
    include_shared: bool,
    agent_id: NotBlankStr,
    query: MemoryQuery,
) -> tuple[tuple[MemoryEntry, ...], tuple[MemoryEntry, ...]]:
    """Fetch personal and shared memories in parallel.

    Each fetch is wrapped in error isolation so one failure
    doesn't cancel the other.  ``builtins.MemoryError`` and
    ``RecursionError`` are unwrapped from ``ExceptionGroup``
    and re-raised as bare exceptions.

    Args:
        backend: Memory backend for personal memories.
        shared_store: Optional shared knowledge store.
        include_shared: Whether to query the shared store.
        agent_id: Agent identifier.
        query: Retrieval query.

    Returns:
        Tuple of (personal_entries, shared_entries).

    Raises:
        builtins.MemoryError: Unwrapped from TaskGroup.
        RecursionError: Unwrapped from TaskGroup.
    """
    personal_coro = _safe_call(
        backend.retrieve(agent_id, query),
        source="personal",
        agent_id=agent_id,
    )

    if include_shared and shared_store is not None:
        shared_coro = _safe_call(
            shared_store.search_shared(
                query,
                exclude_agent=agent_id,
            ),
            source="shared",
            agent_id=agent_id,
        )
        try:
            async with asyncio.TaskGroup() as tg:
                personal_task = tg.create_task(
                    personal_coro,
                )
                shared_task = tg.create_task(
                    shared_coro,
                )
        # TaskGroup wraps task exceptions in ExceptionGroup;
        # unwrap system-level errors so callers see bare exceptions.
        except* builtins.MemoryError as eg:
            raise eg.exceptions[0] from eg
        except* RecursionError as eg:
            raise eg.exceptions[0] from eg
        return personal_task.result(), shared_task.result()

    personal = await personal_coro
    return personal, ()


async def fetch_sparse_memories(
    *,
    backend: MemoryBackend,
    agent_id: NotBlankStr,
    query: MemoryQuery,
) -> tuple[tuple[MemoryEntry, ...], tuple[MemoryEntry, ...]]:
    """Fetch sparse (BM25) results from the backend.

    Returns empty tuples when the backend does not support
    sparse search.  Uses the same error isolation pattern as
    ``fetch_memories()``.

    Args:
        backend: Memory backend (may expose ``retrieve_sparse``).
        agent_id: Agent identifier.
        query: Retrieval query.

    Returns:
        Tuple of (personal_sparse, shared_sparse).
    """
    if not getattr(backend, "supports_sparse_search", False):
        return (), ()

    retrieve_fn = getattr(backend, "retrieve_sparse", None)
    if retrieve_fn is None:
        return (), ()

    # SharedKnowledgeStore does not yet expose retrieve_sparse,
    # so shared sparse is disabled until the protocol is extended.
    personal = await _safe_call(
        retrieve_fn(agent_id, query),
        source="sparse_personal",
        agent_id=agent_id,
    )
    return personal, ()
