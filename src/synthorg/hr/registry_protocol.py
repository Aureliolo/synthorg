"""Structural contract for the agent registry service.

Engine, coordination, budget-forecast, and memory-propagation consumers depend
on the registry's lookup and identity-lifecycle surface, not on the concrete
:class:`~synthorg.hr.registry.AgentRegistryService`. Annotating against this
``@runtime_checkable`` Protocol lets them hold the registry structurally, so the
real class and the autospec test doubles satisfy it. All signature types resolve
at runtime.
"""

from typing import Protocol, runtime_checkable

from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.core.agent import AgentIdentity
from synthorg.core.clock import Clock
from synthorg.core.types import NotBlankStr
from synthorg.hr.enums import AgentStatus
from synthorg.security.autonomy.models import AutonomyUpdate, AutonomyUpdateResult
from synthorg.versioning.service import VersioningService


@runtime_checkable
class AgentRegistryProtocol(Protocol):
    """Agent lookup plus the identity and autonomy lifecycle surface."""

    @property
    def has_versioning(self) -> bool:
        """Whether a versioning service is bound."""
        ...

    def bind_versioning(
        self,
        versioning: VersioningService[AgentIdentity],
    ) -> None:
        """Attach a versioning service after construction."""
        ...

    async def clear(self) -> None:
        """Drop every registered identity (test/reset support)."""
        ...

    async def register(
        self,
        identity: AgentIdentity,
        *,
        saved_by: str = "system",
    ) -> None:
        """Register a new agent identity."""
        ...

    async def unregister(self, agent_id: NotBlankStr) -> AgentIdentity:
        """Remove and return a registered identity."""
        ...

    async def get(self, agent_id: NotBlankStr) -> AgentIdentity | None:
        """Look up an identity by id, or ``None`` if absent."""
        ...

    async def get_by_name(self, name: NotBlankStr) -> AgentIdentity | None:
        """Look up an identity by name, or ``None`` if absent."""
        ...

    async def get_by_names(
        self,
        names: tuple[NotBlankStr, ...],
    ) -> tuple[AgentIdentity | None, ...]:
        """Batch lookup by name, preserving order with ``None`` for misses."""
        ...

    async def list_active(self) -> tuple[AgentIdentity, ...]:
        """List all active agent identities."""
        ...

    async def list_by_department(
        self,
        department: NotBlankStr,
    ) -> tuple[AgentIdentity, ...]:
        """List active identities in a department."""
        ...

    async def update_status(
        self,
        agent_id: NotBlankStr,
        status: AgentStatus,
    ) -> AgentIdentity:
        """Set an agent's lifecycle status."""
        ...

    async def update_identity(
        self,
        agent_id: NotBlankStr,
        **updates: object,
    ) -> AgentIdentity:
        """Update allowlisted identity fields via ``model_copy``."""
        ...

    async def evolve_identity(
        self,
        agent_id: NotBlankStr,
        evolved_identity: AgentIdentity,
        *,
        evolution_rationale: str,
    ) -> AgentIdentity:
        """Replace an identity wholesale after evolution guards pass."""
        ...

    async def apply_identity_update(
        self,
        agent_id: NotBlankStr,
        updates: dict[str, object],
        *,
        saved_by: str,
    ) -> AgentIdentity:
        """Mutate any allowed identity field and snapshot a version."""
        ...

    async def update_autonomy(
        self,
        agent_id: NotBlankStr,
        update: AutonomyUpdate,
        *,
        approval_store: ApprovalStoreProtocol | None = None,
        clock: Clock | None = None,
    ) -> AutonomyUpdateResult:
        """Request an autonomy change, routing through the approval flow."""
        ...

    async def agent_count(self) -> int:
        """Return the number of registered identities."""
        ...
