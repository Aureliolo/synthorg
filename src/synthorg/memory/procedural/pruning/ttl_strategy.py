"""TTL-based memory pruning strategy.

Removes procedural memory entries that have exceeded a maximum age.
"""

from datetime import UTC, datetime
from typing import Final

from synthorg.core.types import NotBlankStr
from synthorg.memory.models import MemoryEntry
from synthorg.observability import get_logger

logger = get_logger(__name__)
_DEFAULT_MAX_AGE_DAYS: Final[int] = 90


class TtlPruningStrategy:
    """Remove entries older than max_age_days.

    Attributes:
        max_age_days: Maximum age in days for entries (default 90).
    """

    def __init__(self, max_age_days: int = _DEFAULT_MAX_AGE_DAYS) -> None:
        """Initialize TTL pruning strategy.

        Args:
            max_age_days: Maximum age in days (default 90).
        """
        self.max_age_days = max_age_days

    @property
    def name(self) -> str:
        """Human-readable strategy name."""
        return "ttl"

    async def prune(
        self,
        *,
        agent_id: NotBlankStr,  # noqa: ARG002
        entries: tuple[MemoryEntry, ...],
    ) -> tuple[str, ...]:
        """Identify entries exceeding max age for removal.

        Args:
            agent_id: Agent whose memories are being pruned.
            entries: Current procedural memory entries.

        Returns:
            Tuple of memory entry IDs to remove.
        """
        now = datetime.now(UTC)
        to_remove = []
        max_age_seconds = self.max_age_days * 86400

        for entry in entries:
            age_seconds = (now - entry.created_at).total_seconds()
            if age_seconds > max_age_seconds:
                to_remove.append(str(entry.id))

        return tuple(to_remove)
