"""Department-scoped memory propagation strategy.

Shares memory with agents in the same department.
"""

from typing import TYPE_CHECKING, Final

from synthorg.memory.models import MemoryStoreRequest
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.procedural_memory import (
    PROCEDURAL_PROPAGATION_TARGET_FAILED,
)

if TYPE_CHECKING:
    from synthorg.core.types import NotBlankStr
    from synthorg.hr.registry import AgentRegistryService
    from synthorg.memory.models import MemoryEntry
    from synthorg.memory.protocol import MemoryBackend

logger = get_logger(__name__)
_DEFAULT_MAX_TARGETS: Final[int] = 10


class DepartmentScopedPropagation:
    """Propagate memories to agents in the same department.

    Attributes:
        max_targets: Maximum number of agents to propagate to
            (default 10).
    """

    def __init__(self, max_targets: int = _DEFAULT_MAX_TARGETS) -> None:
        """Initialize department-scoped propagation strategy.

        Args:
            max_targets: Maximum target agents (default 10).
        """
        self.max_targets = max(1, max_targets)

    @property
    def name(self) -> str:
        """Human-readable strategy name."""
        return "department_scoped"

    async def propagate(
        self,
        *,
        source_agent_id: NotBlankStr,
        memory_entry: MemoryEntry,
        registry: AgentRegistryService,
        memory_backend: MemoryBackend,
    ) -> int:
        """Propagate memory to agents in same department.

        Args:
            source_agent_id: Agent that originated the memory.
            memory_entry: The procedural memory to propagate.
            registry: Agent registry for finding target agents.
            memory_backend: Memory backend for storing copies.

        Returns:
            Number of agents the memory was propagated to.
        """
        # Get source agent to find its department
        source_agent = await registry.get(source_agent_id)
        if source_agent is None:
            return 0

        # Get all agents in the same department
        target_agents = await registry.list_by_department(
            source_agent.department,
        )

        # Filter out source agent.
        # NOTE: list_by_department returns all statuses; callers should
        # use list_active + department filter if inactive exclusion is needed.
        target_agents = tuple(
            a for a in target_agents if str(a.id) != str(source_agent.id)
        )

        # Limit to max_targets
        target_agents = target_agents[: self.max_targets]

        # Propagate to each target
        count = 0
        for target in target_agents:
            try:
                # Create propagation tag
                tag = f"propagated:{source_agent_id}"
                tags = (*memory_entry.metadata.tags, tag)

                # Store copy with propagation tag
                store_request = MemoryStoreRequest(
                    category=memory_entry.category,
                    namespace=memory_entry.namespace,
                    content=memory_entry.content,
                    metadata=memory_entry.metadata.model_copy(
                        update={"tags": tags},
                    ),
                    expires_at=memory_entry.expires_at,
                )
                await memory_backend.store(str(target.id), store_request)
                count += 1
            except Exception as exc:
                logger.warning(
                    PROCEDURAL_PROPAGATION_TARGET_FAILED,
                    target_id=str(target.id),
                    source_id=str(source_agent_id),
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )

        return count
