"""Orchestrator strategy protocol and implementations.

Defines the ``OrchestratorStrategy`` protocol for subtask selection
within the ``CentralizedDispatcher``.  Two implementations:

1. ``NaiveDispatchStrategy`` -- dispatches all subtasks (default)
2. ``MagenticDynamicSelectStrategy`` -- prioritizes stalled subtasks
"""

import re
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from synthorg.observability import get_logger

if TYPE_CHECKING:
    from synthorg.core.types import NotBlankStr
    from synthorg.engine.middleware.models import ProgressLedger

logger = get_logger(__name__)


@runtime_checkable
class OrchestratorStrategy(Protocol):
    """Protocol for subtask selection within centralized dispatch.

    Lives inside ``CentralizedDispatcher``, not as a separate
    topology.  Controls wave composition based on progress signals.
    """

    @property
    def name(self) -> str:
        """Strategy name for logging and configuration."""
        ...

    async def select_subtasks(
        self,
        subtask_ids: tuple[NotBlankStr, ...],
        progress: ProgressLedger | None,
    ) -> tuple[NotBlankStr, ...]:
        """Select and order subtasks for the next dispatch wave.

        Args:
            subtask_ids: All available subtask IDs.
            progress: Current progress ledger, if available.

        Returns:
            Ordered tuple of subtask IDs to dispatch.
        """
        ...


class NaiveDispatchStrategy:
    """Dispatches all subtasks in their original order (default).

    Current behavior: no reordering, no filtering.
    """

    @property
    def name(self) -> str:
        """Strategy name."""
        return "naive"

    async def select_subtasks(
        self,
        subtask_ids: tuple[NotBlankStr, ...],
        progress: ProgressLedger | None,  # noqa: ARG002
    ) -> tuple[NotBlankStr, ...]:
        """Return all subtasks in original order."""
        return subtask_ids


class MagenticDynamicSelectStrategy:
    """Prioritizes blocked/stalled subtasks for re-dispatch.

    Uses ``ProgressLedger.blocking_issues`` to identify subtasks
    that should be prioritized in the next wave.  Moves referenced
    subtask IDs to the front of the dispatch order.
    """

    @property
    def name(self) -> str:
        """Strategy name."""
        return "magentic_dynamic"

    async def select_subtasks(
        self,
        subtask_ids: tuple[NotBlankStr, ...],
        progress: ProgressLedger | None,
    ) -> tuple[NotBlankStr, ...]:
        """Prioritize blocked subtasks, then remaining in order.

        Returns:
            Subtask ids re-ordered to place blocked ids first; the
            original order when no blocking issues exist.
        """
        if progress is None or not progress.blocking_issues:
            return subtask_ids

        # Extract subtask IDs mentioned in blocking issues
        # Use word-boundary matching to prevent "task-1" matching "task-10"
        prioritized: list[str] = []
        for issue in progress.blocking_issues:
            for sid in subtask_ids:
                if (
                    re.search(rf"\b{re.escape(sid)}\b", issue)
                    and sid not in prioritized
                ):
                    prioritized.append(sid)

        # Remaining subtasks in original order
        remaining = [s for s in subtask_ids if s not in prioritized]

        return (*prioritized, *remaining)
