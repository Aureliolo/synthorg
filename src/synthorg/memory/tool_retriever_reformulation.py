"""Reformulation mixin for ``ToolBasedInjectionStrategy``.

Isolates the Search-and-Ask iterative reformulation loop so
``tool_retriever.py`` can stay focused on tool dispatch and the
single-shot retrieval path.  The mixin relies on ``_backend``,
``_config``, ``_shared_store``, ``_reformulator``, and
``_sufficiency_checker`` attributes declared on the concrete strategy
class.

It also owns the one question "what does a tool search read", answered
in :meth:`ToolBasedReformulationMixin._retrieve_fused` and nowhere else.
There are three retrieval sites here (the unreformulated path, round 0
of the loop, and each reformulated round), and while each read the
agent's own backend alone, an agent was shown org knowledge in its
injected context and then handed a tool that could not reach it. A
newly-hired agent owns no memories at all, so the tool answered "No
memories found." to every query it was ever given: measured across a
live run, 44 of 44 calls, while the injection path returned 4 to 8 rows
from the same store, for the same agent, three seconds apart. An agent
that searches, gets nothing, rephrases and searches again is behaving
correctly; it was the tool that could not answer.
"""

import asyncio
import builtins

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.memory_enums import MemoryCategory
from synthorg.core.types import NotBlankStr
from synthorg.memory.models import MemoryEntry, MemoryQuery
from synthorg.memory.namespace_scope import ambient_read_namespaces
from synthorg.memory.protocol import MemoryBackend
from synthorg.memory.reformulation import (
    QueryReformulator,
    SufficiencyChecker,
)
from synthorg.memory.retrieval_config import MemoryRetrievalConfig
from synthorg.memory.shared import SharedKnowledgeStore
from synthorg.memory.tool_retriever_helpers import _truncate_entries, merge_results
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.memory import (
    MEMORY_REFORMULATION_EXHAUSTED,
    MEMORY_REFORMULATION_FAILED,
    MEMORY_REFORMULATION_FINAL_CHECK,
    MEMORY_REFORMULATION_ROUND,
    MEMORY_REFORMULATION_SUFFICIENT,
    MEMORY_RETRIEVAL_DEGRADED,
    MEMORY_SUFFICIENCY_CHECK_FAILED,
)

logger = get_logger(__name__)


