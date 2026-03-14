"""Trust strategy protocol.

Defines the pluggable interface for progressive trust strategies.
All trust strategies must implement this protocol.
"""

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from synthorg.core.types import NotBlankStr
    from synthorg.hr.performance.models import AgentPerformanceSnapshot
    from synthorg.security.trust.models import TrustEvaluationResult, TrustState


@runtime_checkable
class TrustStrategy(Protocol):
    """Protocol for progressive trust evaluation strategies.

    Implementations compute trust evaluations from agent performance
    data and maintain per-agent trust state.
    """

    @property
    def name(self) -> str:
        """Strategy name identifier."""
        ...

    async def evaluate(
        self,
        *,
        agent_id: NotBlankStr,
        current_state: TrustState,
        snapshot: AgentPerformanceSnapshot,
    ) -> TrustEvaluationResult:
        """Evaluate an agent's trust level.

        Args:
            agent_id: Agent to evaluate.
            current_state: Current trust state.
            snapshot: Agent performance snapshot.

        Returns:
            Evaluation result with recommended level.
        """
        ...

    def initial_state(self, *, agent_id: NotBlankStr) -> TrustState:
        """Create the initial trust state for a newly registered agent.

        Args:
            agent_id: Agent identifier.

        Returns:
            Initial trust state.
        """
        ...
