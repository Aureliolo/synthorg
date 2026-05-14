"""Trust strategy protocol.

Defines the pluggable interface for progressive trust strategies.
All trust strategies must implement this protocol.
"""

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from synthorg.core.types import NotBlankStr
    from synthorg.hr.performance.models import AgentPerformanceSnapshot
    from synthorg.security.trust.models import TrustEvaluationResult, TrustState


# Pluggable trust subsystem: 3 impls (Weighted / Milestone / PerCategory).
# DISABLED is a config-only sentinel; build_trust_strategy returns None
# so the caller skips TrustService construction entirely.
@runtime_checkable
class TrustStrategy(Protocol):
    """Protocol for progressive trust evaluation strategies.

    Implementations compute trust evaluations from agent performance
    data and maintain per-agent trust state.
    """

    @property
    def name(self) -> str:
        """Strategy name identifier.

        Used for log attribution and as
        :attr:`TrustEvaluationResult.strategy_name`. MUST be a
        non-empty, lower_snake_case string that is unique across the
        strategies registered in :mod:`synthorg.security.trust.factory`.
        Duplicate names would silently merge their attribution; an
        empty name would emit blank fields downstream.
        """
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
