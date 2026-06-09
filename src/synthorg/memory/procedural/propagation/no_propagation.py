"""No-propagation strategy (baseline).

Memory entries are not shared across agents.
"""

from synthorg.core.types import NotBlankStr
from synthorg.hr.registry import AgentRegistryService
from synthorg.memory.models import MemoryEntry
from synthorg.memory.protocol import MemoryBackend
from synthorg.observability import get_logger

logger = get_logger(__name__)


class NoPropagation:
    """Baseline no-propagation strategy.

    Memory entries remain with their owning agent and are not shared
    with other agents.
    """

    @property
    def name(self) -> str:
        """Human-readable strategy name."""
        return "none"

    async def propagate(
        self,
        *,
        source_agent_id: NotBlankStr,  # noqa: ARG002
        memory_entry: MemoryEntry,  # noqa: ARG002
        registry: AgentRegistryService,  # noqa: ARG002
        memory_backend: MemoryBackend,  # noqa: ARG002
    ) -> int:
        """No-op propagation (baseline).

        Args:
            source_agent_id: Agent that originated the memory.
            memory_entry: The procedural memory to propagate.
            registry: Agent registry for finding target agents.
            memory_backend: Memory backend for storing copies.

        Returns:
            Always returns 0 (no agents reached).
        """
        return 0
