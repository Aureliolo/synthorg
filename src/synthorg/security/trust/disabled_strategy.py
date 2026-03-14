"""Disabled trust strategy.

Static access level — agents keep their hire-time access level
with no automated trust evolution.
"""

from typing import TYPE_CHECKING

from synthorg.core.enums import ToolAccessLevel
from synthorg.observability import get_logger
from synthorg.observability.events.trust import (
    TRUST_EVALUATE_COMPLETE,
    TRUST_EVALUATE_START,
)
from synthorg.security.trust.models import TrustEvaluationResult, TrustState

if TYPE_CHECKING:
    from synthorg.core.types import NotBlankStr
    from synthorg.hr.performance.models import AgentPerformanceSnapshot

logger = get_logger(__name__)


class DisabledTrustStrategy:
    """Trust strategy that does nothing.

    Agents receive a fixed access level and it never changes.
    This is the default strategy.
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
        return "disabled"

    async def evaluate(
        self,
        *,
        agent_id: NotBlankStr,
        current_state: TrustState,
        snapshot: AgentPerformanceSnapshot,  # noqa: ARG002
    ) -> TrustEvaluationResult:
        """Return current level unchanged.

        Args:
            agent_id: Agent to evaluate.
            current_state: Current trust state.
            snapshot: Agent performance snapshot (unused).

        Returns:
            Evaluation result recommending no change.
        """
        logger.debug(
            TRUST_EVALUATE_START,
            agent_id=agent_id,
            strategy="disabled",
        )

        result = TrustEvaluationResult(
            agent_id=agent_id,
            recommended_level=current_state.global_level,
            current_level=current_state.global_level,
            requires_human_approval=False,
            details="Trust is disabled; access level is static",
            strategy_name="disabled",
        )

        logger.debug(
            TRUST_EVALUATE_COMPLETE,
            agent_id=agent_id,
            recommended=current_state.global_level.value,
        )
        return result

    def initial_state(self, *, agent_id: NotBlankStr) -> TrustState:
        """Create initial trust state with the configured level.

        Args:
            agent_id: Agent identifier.

        Returns:
            Initial trust state.
        """
        return TrustState(
            agent_id=agent_id,
            global_level=self._initial_level,
        )
