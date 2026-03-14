"""MemoryCapabilities protocol — capability discovery.

Backends that implement ``MemoryCapabilities`` expose what features
they support, enabling runtime capability checks.
"""

from typing import Protocol, runtime_checkable

from synthorg.core.enums import MemoryCategory  # noqa: TC001


@runtime_checkable
class MemoryCapabilities(Protocol):
    """Capability discovery for memory backends.

    Attributes:
        supported_categories: Memory categories this backend supports.
        supports_graph: Whether graph-based memory is available.
        supports_temporal: Whether temporal tracking is available.
        supports_vector_search: Whether vector/semantic search is
            available.
        supports_shared_access: Whether cross-agent shared memory is
            available.
        max_memories_per_agent: Maximum memories per agent, or ``None``
            for unlimited.
    """

    @property
    def supported_categories(self) -> frozenset[MemoryCategory]:
        """Memory categories this backend supports."""
        ...

    @property
    def supports_graph(self) -> bool:
        """Whether graph-based memory is available."""
        ...

    @property
    def supports_temporal(self) -> bool:
        """Whether temporal tracking is available."""
        ...

    @property
    def supports_vector_search(self) -> bool:
        """Whether vector/semantic search is available."""
        ...

    @property
    def supports_shared_access(self) -> bool:
        """Whether cross-agent shared memory is available."""
        ...

    @property
    def max_memories_per_agent(self) -> int | None:
        """Maximum memories per agent, or ``None`` for unlimited."""
        ...
