"""Protocol for cross-agent procedural memory propagation.

Defines the interface for pluggable propagation strategies that
determine how learned procedural memories are shared between agents.
"""

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from synthorg.core.types import NotBlankStr
from synthorg.memory.models import MemoryEntry

if TYPE_CHECKING:
    from synthorg.hr.registry import AgentRegistryService
    from synthorg.memory.protocol import MemoryBackend


# propagation/factory.py with 3 impls (No/RoleScoped/DepartmentScoped
# propagation); pluggable seam.
@runtime_checkable
class PropagationStrategy(Protocol):
    """Strategy for propagating procedural memories across agents.

    Implementations include no-propagation (baseline), role-scoped,
    and department-scoped propagation.
    """

    @property
    def name(self) -> str:
        """Human-readable strategy name."""
        ...

    async def propagate(
        self,
        *,
        source_agent_id: NotBlankStr,
        memory_entry: MemoryEntry,
        registry: AgentRegistryService,
        memory_backend: MemoryBackend,
    ) -> int:
        """Propagate a memory entry to other agents.

        Args:
            source_agent_id: Agent that originated the memory.
            memory_entry: The procedural memory to propagate.
            registry: Agent registry for finding target agents.
            memory_backend: Memory backend for storing copies.

        Returns:
            Number of agents the memory was propagated to.
        """
        ...
