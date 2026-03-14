"""Model mapping strategy protocol.

Defines the pluggable interface for mapping seniority levels to
LLM model identifiers.
"""

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from synthorg.core.agent import AgentIdentity
    from synthorg.core.enums import SeniorityLevel


@runtime_checkable
class ModelMappingStrategy(Protocol):
    """Protocol for mapping seniority to LLM models.

    Implementations determine which model an agent should use
    after a seniority level change.
    """

    @property
    def name(self) -> str:
        """Strategy name identifier."""
        ...

    def resolve_model(
        self,
        *,
        agent_identity: AgentIdentity,
        new_level: SeniorityLevel,
    ) -> str | None:
        """Resolve the model for an agent at a new seniority level.

        Args:
            agent_identity: The agent's current identity.
            new_level: The new seniority level.

        Returns:
            New model_id, or None if no change needed.
        """
        ...
