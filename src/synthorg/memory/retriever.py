"""Context injection strategy — pre-retrieves and injects memories.

Orchestrates the full retrieval pipeline: backend query → ranking →
budget-fit → format.  Implements ``MemoryInjectionStrategy`` protocol.
"""

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from synthorg.memory import errors as memory_errors
from synthorg.memory.filter import TagBasedMemoryFilter
from synthorg.memory.formatter import format_memory_context
from synthorg.memory.injection import (
    DefaultTokenEstimator,
    TokenEstimator,
)
from synthorg.memory.models import MemoryQuery
from synthorg.memory.ranking import rank_memories
from synthorg.observability import get_logger
from synthorg.observability.events.memory import (
    MEMORY_FILTER_INIT,
    MEMORY_RETRIEVAL_COMPLETE,
    MEMORY_RETRIEVAL_DEGRADED,
    MEMORY_RETRIEVAL_SKIPPED,
    MEMORY_RETRIEVAL_START,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable

    from synthorg.core.enums import MemoryCategory
    from synthorg.core.types import NotBlankStr
    from synthorg.memory.filter import MemoryFilterStrategy
    from synthorg.memory.models import MemoryEntry
    from synthorg.memory.protocol import MemoryBackend
    from synthorg.memory.retrieval_config import MemoryRetrievalConfig
    from synthorg.memory.shared import SharedKnowledgeStore
    from synthorg.providers.models import ChatMessage, ToolDefinition

logger = get_logger(__name__)

# Alias to disambiguate from domain MemoryError
builtins_MemoryError = MemoryError  # noqa: N816


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
        builtins.MemoryError: Re-raised (system-level).
        RecursionError: Re-raised (system-level).
    """
    try:
        return await coro
    except builtins_MemoryError:
        raise
    except RecursionError:
        raise
    except memory_errors.MemoryError as exc:
        logger.warning(
            MEMORY_RETRIEVAL_DEGRADED,
            source=source,
            agent_id=agent_id,
            error_type=type(exc).__qualname__,
            exc_info=True,
        )
        return ()
    except Exception as exc:
        logger.error(
            MEMORY_RETRIEVAL_DEGRADED,
            source=source,
            agent_id=agent_id,
            error_type=type(exc).__qualname__,
            exc_info=True,
        )
        return ()


class ContextInjectionStrategy:
    """Context injection strategy — pre-retrieves and injects memories.

    Implements ``MemoryInjectionStrategy`` protocol.  Orchestrates
    the full pipeline: retrieve → rank → budget-fit → format.
    """

    def __init__(
        self,
        *,
        backend: MemoryBackend,
        config: MemoryRetrievalConfig,
        shared_store: SharedKnowledgeStore | None = None,
        token_estimator: TokenEstimator | None = None,
        memory_filter: MemoryFilterStrategy | None = None,
    ) -> None:
        """Initialise the context injection strategy.

        Args:
            backend: Memory backend for personal memories.
            config: Retrieval pipeline configuration.
            shared_store: Optional shared knowledge store.
            token_estimator: Optional custom token estimator.
            memory_filter: Optional filter applied after ranking,
                before formatting.  When ``None`` and
                ``config.non_inferable_only`` is ``True``, a
                ``TagBasedMemoryFilter`` is auto-created.  When ``None``
                and ``non_inferable_only`` is ``False``, all ranked
                memories are injected (backward-compatible).
        """
        self._backend = backend
        self._config = config
        self._shared_store = shared_store
        if memory_filter is None and config.non_inferable_only:
            memory_filter = TagBasedMemoryFilter()
        elif memory_filter is not None and config.non_inferable_only:
            logger.debug(
                MEMORY_FILTER_INIT,
                note="explicit memory_filter overrides non_inferable_only config",
                filter_strategy=getattr(memory_filter, "strategy_name", "unknown"),
            )
        self._memory_filter = memory_filter
        self._estimator = (
            token_estimator if token_estimator is not None else DefaultTokenEstimator()
        )
        logger.debug(
            MEMORY_RETRIEVAL_START,
            strategy="context_injection",
            backend=backend.backend_name
            if hasattr(backend, "backend_name")
            else type(backend).__qualname__,
            has_shared_store=shared_store is not None,
        )

    async def prepare_messages(
        self,
        agent_id: NotBlankStr,
        query_text: NotBlankStr,
        token_budget: int,
        *,
        categories: frozenset[MemoryCategory] | None = None,
    ) -> tuple[ChatMessage, ...]:
        """Full pipeline: retrieve → rank → budget-fit → format.

        Returns empty tuple on any failure (graceful degradation).
        Never raises domain memory errors to the caller.
        Re-raises ``builtins.MemoryError`` and ``RecursionError``.

        Args:
            agent_id: The agent requesting memories.
            query_text: Text for semantic retrieval.
            token_budget: Maximum tokens for memory content.
            categories: Optional category filter.

        Returns:
            Tuple of ``ChatMessage`` instances (may be empty).
        """
        logger.info(
            MEMORY_RETRIEVAL_START,
            agent_id=agent_id,
            token_budget=token_budget,
        )

        if token_budget <= 0:
            logger.info(
                MEMORY_RETRIEVAL_SKIPPED,
                agent_id=agent_id,
                reason="non-positive token budget",
                token_budget=token_budget,
            )
            return ()

        try:
            return await self._execute_pipeline(
                agent_id=agent_id,
                query_text=query_text,
                token_budget=token_budget,
                categories=categories,
            )
        except builtins_MemoryError:
            raise
        except RecursionError:
            raise
        except memory_errors.MemoryError:
            logger.warning(
                MEMORY_RETRIEVAL_DEGRADED,
                source="pipeline",
                agent_id=agent_id,
                exc_info=True,
            )
            return ()
        except Exception as exc:
            # ExceptionGroup may wrap system-level errors that must
            # propagate — inspect and re-raise them.
            if isinstance(exc, ExceptionGroup):
                system_errors = exc.subgroup(
                    lambda e: isinstance(
                        e,
                        builtins_MemoryError | RecursionError,
                    ),
                )
                if system_errors is not None:
                    raise system_errors from exc
            logger.error(
                MEMORY_RETRIEVAL_DEGRADED,
                source="pipeline",
                agent_id=agent_id,
                error_type=type(exc).__qualname__,
                exc_info=True,
            )
            return ()

    async def _execute_pipeline(
        self,
        *,
        agent_id: NotBlankStr,
        query_text: NotBlankStr,
        token_budget: int,
        categories: frozenset[MemoryCategory] | None,
    ) -> tuple[ChatMessage, ...]:
        """Execute the retrieval → rank → format pipeline.

        Args:
            agent_id: Agent identifier.
            query_text: Semantic search text.
            token_budget: Token budget.
            categories: Category filter.

        Returns:
            Formatted memory messages.
        """
        query = MemoryQuery(
            text=query_text,
            categories=categories,
            limit=self._config.max_memories,
        )

        personal_entries, shared_entries = await self._fetch_memories(
            agent_id=agent_id,
            query=query,
        )

        if not personal_entries and not shared_entries:
            logger.info(
                MEMORY_RETRIEVAL_SKIPPED,
                agent_id=agent_id,
                reason="no memories found",
            )
            return ()

        now = datetime.now(UTC)
        ranked = rank_memories(
            personal_entries,
            config=self._config,
            now=now,
            shared_entries=shared_entries,
        )

        if not ranked:
            logger.info(
                MEMORY_RETRIEVAL_SKIPPED,
                agent_id=agent_id,
                reason="all below min_relevance",
            )
            return ()

        if self._memory_filter is not None:
            try:
                ranked = self._memory_filter.filter_for_injection(ranked)
            except builtins_MemoryError, RecursionError:
                raise
            except Exception as exc:
                logger.warning(
                    MEMORY_RETRIEVAL_DEGRADED,
                    source="memory_filter",
                    agent_id=agent_id,
                    error_type=type(exc).__qualname__,
                    filter_strategy=getattr(
                        self._memory_filter, "strategy_name", "unknown"
                    ),
                    exc_info=True,
                )
                # Graceful degradation: use unfiltered ranked memories.
            if not ranked:
                logger.info(
                    MEMORY_RETRIEVAL_SKIPPED,
                    agent_id=agent_id,
                    reason="all filtered by memory filter",
                )
                return ()

        result = format_memory_context(
            ranked,
            estimator=self._estimator,
            token_budget=token_budget,
            injection_point=self._config.injection_point,
        )

        logger.info(
            MEMORY_RETRIEVAL_COMPLETE,
            agent_id=agent_id,
            personal_count=len(personal_entries),
            shared_count=len(shared_entries),
            ranked_count=len(ranked),
            messages_produced=len(result),
        )

        return result

    async def _fetch_memories(
        self,
        *,
        agent_id: NotBlankStr,
        query: MemoryQuery,
    ) -> tuple[tuple[MemoryEntry, ...], tuple[MemoryEntry, ...]]:
        """Fetch personal and shared memories in parallel.

        Each fetch is wrapped in error isolation so one failure
        doesn't cancel the other.  ``builtins.MemoryError`` and
        ``RecursionError`` are unwrapped from ``ExceptionGroup``
        and re-raised as bare exceptions.

        Args:
            agent_id: Agent identifier.
            query: Retrieval query.

        Returns:
            Tuple of (personal_entries, shared_entries).

        Raises:
            builtins.MemoryError: Unwrapped from TaskGroup.
            RecursionError: Unwrapped from TaskGroup.
        """
        personal_coro = _safe_call(
            self._backend.retrieve(agent_id, query),
            source="personal",
            agent_id=agent_id,
        )

        shared_store = self._shared_store
        if self._config.include_shared and shared_store is not None:
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
            except* builtins_MemoryError as eg:
                raise eg.exceptions[0] from eg
            except* RecursionError as eg:
                raise eg.exceptions[0] from eg
            return personal_task.result(), shared_task.result()

        personal = await personal_coro
        return personal, ()

    def get_tool_definitions(self) -> tuple[ToolDefinition, ...]:
        """Context injection provides no tools.

        Returns:
            Empty tuple.
        """
        return ()

    @property
    def strategy_name(self) -> str:
        """Human-readable strategy identifier.

        Returns:
            ``"context_injection"``.
        """
        return "context_injection"
