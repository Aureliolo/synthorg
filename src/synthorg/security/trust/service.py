"""Trust service orchestrator.

Central service for managing progressive trust state, evaluation,
and trust level changes for agents.
"""

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from synthorg.core.enums import ApprovalRiskLevel, ToolAccessLevel
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.trust import (
    TRUST_APPROVAL_REQUIRED,
    TRUST_APPROVAL_STORE_MISSING,
    TRUST_ELEVATED_GATE_ENFORCED,
    TRUST_EVALUATE_COMPLETE,
    TRUST_EVALUATE_FAILED,
    TRUST_EVALUATE_START,
    TRUST_INITIALIZED,
    TRUST_LEVEL_CHANGED,
)
from synthorg.security.trust.enums import TrustChangeReason
from synthorg.security.trust.errors import TrustEvaluationError
from synthorg.security.trust.models import (
    TrustChangeRecord,
    TrustEvaluationResult,
    TrustState,
)

if TYPE_CHECKING:
    from synthorg.api.approval_store import ApprovalStore
    from synthorg.hr.performance.models import AgentPerformanceSnapshot
    from synthorg.security.trust.config import TrustConfig
    from synthorg.security.trust.protocol import TrustStrategy

logger = get_logger(__name__)


class TrustService:
    """Orchestrates progressive trust evaluation and state management.

    Delegates trust evaluation to a pluggable strategy, manages
    per-agent trust state, and enforces the security invariant
    that standard-to-elevated always requires human approval.

    Args:
        strategy: Trust evaluation strategy.
        config: Trust configuration.
        approval_store: Optional approval store for human approval gates.
    """

    def __init__(
        self,
        *,
        strategy: TrustStrategy,
        config: TrustConfig,
        approval_store: ApprovalStore | None = None,
    ) -> None:
        self._strategy = strategy
        self._config = config
        self._approval_store = approval_store
        self._trust_states: dict[str, TrustState] = {}
        self._change_history: dict[str, list[TrustChangeRecord]] = {}

    def initialize_agent(self, agent_id: NotBlankStr) -> TrustState:
        """Create initial trust state for a new agent.

        Args:
            agent_id: Agent identifier.

        Returns:
            Initial trust state with created_at timestamp.
        """
        now = datetime.now(UTC)
        state = self._strategy.initial_state(agent_id=agent_id)
        state = state.model_copy(update={"created_at": now})
        self._trust_states[str(agent_id)] = state
        self._change_history.setdefault(str(agent_id), [])

        logger.info(
            TRUST_INITIALIZED,
            agent_id=agent_id,
            level=state.global_level.value,
        )
        return state

    async def evaluate_agent(
        self,
        agent_id: NotBlankStr,
        snapshot: AgentPerformanceSnapshot,
    ) -> TrustEvaluationResult:
        """Evaluate an agent's trust level.

        Args:
            agent_id: Agent to evaluate.
            snapshot: Agent performance snapshot.

        Returns:
            Evaluation result with recommended level.

        Raises:
            TrustEvaluationError: If agent not initialized or
                evaluation fails.
        """
        key = str(agent_id)
        state = self._trust_states.get(key)
        if state is None:
            msg = f"Agent {agent_id!r} not initialized for trust tracking"
            logger.warning(
                TRUST_EVALUATE_FAILED,
                agent_id=agent_id,
                error=msg,
            )
            raise TrustEvaluationError(msg)

        logger.debug(
            TRUST_EVALUATE_START,
            agent_id=agent_id,
            strategy=self._strategy.name,
        )

        result = await self._strategy.evaluate(
            agent_id=agent_id,
            current_state=state,
            snapshot=snapshot,
        )

        # Defense-in-depth: enforce elevated gate
        result = self._enforce_elevated_gate(result)

        # Update last_evaluated_at
        now = datetime.now(UTC)
        updated_state = state.model_copy(
            update={"last_evaluated_at": now},
        )
        self._trust_states[key] = updated_state

        logger.debug(
            TRUST_EVALUATE_COMPLETE,
            agent_id=agent_id,
            recommended=result.recommended_level.value,
            should_change=result.should_change,
        )
        return result

    async def apply_trust_change(
        self,
        agent_id: NotBlankStr,
        result: TrustEvaluationResult,
    ) -> TrustChangeRecord | None:
        """Apply a trust level change based on evaluation result.

        If human approval is required, creates an approval item and
        returns None. The change is applied when the approval is granted.

        Args:
            agent_id: Agent whose trust to change.
            result: Evaluation result to apply.

        Returns:
            Change record if applied, None if awaiting approval.

        Raises:
            TrustEvaluationError: If agent not initialized.
        """
        if not result.should_change:
            return None

        key = str(agent_id)
        state = self._trust_states.get(key)
        if state is None:
            msg = f"Agent {agent_id!r} not initialized for trust tracking"
            logger.warning(
                TRUST_EVALUATE_FAILED,
                agent_id=agent_id,
                error=msg,
            )
            raise TrustEvaluationError(msg)

        # Defense-in-depth: re-enforce elevated gate on the result
        # to prevent crafted TrustEvaluationResults from bypassing
        # the mandatory human approval gate.
        result = self._enforce_elevated_gate(result)

        if result.requires_human_approval:
            await self._create_approval(agent_id, result)
            return None

        # Apply the change
        now = datetime.now(UTC)
        reason = self._infer_reason(result)

        record = TrustChangeRecord(
            agent_id=agent_id,
            old_level=state.global_level,
            new_level=result.recommended_level,
            reason=reason,
            timestamp=now,
            details=result.details,
        )

        # Update state — only set last_promoted_at on actual promotions
        from synthorg.security.trust.levels import (  # noqa: PLC0415
            TRUST_LEVEL_RANK,
        )

        is_promotion = TRUST_LEVEL_RANK.get(
            result.recommended_level, 0
        ) > TRUST_LEVEL_RANK.get(state.global_level, 0)
        state_update: dict[str, object] = {
            "global_level": result.recommended_level,
            "trust_score": result.score,
        }
        if is_promotion:
            state_update["last_promoted_at"] = now
        updated = state.model_copy(update=state_update)
        self._trust_states[key] = updated
        self._change_history.setdefault(key, []).append(record)

        logger.info(
            TRUST_LEVEL_CHANGED,
            agent_id=agent_id,
            old_level=record.old_level.value,
            new_level=record.new_level.value,
            reason=reason.value,
        )
        return record

    def get_trust_state(
        self,
        agent_id: NotBlankStr,
    ) -> TrustState | None:
        """Get current trust state for an agent.

        Args:
            agent_id: Agent identifier.

        Returns:
            Trust state, or None if not initialized.
        """
        return self._trust_states.get(str(agent_id))

    def get_change_history(
        self,
        agent_id: NotBlankStr,
    ) -> tuple[TrustChangeRecord, ...]:
        """Get trust change history for an agent.

        Args:
            agent_id: Agent identifier.

        Returns:
            Tuple of change records.
        """
        return tuple(self._change_history.get(str(agent_id), []))

    async def check_decay(
        self,
        agent_id: NotBlankStr,
        snapshot: AgentPerformanceSnapshot,
    ) -> TrustEvaluationResult:
        """Check for trust decay conditions.

        Delegates to evaluate_agent first, then updates the decay
        check timestamp.  The ordering ensures that the strategy's
        decay logic sees the *previous* last_decay_check_at value,
        not a freshly-updated one.

        Args:
            agent_id: Agent to check.
            snapshot: Agent performance snapshot.

        Returns:
            Evaluation result (may recommend demotion on decay).
        """
        result = await self.evaluate_agent(agent_id, snapshot)

        # Update decay check timestamp *after* evaluation
        key = str(agent_id)
        state = self._trust_states.get(key)
        if state is not None:
            now = datetime.now(UTC)
            updated = state.model_copy(
                update={"last_decay_check_at": now},
            )
            self._trust_states[key] = updated

        return result

    def _enforce_elevated_gate(
        self,
        result: TrustEvaluationResult,
    ) -> TrustEvaluationResult:
        """Defense-in-depth: force human approval for elevated promotion.

        If a strategy recommends ELEVATED but doesn't flag human
        approval, override it.
        """
        if (
            result.recommended_level == ToolAccessLevel.ELEVATED
            and result.current_level != ToolAccessLevel.ELEVATED
            and not result.requires_human_approval
        ):
            logger.warning(
                TRUST_ELEVATED_GATE_ENFORCED,
                agent_id=result.agent_id,
                strategy=result.strategy_name,
            )
            return result.model_copy(
                update={"requires_human_approval": True},
            )
        return result

    async def _create_approval(
        self,
        agent_id: NotBlankStr,
        result: TrustEvaluationResult,
    ) -> None:
        """Create an approval item for trust level promotion."""
        if self._approval_store is None:
            msg = (
                f"Cannot create trust approval for agent {agent_id!r}: "
                f"no approval store configured"
            )
            logger.warning(
                TRUST_APPROVAL_STORE_MISSING,
                agent_id=agent_id,
                recommended=result.recommended_level.value,
                error=msg,
            )
            raise TrustEvaluationError(msg)

        from synthorg.core.approval import ApprovalItem  # noqa: PLC0415

        now = datetime.now(UTC)
        approval = ApprovalItem(
            id=NotBlankStr(str(uuid4())),
            action_type="trust:promote",
            title=(
                f"Trust promotion: {result.current_level.value} "
                f"-> {result.recommended_level.value}"
            ),
            description=result.details or "Trust level change requested",
            requested_by=agent_id,
            risk_level=ApprovalRiskLevel.HIGH,
            created_at=now,
            metadata={
                "agent_id": str(agent_id),
                "current_level": result.current_level.value,
                "recommended_level": result.recommended_level.value,
            },
        )
        await self._approval_store.add(approval)

        logger.info(
            TRUST_APPROVAL_REQUIRED,
            agent_id=agent_id,
            approval_id=approval.id,
            recommended=result.recommended_level.value,
        )

    @staticmethod
    def _infer_reason(
        result: TrustEvaluationResult,
    ) -> TrustChangeReason:
        """Infer the change reason from the evaluation result.

        Distinguishes promotions from demotions: demotions use
        TRUST_DECAY, promotions use strategy-specific reasons.
        """
        from synthorg.security.trust.levels import (  # noqa: PLC0415
            TRUST_LEVEL_RANK,
        )

        is_demotion = TRUST_LEVEL_RANK.get(
            result.recommended_level, 0
        ) < TRUST_LEVEL_RANK.get(result.current_level, 0)
        if is_demotion:
            return TrustChangeReason.TRUST_DECAY

        strategy = result.strategy_name
        if strategy == "milestone":
            return TrustChangeReason.MILESTONE_ACHIEVED
        if strategy == "weighted" and result.score is not None:
            return TrustChangeReason.SCORE_THRESHOLD
        if strategy == "per_category":
            return TrustChangeReason.SCORE_THRESHOLD
        return TrustChangeReason.MANUAL
