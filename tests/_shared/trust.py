"""Test-only no-op trust strategy.

Production code skips ``TrustService`` construction when trust is
disabled (``build_trust_strategy`` returns ``None``), so there is no
production ``TrustStrategy`` implementation safe for use as a test
double. ``NoOpTrustStrategy`` satisfies the protocol with deterministic
no-op behaviour for tests that exercise ``TrustService`` orchestration
without driving real trust evaluation.
"""

from synthorg.core.tool_constraints import ToolAccessLevel
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