class ToolBasedReformulationMixin:
    """Search-and-Ask reformulation loop for ``ToolBasedInjectionStrategy``."""

    __slots__ = ()

    _backend: MemoryBackend
    _config: MemoryRetrievalConfig
    _shared_store: SharedKnowledgeStore | None
    _reformulator: QueryReformulator | None
    _sufficiency_checker: SufficiencyChecker | None

    async def _retrieve_fused(
        self,
        *,
        agent_id: str,
        query_text: str,
        limit: int,
        categories: frozenset[MemoryCategory] | None,
    ) -> tuple[MemoryEntry, ...]:
        """Read the agent's own memory fused with org knowledge.

        The two halves take DIFFERENT error postures on purpose. The
        personal read propagates, so the caller answers "search
        unavailable" rather than "no memories found": an agent told the
        store is empty stops asking, which is the worse of the two wrong
        answers. The shared read is additive, so its failure degrades to
        personal-only and is logged rather than losing the half that did
        answer.

        Returns:
            Personal and shared entries merged by id, best first, cut to
            *limit*.

        Raises:
            MemoryError: If a critical system error occurs.
            RecursionError: If a critical system error occurs.
            CancelledError: If the task is cancelled.
        """
        query = MemoryQuery(
            text=query_text,
            limit=limit,
            categories=categories,
            namespaces=ambient_read_namespaces(),
        )
        personal = await self._backend.retrieve(NotBlankStr(agent_id), query)
        shared = await self._shared_entries(
            agent_id=agent_id,
            query=query,
        )
        if not shared:
            return personal
        return _truncate_entries(merge_results(personal, shared), limit)

    async def _shared_entries(
        self,
        *,
        agent_id: str,
        query: MemoryQuery,
    ) -> tuple[MemoryEntry, ...]:
        """Read org knowledge for *query*, degrading to empty on failure.

        Returns:
            The shared entries, or empty when the store is absent, the
            config excludes it, or the read failed.

        Raises:
            MemoryError: If a critical system error occurs.
            RecursionError: If a critical system error occurs.
            CancelledError: If the task is cancelled.
        """
        store = self._shared_store
        if store is None or not self._config.include_shared:
            return ()
        try:
            return await store.search_shared(query, exclude_agent=agent_id)
        except builtins.MemoryError, RecursionError:
            logger.error(
                MEMORY_RETRIEVAL_DEGRADED,
                source="shared",
                agent_id=agent_id,
                error_type="system",
                reason="system_error_in_shared_search",
            )
            raise
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                MEMORY_RETRIEVAL_DEGRADED,
                source="shared",
                agent_id=agent_id,
                error=safe_error_description(exc),
                error_type=type(exc).__qualname__,
                reason="org_knowledge_unavailable_personal_only",
            )
            return ()

    async def _retrieve_with_reformulation(
        self,
        *,
        query_text: str,
        limit: int,
        categories: frozenset[MemoryCategory] | None,
        agent_id: str,
    ) -> tuple[MemoryEntry, ...]:
        """Retrieve memories, optionally with iterative reformulation.

        When ``query_reformulation_enabled`` is True and both the
        reformulator and sufficiency checker are configured, performs
        up to ``max_reformulation_rounds`` rounds of
        ``search -> check sufficiency -> reformulate query``.

        Returns the cumulative merged results across all rounds.
        Duplicates (by entry ID) are deduplicated across rounds,
        keeping the higher-relevance-score version; ``None`` relevance
        is treated as ``0.0``.

        Returns:
            Tuple of ``MemoryEntry``.
        """
        reformulator = self._reformulator
        sufficiency_checker = self._sufficiency_checker
        if (
            not self._config.query_reformulation_enabled
            or reformulator is None
            or sufficiency_checker is None
        ):
            return await self._retrieve_fused(
                agent_id=agent_id,
                query_text=query_text,
                limit=limit,
                categories=categories,
            )

        return await self._reformulation_loop(
            reformulator=reformulator,
            sufficiency_checker=sufficiency_checker,
            query_text=query_text,
            limit=limit,
            categories=categories,
            agent_id=agent_id,
        )

    async def _reformulation_loop(
        self,
        *,
        reformulator: QueryReformulator,
        sufficiency_checker: SufficiencyChecker,
        query_text: str,
        limit: int,
        categories: frozenset[MemoryCategory] | None,
        agent_id: str,
    ) -> tuple[MemoryEntry, ...]:
        """Run the iterative Search-and-Ask reformulation loop.

        Starts with the initial query, retrieves, checks sufficiency,
        reformulates if insufficient, and re-retrieves -- up to
        ``config.max_reformulation_rounds`` rounds.  Results across
        rounds are merged by ID, keeping the higher-relevance version
        of any duplicate and truncating to ``limit`` on return so the
        tool contract is honoured regardless of how many rounds ran.

        Reformulator, sufficiency checker, and mid-loop backend
        retrieve calls are all wrapped in error isolation: if any
        raises a non-system exception, the round helper returns
        ``None`` and the loop returns the current cumulative entries
        rather than propagating.  System errors
        (builtins.MemoryError, RecursionError) still propagate.  The
        initial (round 0) retrieval is NOT isolated, so a non-system
        error from the backend there propagates to the caller.

        Returns:
            Tuple of ``MemoryEntry`` results merged across rounds and
            truncated to ``limit``.

        Raises:
            MemoryError: If a critical system error occurs.
            RecursionError: If a critical system error occurs.
            CancelledError: If the task is cancelled.
            Exception: Any non-system error raised by the backend during
                the initial (round 0) retrieval propagates uncaught; only
                the per-round reformulation steps isolate such errors.
        """
        max_rounds = self._config.max_reformulation_rounds
        current_query = query_text
        try:
            entries = await self._retrieve_fused(
                agent_id=agent_id,
                query_text=current_query,
                limit=limit,
                categories=categories,
            )
        except builtins.MemoryError, RecursionError:
            logger.error(
                MEMORY_RETRIEVAL_DEGRADED,
                agent_id=agent_id,
                round=0,
                query_length=len(current_query),
                limit=limit,
                error_type="system",
                reason="system_error_in_initial_retrieve",
            )
            raise
        for round_idx in range(max_rounds):
            step = await self._run_reformulation_step(
                reformulator=reformulator,
                sufficiency_checker=sufficiency_checker,
                current_query=current_query,
                entries=entries,
                limit=limit,
                categories=categories,
                agent_id=agent_id,
                round_idx=round_idx,
            )
            if step is None:
                return _truncate_entries(entries, limit)
            entries, current_query = step
        final_sufficient = await self._check_sufficiency(
            sufficiency_checker,
            current_query,
            entries,
            agent_id=agent_id,
            round_idx=max_rounds,
        )
        logger.info(
            MEMORY_REFORMULATION_FINAL_CHECK,
            agent_id=agent_id,
            rounds_exhausted=max_rounds,
            result_count=len(entries),
            sufficient=final_sufficient,
            sufficiency_check_failed=final_sufficient is None,
        )
        return _truncate_entries(entries, limit)

    async def _run_reformulation_step(
        self,
        *,
        reformulator: QueryReformulator,
        sufficiency_checker: SufficiencyChecker,
        current_query: str,
        entries: tuple[MemoryEntry, ...],
        limit: int,
        categories: frozenset[MemoryCategory] | None,
        agent_id: str,
        round_idx: int,
    ) -> tuple[tuple[MemoryEntry, ...], str] | None:
        """Execute one round of the Search-and-Ask loop.

        Returns ``(new_entries, new_query)`` when the loop should
        continue, or ``None`` when it should terminate with the
        current ``entries`` (sufficiency met, reformulator exhausted,
        or non-system error in any sub-step).

        Returns:
            The resulting ``tuple[tuple[MemoryEntry, ...], str]``, or ``None`` when
            unavailable.
        """
        sufficient = await self._check_sufficiency(
            sufficiency_checker,
            current_query,
            entries,
            agent_id=agent_id,
            round_idx=round_idx,
        )
        if sufficient is None:
            return None
        if sufficient:
            logger.info(
                MEMORY_REFORMULATION_SUFFICIENT,
                agent_id=agent_id,
                round=round_idx,
                result_count=len(entries),
            )
            return None
        new_query = await self._reformulate(
            reformulator,
            current_query,
            entries,
            agent_id=agent_id,
            round_idx=round_idx,
        )
        if new_query is None or new_query == current_query:
            logger.info(
                MEMORY_REFORMULATION_EXHAUSTED,
                agent_id=agent_id,
                round=round_idx + 1,
                result_count=len(entries),
                reason=(
                    "reformulator_stable"
                    if new_query == current_query
                    else "reformulator_gave_up"
                ),
            )
            return None
        logger.info(
            MEMORY_REFORMULATION_ROUND,
            agent_id=agent_id,
            round=round_idx + 1,
            original_length=len(current_query),
            new_length=len(new_query),
        )
        new_entries = await self._retrieve_round(
            agent_id=agent_id,
            query=new_query,
            limit=limit,
            categories=categories,
            round_idx=round_idx,
        )
        if new_entries is None:
            return None
        return merge_results(entries, new_entries), new_query

    @staticmethod
    async def _check_sufficiency(
        sufficiency_checker: SufficiencyChecker,
        query: str,
        entries: tuple[MemoryEntry, ...],
        *,
        agent_id: str,
        round_idx: int,
    ) -> bool | None:
        """Run the sufficiency checker with error isolation.

        Returns ``True``/``False`` on success, or ``None`` when the
        check raised a non-system exception (caller should exit the
        loop and return current cumulative entries).

        Returns:
            ``True`` when sufficient, ``False`` when insufficient, or
            ``None`` when a non-system checker failure is isolated.

        Raises:
            MemoryError: If a critical system error occurs.
            RecursionError: If a critical system error occurs.
            CancelledError: If the task is cancelled.
        """
        try:
            return await sufficiency_checker.check_sufficiency(query, entries)
        except builtins.MemoryError, RecursionError:
            logger.error(
                MEMORY_SUFFICIENCY_CHECK_FAILED,
                agent_id=agent_id,
                round=round_idx,
                error_type="system",
                reason="system_error_in_sufficiency_check",
            )
            raise
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                MEMORY_SUFFICIENCY_CHECK_FAILED,
                agent_id=agent_id,
                round=round_idx,
                error=safe_error_description(exc),
                error_type=type(exc).__qualname__,
            )
            return None

    @staticmethod
    async def _reformulate(
        reformulator: QueryReformulator,
        current_query: str,
        entries: tuple[MemoryEntry, ...],
        *,
        agent_id: str,
        round_idx: int,
    ) -> str | None:
        """Run the reformulator with error isolation.

        Returns the new query string, ``None`` when the reformulator
        gave up, or ``None`` when it raised a non-system exception
        (caller cannot distinguish these two cases -- both terminate
        the loop).

        Returns:
            The resulting ``str``, or ``None`` when unavailable.

        Raises:
            MemoryError: If the related operation fails.
            RecursionError: If the related operation fails.
            CancelledError: If the related operation fails.
        """
        try:
            return await reformulator.reformulate(current_query, entries)
        except builtins.MemoryError, RecursionError:
            logger.error(
                MEMORY_REFORMULATION_FAILED,
                agent_id=agent_id,
                round=round_idx,
                error_type="system",
                reason="system_error_in_reformulate",
            )
            raise
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                MEMORY_REFORMULATION_FAILED,
                agent_id=agent_id,
                round=round_idx,
                error=safe_error_description(exc),
                error_type=type(exc).__qualname__,
            )
            return None

    async def _retrieve_round(
        self,
        *,
        agent_id: str,
        query: str,
        limit: int,
        categories: frozenset[MemoryCategory] | None,
        round_idx: int,
    ) -> tuple[MemoryEntry, ...] | None:
        """Retrieve for a reformulated round with error isolation.

        Returns the new entries, or ``None`` on non-system failure so
        the loop can degrade gracefully to the accumulated results.

        Returns:
            The resulting ``tuple[MemoryEntry, ...]``, or ``None`` when unavailable.

        Raises:
            MemoryError: If the related operation fails.
            RecursionError: If the related operation fails.
            CancelledError: If the related operation fails.
        """
        from synthorg.memory.errors import (  # noqa: PLC0415
            MemoryError as DomainMemoryError,
        )

        try:
            return await self._retrieve_fused(
                agent_id=agent_id,
                query_text=query,
                limit=limit,
                categories=categories,
            )
        except builtins.MemoryError, RecursionError:
            logger.error(
                MEMORY_RETRIEVAL_DEGRADED,
                agent_id=agent_id,
                round=round_idx + 1,
                query_length=len(query),
                limit=limit,
                error_type="system",
                reason="system_error_in_retrieve_round",
            )
            raise
        except DomainMemoryError as exc:
            logger.warning(
                MEMORY_RETRIEVAL_DEGRADED,
                agent_id=agent_id,
                round=round_idx + 1,
                reason="retrieve_failed_mid_loop",
                error=safe_error_description(exc),
                error_type=type(exc).__qualname__,
            )
            return None
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.error(
                MEMORY_RETRIEVAL_DEGRADED,
                agent_id=agent_id,
                round=round_idx + 1,
                reason="unexpected_retrieve_failure_mid_loop",
                error=safe_error_description(exc),
                error_type=type(exc).__qualname__,
            )
            return None
