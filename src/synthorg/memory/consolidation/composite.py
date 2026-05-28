"""Composite consolidation strategy (ADR-0005).

``CompositeConsolidationStrategy(selector, op, *, parallel=False)``
satisfies the existing ``ConsolidationStrategy`` Protocol by running
the selector then aggregating one :class:`OpResult` per group.

``parallel`` (the factory wires it ``True`` for the LLM composite)
moves the per-group op calls under one ``asyncio.TaskGroup`` and
unwraps the resulting ``ExceptionGroup`` to the first exception --
byte-identical with ``LLMConsolidationStrategy._run_groups``.
"""

import asyncio
from typing import TYPE_CHECKING

from synthorg.core.types import NotBlankStr
from synthorg.memory.consolidation.models import (
    ArchivalModeAssignment,
    ConsolidationResult,
)
from synthorg.observability import get_logger
from synthorg.observability.events.consolidation import (
    LLM_STRATEGY_ERROR,
    STRATEGY_COMPLETE,
    STRATEGY_START,
)
from synthorg.providers.errors import ProviderError

if TYPE_CHECKING:
    from synthorg.memory.consolidation.axis import (
        ConsolidationOp,
        EntrySelector,
        OpResult,
        SelectionGroup,
    )
    from synthorg.memory.models import MemoryEntry

logger = get_logger(__name__)


class CompositeConsolidationStrategy:
    """Selector + op composite implementing ``ConsolidationStrategy``.

    Args:
        selector: Decides which entries are consolidated per group.
        op: Summarises + stores + deletes one group with its
            strategy's exact failure semantics.
        parallel: When ``True``, the per-group op calls run under one
            ``asyncio.TaskGroup`` (the LLM strategy's cross-group
            fan-out). Defaults to ``False`` (sequential -- Simple,
            DualMode).
    """

    def __init__(
        self,
        *,
        selector: EntrySelector,
        op: ConsolidationOp,
        parallel: bool = False,
    ) -> None:
        self._selector = selector
        self._op = op
        self._parallel = parallel

    async def consolidate(
        self,
        entries: tuple[MemoryEntry, ...],
        *,
        agent_id: NotBlankStr,
    ) -> ConsolidationResult:
        """Run the selector then aggregate one ``OpResult`` per group.

        Returns:
            Result of type ``ConsolidationResult``.
        """
        if not entries:
            return ConsolidationResult()

        logger.info(
            STRATEGY_START,
            agent_id=agent_id,
            entry_count=len(entries),
        )

        groups = self._selector.select(entries)
        if not groups:
            logger.info(
                STRATEGY_COMPLETE,
                agent_id=agent_id,
                consolidated_count=0,
                summary_count=0,
            )
            return ConsolidationResult()

        context = await self._op.prepare(agent_id)
        if self._parallel:
            results = await self._run_parallel(groups, agent_id, context)
        else:
            results = [
                await self._op.consolidate(group, context=context) for group in groups
            ]

        removed_ids: list[NotBlankStr] = []
        summary_ids: list[NotBlankStr] = []
        mode_assignments: list[ArchivalModeAssignment] = []
        for res in results:
            summary_ids.append(res.summary_id)
            removed_ids.extend(res.removed_ids)
            mode_assignments.extend(res.mode_assignments)

        result = ConsolidationResult(
            removed_ids=tuple(removed_ids),
            summary_ids=tuple(summary_ids),
            mode_assignments=tuple(mode_assignments),
        )
        logger.info(
            STRATEGY_COMPLETE,
            agent_id=agent_id,
            consolidated_count=result.consolidated_count,
            summary_count=len(result.summary_ids),
        )
        return result

    async def _run_parallel(
        self,
        groups: tuple[SelectionGroup, ...],
        agent_id: NotBlankStr,
        context: object,
    ) -> list[OpResult]:
        """Cross-group ``TaskGroup`` fan-out (LLM ``_run_groups`` parity).

        Unwraps the ``ExceptionGroup`` so callers see the original
        exception type (matching the pre-split sequential semantics);
        every ``except*`` branch logs the sibling count before
        re-raising the first exception.

        Returns:
            List of ``OpResult``.
        """
        try:
            async with asyncio.TaskGroup() as tg:
                tasks = [
                    tg.create_task(
                        self._op.consolidate(group, context=context)  # type: ignore[arg-type]
                    )
                    for group in groups
                ]
        except* MemoryError as eg:
            self._log_taskgroup_failure(agent_id, eg, "task_group_memory_error")
            raise eg.exceptions[0] from eg
        except* RecursionError as eg:
            self._log_taskgroup_failure(agent_id, eg, "task_group_recursion_error")
            raise eg.exceptions[0] from eg
        except* ProviderError as eg:
            self._log_taskgroup_failure(agent_id, eg, "task_group_provider_error")
            raise eg.exceptions[0] from eg
        except* Exception as eg:
            self._log_taskgroup_failure(agent_id, eg, "task_group_unexpected_error")
            raise eg.exceptions[0] from eg
        return [task.result() for task in tasks]

    @staticmethod
    def _log_taskgroup_failure(
        agent_id: NotBlankStr,
        eg: BaseExceptionGroup[BaseException],
        reason: str,
    ) -> None:
        """Log a TaskGroup failure, preserving sibling exception info."""
        logger.error(
            LLM_STRATEGY_ERROR,
            agent_id=agent_id,
            reason=reason,
            exception_count=len(eg.exceptions),
            exception_types=[type(e).__name__ for e in eg.exceptions],
        )
