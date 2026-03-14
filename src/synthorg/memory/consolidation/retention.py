"""Retention enforcer for memory lifecycle management.

Deletes memories that have exceeded their per-category retention
period.
"""

from datetime import UTC, datetime, timedelta

from synthorg.core.enums import MemoryCategory
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.memory.consolidation.config import RetentionConfig  # noqa: TC001
from synthorg.memory.models import MemoryQuery
from synthorg.memory.protocol import MemoryBackend  # noqa: TC001
from synthorg.observability import get_logger
from synthorg.observability.events.consolidation import (
    RETENTION_CLEANUP_COMPLETE,
    RETENTION_CLEANUP_FAILED,
    RETENTION_CLEANUP_START,
    RETENTION_DELETE_SKIPPED,
)

logger = get_logger(__name__)


class RetentionEnforcer:
    """Enforces per-category memory retention policies.

    Queries for memories older than the configured retention period
    and deletes them from the backend.

    Args:
        config: Retention configuration with per-category rules.
        backend: Memory backend for querying and deleting.
    """

    def __init__(
        self,
        *,
        config: RetentionConfig,
        backend: MemoryBackend,
    ) -> None:
        self._config = config
        self._backend = backend
        self._categories_to_check = self._build_categories_to_check(config)

    @staticmethod
    def _build_categories_to_check(
        config: RetentionConfig,
    ) -> tuple[tuple[MemoryCategory, int], ...]:
        """Pre-compute (category, retention_days) pairs at construction.

        Includes explicit per-category rules and fills in any remaining
        categories with the default retention (if set).

        Args:
            config: Retention configuration with per-category rules.

        Returns:
            Tuple of category/retention_days pairs.
        """
        category_days = {rule.category: rule.retention_days for rule in config.rules}
        result: list[tuple[MemoryCategory, int]] = []
        for category in MemoryCategory:
            days = category_days.get(category)
            if days is not None:
                result.append((category, days))
            elif config.default_retention_days is not None:
                result.append((category, config.default_retention_days))
        return tuple(result)

    async def cleanup_expired(
        self,
        agent_id: NotBlankStr,
        now: datetime | None = None,
    ) -> int:
        """Delete memories that have exceeded their retention period.

        Processes each category independently so that a failure in one
        category does not prevent cleanup of the remaining categories.

        Processes up to 1000 expired entries per category per
        invocation.  Multiple calls may be needed for categories
        with a large backlog.

        Args:
            agent_id: Agent whose memories to clean up.
            now: Current time (defaults to UTC now).

        Returns:
            Number of expired memories deleted.
        """
        if now is None:
            now = datetime.now(UTC)

        logger.info(RETENTION_CLEANUP_START, agent_id=agent_id)
        total_deleted = 0

        for category, retention_days in self._categories_to_check:
            try:
                cutoff = now - timedelta(days=retention_days)
                query = MemoryQuery(
                    categories=frozenset({category}),
                    until=cutoff,
                    limit=1000,
                )
                expired = await self._backend.retrieve(agent_id, query)
                for entry in expired:
                    deleted = await self._backend.delete(agent_id, entry.id)
                    if deleted:
                        total_deleted += 1
                    else:
                        logger.debug(
                            RETENTION_DELETE_SKIPPED,
                            agent_id=agent_id,
                            entry_id=entry.id,
                            category=category.value,
                        )
            except Exception as exc:
                logger.warning(
                    RETENTION_CLEANUP_FAILED,
                    agent_id=agent_id,
                    category=category.value,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                continue

        logger.info(
            RETENTION_CLEANUP_COMPLETE,
            agent_id=agent_id,
            deleted_count=total_deleted,
        )
        return total_deleted
