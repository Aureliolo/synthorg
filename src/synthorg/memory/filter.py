"""Memory filter strategies for non-inferable principle enforcement.

Filters scored memories before injection into agent prompts.  The
``TagBasedMemoryFilter`` (initial D23 implementation) retains only
memories tagged with ``"non-inferable"``; the ``PassthroughMemoryFilter``
is a no-op for backward compatibility and testing.

Both satisfy the ``MemoryFilterStrategy`` runtime-checkable protocol.
"""

from typing import TYPE_CHECKING, Final, Protocol, runtime_checkable

from synthorg.observability import get_logger
from synthorg.observability.events.memory import (
    MEMORY_FILTER_APPLIED,
    MEMORY_FILTER_INIT,
)

if TYPE_CHECKING:
    from synthorg.memory.ranking import ScoredMemory

logger = get_logger(__name__)

NON_INFERABLE_TAG: Final[str] = "non-inferable"


@runtime_checkable
class MemoryFilterStrategy(Protocol):
    """Protocol for filtering scored memories before prompt injection."""

    def filter_for_injection(
        self,
        memories: tuple[ScoredMemory, ...],
    ) -> tuple[ScoredMemory, ...]:
        """Filter memories suitable for injection.

        Args:
            memories: Ranked scored memories from the retrieval pipeline.

        Returns:
            Subset of memories that pass the filter.
        """
        ...

    @property
    def strategy_name(self) -> str:
        """Human-readable name of the filter strategy."""
        ...


class TagBasedMemoryFilter:
    """Filter that retains only memories with a required tag.

    The default required tag is ``"non-inferable"`` per D23.  Memories
    whose ``entry.metadata.tags`` do not contain the required tag are
    excluded from prompt injection.

    Args:
        required_tag: Tag that must be present for a memory to pass.
    """

    def __init__(self, required_tag: str = NON_INFERABLE_TAG) -> None:
        if not isinstance(required_tag, str) or not required_tag.strip():
            msg = "required_tag must be a non-empty string"
            raise ValueError(msg)
        self._required_tag = required_tag.strip()
        logger.debug(
            MEMORY_FILTER_INIT,
            strategy=self.strategy_name,
            required_tag=required_tag,
        )

    def filter_for_injection(
        self,
        memories: tuple[ScoredMemory, ...],
    ) -> tuple[ScoredMemory, ...]:
        """Return only memories containing the required tag.

        Args:
            memories: Ranked scored memories.

        Returns:
            Filtered tuple with only tagged memories.
        """
        retained = tuple(
            m for m in memories if self._required_tag in m.entry.metadata.tags
        )

        logger.info(
            MEMORY_FILTER_APPLIED,
            strategy=self.strategy_name,
            candidates=len(memories),
            retained=len(retained),
            required_tag=self._required_tag,
        )

        return retained

    @property
    def strategy_name(self) -> str:
        """Human-readable name of the filter strategy.

        Returns:
            ``"tag_based"``.
        """
        return "tag_based"


class PassthroughMemoryFilter:
    """No-op filter that returns all memories unchanged.

    Useful for backward compatibility and testing — all memories pass
    through without filtering.
    """

    def filter_for_injection(
        self,
        memories: tuple[ScoredMemory, ...],
    ) -> tuple[ScoredMemory, ...]:
        """Return all memories unchanged.

        Args:
            memories: Ranked scored memories.

        Returns:
            The input tuple unchanged.
        """
        logger.info(
            MEMORY_FILTER_APPLIED,
            strategy=self.strategy_name,
            candidates=len(memories),
            retained=len(memories),
        )
        return memories

    @property
    def strategy_name(self) -> str:
        """Human-readable name of the filter strategy.

        Returns:
            ``"passthrough"``.
        """
        return "passthrough"
