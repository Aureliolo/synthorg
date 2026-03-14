"""Memory archival strategy protocol.

Defines the interface for pluggable strategies that handle
agent memory archival during offboarding (D10).
"""

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.enums import SeniorityLevel  # noqa: TC001
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.memory.consolidation.archival import ArchivalStore  # noqa: TC001
from synthorg.memory.org.protocol import OrgMemoryBackend  # noqa: TC001
from synthorg.memory.protocol import MemoryBackend  # noqa: TC001


class ArchivalResult(BaseModel):
    """Result of a memory archival operation.

    Attributes:
        agent_id: Agent whose memories were archived.
        total_archived: Number of memories archived.
        promoted_to_org: Number promoted to org memory.
        hot_store_cleaned: Whether the hot store was cleaned.
        strategy_name: Name of the archival strategy used.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    agent_id: NotBlankStr = Field(description="Agent whose memories were archived")
    total_archived: int = Field(ge=0, description="Memories archived")
    promoted_to_org: int = Field(ge=0, description="Promoted to org memory")
    hot_store_cleaned: bool = Field(description="Hot store cleaned")
    strategy_name: NotBlankStr = Field(description="Archival strategy used")


@runtime_checkable
class MemoryArchivalStrategy(Protocol):
    """Strategy for archiving agent memories during offboarding.

    Implementations handle the complete memory archival pipeline:
    retrieving from hot store, archiving to cold store, optionally
    promoting to org memory, and cleaning up the hot store.
    """

    @property
    def name(self) -> str:
        """Human-readable strategy name."""
        ...

    async def archive(
        self,
        *,
        agent_id: NotBlankStr,
        memory_backend: MemoryBackend,
        archival_store: ArchivalStore,
        org_memory_backend: OrgMemoryBackend | None = None,
        agent_seniority: SeniorityLevel | None = None,
    ) -> ArchivalResult:
        """Archive all memories for a departing agent.

        Args:
            agent_id: Agent whose memories to archive.
            memory_backend: Hot memory store.
            archival_store: Cold archival storage.
            org_memory_backend: Optional org memory for promotion.
            agent_seniority: Seniority level of the departing agent.
                Required for org memory promotion (skipped if None).

        Returns:
            Result of the archival operation.

        Raises:
            MemoryArchivalError: If retrieval from hot store fails.
        """
        ...
