# module-kind: code
"""Memory filter strategies for non-inferable principle enforcement.

Filters scored memories before injection into agent prompts.  The
``TagBasedMemoryFilter`` retains only memories tagged with
``"non-inferable"``; the ``PassthroughMemoryFilter`` is an explicit
no-op.  Both satisfy the ``MemoryFilterStrategy`` runtime-checkable
protocol and are selected by :func:`build_memory_filter` via the
``memory_filter_strategy`` discriminator.
"""

from typing import Final, Literal, Protocol, assert_never, runtime_checkable

from synthorg.memory.ranking import ScoredMemory
from synthorg.observability import get_logger
from synthorg.observability.events.memory import (
    MEMORY_FILTER_APPLIED,
    MEMORY_FILTER_INIT,
)

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
    """No-op filter that injects every ranked memory unchanged.

    Selected by ``memory_filter_strategy="passthrough"``.  Unlike a
    ``None`` filter (the default ``off`` path), choosing passthrough is
    an explicit opt-in that records the decision in the logs, so an
    operator can distinguish "no filter configured" from "filtering
    deliberately disabled".
    """

    def __init__(self) -> None:
        logger.debug(
            MEMORY_FILTER_INIT,
            strategy=self.strategy_name,
        )

    def filter_for_injection(
        self,
        memories: tuple[ScoredMemory, ...],
    ) -> tuple[ScoredMemory, ...]:
        """Return all memories unchanged.

        Args:
            memories: Ranked scored memories.

        Returns:
            The input tuple, unmodified.
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


MemoryFilterStrategyName = Literal["off", "tag_based", "passthrough"]


def build_memory_filter(
    strategy: MemoryFilterStrategyName,
) -> MemoryFilterStrategy | None:
    """Resolve the post-ranking memory filter from the discriminator.

    ``off`` applies no filter; ``tag_based`` retains only
    non-inferable-tagged memories; ``passthrough`` injects every ranked
    memory.

    Args:
        strategy: The ``memory_filter_strategy`` discriminator.

    Returns:
        The selected filter, or ``None`` when ``off`` (no filtering).
    """
    match strategy:
        case "off":
            return None
        case "tag_based":
            return TagBasedMemoryFilter()
        case "passthrough":
            return PassthroughMemoryFilter()
        case _:  # pragma: no cover
            assert_never(strategy)
