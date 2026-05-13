"""Test-only no-op trust strategy.

Replaces the production ``DisabledTrustStrategy`` that was deleted by
#1891 candidate 6. Production now skips ``TrustService`` construction
entirely when trust is disabled (``build_trust_strategy`` returns
``None``), but tests that exercise ``TrustService`` orchestration logic
still need a strategy that satisfies the protocol without doing real
evaluation work.
"""

from synthorg.core.enums import ToolAccessLevel
from synthorg.core.types import NotBlankStr
from synthorg.hr.performance.models import AgentPerformanceSnapshot
from synthorg.security.trust.models import TrustEvaluationResult, TrustState


class NoOpTrustStrategy:
    """Static-level strategy that recommends no change.

    Used only by tests; production code uses one of the three real
    strategies (weighted / per_category / milestone) or builds no
    :class:`TrustService` at all.
    """

    def __init__(
        self,
        *,
        initial_level: ToolAccessLevel = ToolAccessLevel.STANDARD,
    ) -> None:
        self._initial_level = initial_level

    @property
    def name(self) -> str:
        """Strategy name identifier."""
        return "noop"

    async def evaluate(
        self,
        *,
        agent_id: NotBlankStr,
        current_state: TrustState,
        snapshot: AgentPerformanceSnapshot,
    ) -> TrustEvaluationResult:
        """Return the current level unchanged."""
        return TrustEvaluationResult(
            agent_id=agent_id,
            recommended_level=current_state.global_level,
            current_level=current_state.global_level,
            requires_human_approval=False,
            details="noop test strategy",
            strategy_name="noop",
        )

    def initial_state(self, *, agent_id: NotBlankStr) -> TrustState:
        """Create initial state with the configured level."""
        return TrustState(
            agent_id=agent_id,
            global_level=self._initial_level,
        )
