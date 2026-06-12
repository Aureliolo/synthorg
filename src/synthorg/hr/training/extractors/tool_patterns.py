"""Tool pattern content extractor.

Queries the tool invocation tracker for usage history from source
agents, aggregates by tool name, and produces summary items.
"""

import asyncio
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Final

from synthorg.core.collections import dedupe_preserving_order
from synthorg.core.types import NotBlankStr
from synthorg.hr.seniority import SeniorityLevel
from synthorg.hr.training.models import ContentType, TrainingItem
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.training import (
    HR_TRAINING_EXTRACTION_FAILED,
    HR_TRAINING_EXTRACTOR_CONFIG_INVALID,
    HR_TRAINING_ITEMS_EXTRACTED,
)
from synthorg.tools.invocation_record import ToolInvocationRecord
from synthorg.tools.invocation_tracker import ToolInvocationTracker

logger = get_logger(__name__)

_DEFAULT_LOOKBACK_DAYS: Final[int] = 90


class ToolPatternExtractor:
    """Extract tool usage patterns from senior agents.

    Queries the invocation tracker for tool usage history,
    aggregates by tool name, computes success rates, and
    produces summary training items.

    Args:
        tracker: Tool invocation tracker.
        lookback_days: Number of days to look back (must be positive).
    """

    def __init__(
        self,
        *,
        tracker: ToolInvocationTracker,
        lookback_days: int = _DEFAULT_LOOKBACK_DAYS,
    ) -> None:
        if lookback_days <= 0:
            msg = f"lookback_days must be a positive integer, got {lookback_days}"
            logger.warning(
                HR_TRAINING_EXTRACTOR_CONFIG_INVALID,
                extractor_type="tool_patterns",
                reason=msg,
                lookback_days=lookback_days,
            )
            raise ValueError(msg)
        self._tracker = tracker
        self._lookback_days = lookback_days

    @property
    def content_type(self) -> ContentType:
        """The content type this extractor produces."""
        return ContentType.TOOL_PATTERNS

    async def extract(
        self,
        *,
        source_agent_ids: tuple[NotBlankStr, ...],
        new_agent_role: NotBlankStr,  # noqa: ARG002
        new_agent_level: SeniorityLevel,  # noqa: ARG002
    ) -> tuple[TrainingItem, ...]:
        """Extract tool usage patterns from source agents in parallel.

        Args:
            source_agent_ids: Senior agents to extract from.
            new_agent_role: Role of the new hire (unused).
            new_agent_level: Seniority level (unused).

        Returns:
            Aggregated tool pattern training items.
        """
        if not source_agent_ids:
            return ()

        now = datetime.now(UTC)
        start = now - timedelta(days=self._lookback_days)

        # Deduplicate while preserving order to avoid redundant fetches.
        unique_ids = list(dedupe_preserving_order(source_agent_ids))

        # Fetch records for each agent concurrently.
        async with asyncio.TaskGroup() as tg:
            tasks = [
                tg.create_task(self._fetch_for_agent(agent_id, start, now))
                for agent_id in unique_ids
            ]

        # Aggregate across all source agents after retrieval completes.
        tool_stats: dict[str, dict[str, int]] = defaultdict(
            lambda: {"total": 0, "success": 0},
        )
        source_agents_by_tool: dict[str, set[str]] = defaultdict(set)

        for task in tasks:
            agent_id, records = task.result()
            for record in records:
                tool_name = str(record.tool_name)
                tool_stats[tool_name]["total"] += 1
                if record.is_success:
                    tool_stats[tool_name]["success"] += 1
                source_agents_by_tool[tool_name].add(str(agent_id))

        items: list[TrainingItem] = []
        for tool_name, stats in sorted(tool_stats.items()):
            total = stats["total"]
            success = stats["success"]
            rate = success / total if total > 0 else 0.0
            rate_pct = round(rate * 100)
            agents = source_agents_by_tool[tool_name]

            content = (
                f"Tool: {tool_name} | "
                f"Usage: {total} invocations | "
                f"Success rate: {rate_pct}% ({success}/{total}) | "
                f"Used by {len(agents)} senior agent(s)"
            )

            # Deterministic source_agent_id: lexicographically smallest
            # contributing agent so the same aggregate maps to the same
            # provenance across runs.
            representative = sorted(agents)[0]

            items.append(
                TrainingItem(
                    source_agent_id=representative,
                    content_type=ContentType.TOOL_PATTERNS,
                    content=content,
                    created_at=datetime.now(UTC),
                ),
            )

        logger.info(
            HR_TRAINING_ITEMS_EXTRACTED,
            content_type="tool_patterns",
            agent_count=len(unique_ids),
            item_count=len(items),
        )
        return tuple(items)

    async def _fetch_for_agent(
        self,
        agent_id: NotBlankStr,
        start: datetime,
        end: datetime,
    ) -> tuple[NotBlankStr, tuple[ToolInvocationRecord, ...]]:
        """Fetch invocation records for a single agent with error logging.

        Returns:
            Tuple ``(NotBlankStr, tuple[ToolInvocationRecord, ...])``.

        Raises:
            Exception: Raised when the relevant invariant fails.
        """
        try:
            records = await self._tracker.get_records(
                agent_id=str(agent_id),
                start=start,
                end=end,
            )
        except Exception as exc:
            logger.warning(
                HR_TRAINING_EXTRACTION_FAILED,
                content_type="tool_patterns",
                agent_id=str(agent_id),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise
        return agent_id, tuple(records)
