# module-kind: service
"""Scaling service -- orchestrates the scaling pipeline.

Trigger -> build context -> strategies (parallel) -> merge ->
guards (sequential) -> execute.
"""

import asyncio
from collections import deque
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Final, Protocol, runtime_checkable

from synthorg.core.agent import AgentIdentity
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.hr.enums import FiringReason
from synthorg.hr.hiring_service import HiringService
from synthorg.hr.models import FiringRequest
from synthorg.hr.offboarding_service import OffboardingService
from synthorg.hr.scaling.config import ScalingConfig
from synthorg.hr.scaling.context import ScalingContextBuilder
from synthorg.hr.scaling.enums import (
    ScalingActionType,
    ScalingOutcome,
    ScalingStrategyName,
)
from synthorg.hr.scaling.guards.composite import CompositeScalingGuard
from synthorg.hr.scaling.guards.conflict_resolver import ConflictResolver
from synthorg.hr.scaling.guards.cooldown import CooldownGuard
from synthorg.hr.scaling.guards.rate_limit import RateLimitGuard
from synthorg.hr.scaling.models import (
    ScalingActionRecord,
    ScalingContext,
    ScalingDecision,
)
from synthorg.hr.scaling.protocols import (
    ScalingGuard,
    ScalingStrategy,
    ScalingTrigger,
)
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.events.hr import (
    HR_SCALING_CYCLE_COMPLETE,
    HR_SCALING_CYCLE_STARTED,
    HR_SCALING_DECISIONS_MERGED,
    HR_SCALING_EXECUTED,
    HR_SCALING_EXECUTION_FAILED,
    HR_SCALING_SERVICE_VALIDATION_FAILED,
    HR_SCALING_STRATEGY_EVALUATED,
)


@runtime_checkable
class AgentLookup(Protocol):
    """Protocol for agent registry lookups."""

    async def get(self, agent_id: str) -> AgentIdentity | None:
        """Retrieve an agent by ID.

        Returns:
            The matching ``AgentIdentity``, or ``None`` when no match is found.
        """
        ...


logger = get_logger(__name__)

_MAX_HISTORY: Final[int] = 100

_STRATEGY_TO_FIRING_REASON: dict[str, FiringReason] = {
    ScalingStrategyName.BUDGET_CAP.value: FiringReason.BUDGET,
    ScalingStrategyName.PERFORMANCE_PRUNING.value: FiringReason.PERFORMANCE,
    ScalingStrategyName.WORKLOAD.value: FiringReason.MANUAL,
}


def _firing_reason_for(decision: ScalingDecision) -> FiringReason:
    """Map a decision's source strategy to the appropriate firing reason.

    Returns:
        Result of type ``FiringReason``.
    """
    return _STRATEGY_TO_FIRING_REASON.get(
        decision.source_strategy.value,
        FiringReason.PERFORMANCE,
    )


class ScalingService:
    """Orchestrates the scaling pipeline.

    Pipeline:
    1. Build ScalingContext via context_builder.
    2. Run all strategies in parallel with per-strategy error isolation.
    3. Merge + deduplicate decisions.
    4. Apply guard chain sequentially (conflict -> cooldown -> rate
       limit -> approval gate).
    5. Return filtered decisions ready for execution.

    Execution of decisions is performed by ``execute_decisions``, which
    dispatches HIRE decisions to ``HiringService.create_request`` and
    PRUNE decisions to ``OffboardingService.offboard``. After execution,
    cooldown and rate limit guards are updated via ``record_action``
    so repeated evaluations respect the recent actions.

    Args:
        strategies: Enabled scaling strategies.
        trigger: Optional trigger (None = always evaluate).
        guard: Guard chain to filter decisions.
        context_builder: Builds ScalingContext from signals.
        config: Scaling configuration.
        hiring_service: Optional hiring service for HIRE execution.
        offboarding_service: Optional offboarding service for PRUNE
            execution.
    """

    def __init__(  # noqa: PLR0913
        self,
        *,
        strategies: tuple[ScalingStrategy, ...],
        trigger: ScalingTrigger | None = None,
        guard: ScalingGuard,
        context_builder: ScalingContextBuilder,
        config: ScalingConfig,
        hiring_service: HiringService | None = None,
        offboarding_service: OffboardingService | None = None,
        agent_registry: AgentLookup | None = None,
    ) -> None:
        self._strategies = strategies
        self._trigger = trigger
        self._guard = guard
        self._context_builder = context_builder
        self._config = config
        self._hiring_service = hiring_service
        self._offboarding_service = offboarding_service
        self._agent_registry = agent_registry
        self._disabled_strategies: set[str] = set()
        self._last_context: ScalingContext | None = None
        self._recent_decisions: deque[ScalingDecision] = deque(
            maxlen=_MAX_HISTORY,
        )
        self._recent_actions: deque[ScalingActionRecord] = deque(
            maxlen=_MAX_HISTORY,
        )

    @property
    def strategies(self) -> tuple[ScalingStrategy, ...]:
        """Return the configured strategies (read-only)."""
        return self._strategies

    def set_strategy_enabled(
        self,
        name: str,
        *,
        enabled: bool,
    ) -> None:
        """Enable or disable a strategy at runtime."""
        if enabled:
            self._disabled_strategies.discard(name)
        else:
            self._disabled_strategies.add(name)

    def is_strategy_enabled(self, name: str) -> bool:
        """Check if a strategy is enabled at runtime.

        Returns:
            ``True`` when the predicate holds, ``False`` otherwise.
        """
        return name not in self._disabled_strategies

    async def _check_trigger(self) -> bool:
        """Check trigger and return True if evaluation should proceed.

        Returns:
            ``True`` if the operation succeeds, ``False`` otherwise.
        """
        if self._trigger is None:
            return True
        return await self._trigger.should_trigger()

    def update_priority_order(
        self,
        order: tuple[ScalingStrategyName, ...],
    ) -> None:
        """Update the conflict resolution priority order at runtime.

        Raises:
            ValueError: If order contains duplicates.
        """
        if len(order) != len(set(order)):
            msg = "priority_order must not contain duplicates"
            logger.warning(
                HR_SCALING_SERVICE_VALIDATION_FAILED,
                method="update_priority_order",
                reason="duplicate_strategy_names",
                order=[n.value for n in order],
            )
            raise ValueError(msg)
        self._config = self._config.model_copy(
            update={"priority_order": order},
        )
        priority_map = {name.value: idx for idx, name in enumerate(order)}
        guard = self._guard
        if isinstance(guard, CompositeScalingGuard):
            for inner in guard.get_guards():
                if isinstance(inner, ConflictResolver):
                    inner.set_priority(priority_map)
        elif isinstance(guard, ConflictResolver):
            guard.set_priority(priority_map)

    async def evaluate(
        self,
        *,
        agent_ids: tuple[NotBlankStr, ...],
        context_kwargs: Mapping[str, Mapping[str, object] | None] | None = None,
    ) -> tuple[ScalingDecision, ...]:
        """Run the evaluation pipeline.

        Args:
            agent_ids: Active agent IDs.
            context_kwargs: Extra kwargs passed to context builder.

        Returns:
            Filtered scaling decisions ready for execution.
        """
        if not self._config.enabled:
            return ()

        if not await self._check_trigger():
            return ()

        logger.info(HR_SCALING_CYCLE_STARTED, agent_count=len(agent_ids))

        # 1. Build context.
        context = await self._context_builder.build(
            agent_ids=agent_ids,
            **(context_kwargs or {}),
        )
        self._last_context = context

        # 2. Run enabled strategies in parallel.
        active = tuple(
            s for s in self._strategies if str(s.name) not in self._disabled_strategies
        )
        all_decisions: list[ScalingDecision] = []

        async def _safe_evaluate(
            s: ScalingStrategy,
        ) -> tuple[ScalingDecision, ...]:
            """Safe evaluate.

            Returns:
                Tuple of ``ScalingDecision``.

            Raises:
                CancelledError: If the related operation fails.
            """
            try:
                return await s.evaluate(context)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                reraise_critical(exc)
                logger.warning(
                    HR_SCALING_STRATEGY_EVALUATED,
                    strategy=str(s.name),
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                    decisions=0,
                )
                return ()

        strategy_results = await asyncio.gather(
            *(_safe_evaluate(s) for s in active),
        )

        for strategy, decisions in zip(
            active,
            strategy_results,
            strict=True,
        ):
            logger.info(
                HR_SCALING_STRATEGY_EVALUATED,
                strategy=str(strategy.name),
                decisions=len(decisions),
            )
            all_decisions.extend(decisions)

        logger.info(
            HR_SCALING_DECISIONS_MERGED,
            total_decisions=len(all_decisions),
        )

        # 3. Apply guard chain.
        filtered = await self._guard.filter(tuple(all_decisions))

        # 4. Record decisions.
        for decision in filtered:
            self._recent_decisions.append(decision)

        logger.info(
            HR_SCALING_CYCLE_COMPLETE,
            input_decisions=len(all_decisions),
            output_decisions=len(filtered),
        )

        # 5. Record trigger completion so in-progress flags reset.
        if self._trigger is not None:
            await self._trigger.record_run()

        return filtered

    async def execute_decisions(
        self,
        decisions: tuple[ScalingDecision, ...],
    ) -> tuple[ScalingActionRecord, ...]:
        """Execute scaling decisions via the hire/offboard services.

        HIRE decisions dispatch to ``HiringService.create_request``.
        PRUNE decisions dispatch to ``OffboardingService.offboard``.
        HOLD and NO_OP decisions are recorded as EXECUTED no-ops.

        After each successful execution, the cooldown and rate limit
        guards are notified via ``record_action`` so that subsequent
        evaluations respect the recent activity.

        Args:
            decisions: Filtered decisions from ``evaluate``.

        Returns:
            Action records documenting the outcome of each decision.
        """
        records: list[ScalingActionRecord] = []
        for decision in decisions:
            record = await self._execute_one(decision)
            records.append(record)
            self.record_action(record)
            if record.outcome == ScalingOutcome.EXECUTED:
                # Notify stateful guards so repeated cycles respect recent
                # activity (cooldown + rate limit tracking).
                await self._notify_stateful_guards(decision)
            elif record.outcome in (
                ScalingOutcome.FAILED,
                ScalingOutcome.DEFERRED,
            ):
                # Release the tentative cooldown reservation so a failed
                # attempt does not consume the cooldown window.
                await self._release_guard_reservations(decision)
        return tuple(records)

    async def _execute_one(
        self,
        decision: ScalingDecision,
    ) -> ScalingActionRecord:
        """Dispatch a single decision and return its action record.

        Returns:
            Result of type ``ScalingActionRecord``.
        """
        now = datetime.now(UTC)

        if decision.action_type in {
            ScalingActionType.NO_OP,
            ScalingActionType.HOLD,
        }:
            return ScalingActionRecord(
                decision_id=str(decision.id),
                outcome=ScalingOutcome.EXECUTED,
                result_id=str(decision.id),
                executed_at=now,
            )

        if (
            decision.action_type == ScalingActionType.HIRE
            and self._hiring_service is not None
        ):
            return await self._execute_hire(decision, now)

        if (
            decision.action_type == ScalingActionType.PRUNE
            and self._offboarding_service is not None
        ):
            return await self._execute_prune(decision, now)

        # Service not configured -- surface as DEFERRED with the
        # decision id as the result id so operators can investigate.
        return ScalingActionRecord(
            decision_id=str(decision.id),
            outcome=ScalingOutcome.DEFERRED,
            result_id=str(decision.id),
            reason=NotBlankStr("execution service not configured"),
            executed_at=now,
        )

    async def _execute_hire(
        self,
        decision: ScalingDecision,
        now: datetime,
    ) -> ScalingActionRecord:
        """Execute a HIRE decision via the hiring service.

        Returns:
            Result of type ``ScalingActionRecord``.

        Raises:
            CancelledError: If the related operation fails.
        """
        assert self._hiring_service is not None  # noqa: S101
        try:
            request = await self._hiring_service.create_request(
                requested_by=NotBlankStr("scaling_service"),
                department=decision.target_department or NotBlankStr("engineering"),
                role=decision.target_role or NotBlankStr("general"),
                level=self._config.default_hire_level,
                required_skills=decision.target_skills,
                reason=decision.rationale,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                HR_SCALING_EXECUTION_FAILED,
                decision_id=str(decision.id),
                action="hire",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return ScalingActionRecord(
                decision_id=str(decision.id),
                outcome=ScalingOutcome.FAILED,
                reason=NotBlankStr(
                    f"{type(exc).__name__}: {safe_error_description(exc)}"
                ),
                executed_at=now,
            )
        return ScalingActionRecord(
            decision_id=str(decision.id),
            outcome=ScalingOutcome.EXECUTED,
            result_id=NotBlankStr(str(request.id)),
            executed_at=now,
        )

    async def _execute_prune(
        self,
        decision: ScalingDecision,
        now: datetime,
    ) -> ScalingActionRecord:
        """Execute a PRUNE decision via the offboarding service.

        Returns:
            Result of type ``ScalingActionRecord``.

        Raises:
            CancelledError: If the related operation fails.
        """
        assert self._offboarding_service is not None  # noqa: S101
        assert decision.target_agent_id is not None  # noqa: S101
        try:
            firing_reason = _firing_reason_for(decision)
            agent_name = decision.target_agent_id
            if self._agent_registry is not None:
                agent = await self._agent_registry.get(
                    str(decision.target_agent_id),
                )
                if agent is not None:
                    agent_name = NotBlankStr(
                        getattr(agent, "name", str(decision.target_agent_id)),
                    )
            firing_request = FiringRequest(
                agent_id=decision.target_agent_id,
                agent_name=agent_name,
                reason=firing_reason,
                requested_by=NotBlankStr("scaling_service"),
                details=str(decision.rationale),
                created_at=now,
            )
            record = await self._offboarding_service.offboard(firing_request)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                HR_SCALING_EXECUTION_FAILED,
                decision_id=str(decision.id),
                action="prune",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return ScalingActionRecord(
                decision_id=str(decision.id),
                outcome=ScalingOutcome.FAILED,
                reason=NotBlankStr(
                    f"{type(exc).__name__}: {safe_error_description(exc)}"
                ),
                executed_at=now,
            )
        result_id = (
            getattr(record, "firing_request_id", None) or decision.target_agent_id
        )
        return ScalingActionRecord(
            decision_id=str(decision.id),
            outcome=ScalingOutcome.EXECUTED,
            result_id=NotBlankStr(str(result_id)),
            executed_at=now,
        )

    def _iter_inner_guards(self) -> list[ScalingGuard]:
        """Flatten the guard chain into a list of leaf guards.

        Returns:
            List of ``ScalingGuard``.
        """
        guard = self._guard
        if isinstance(guard, CompositeScalingGuard):
            return list(guard.get_guards())
        return [guard]

    async def _notify_stateful_guards(
        self,
        decision: ScalingDecision,
    ) -> None:
        """Notify CooldownGuard and RateLimitGuard of an executed action."""
        for inner in self._iter_inner_guards():
            if isinstance(inner, (CooldownGuard, RateLimitGuard)):
                try:
                    await inner.record_action(decision)
                except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                    reraise_critical(exc)
                    log_exception_redacted(
                        logger,
                        HR_SCALING_EXECUTION_FAILED,
                        exc,
                        action="guard_record_failed",
                        guard=str(inner.name),
                        decision_id=str(decision.id),
                    )

    async def _release_guard_reservations(
        self,
        decision: ScalingDecision,
    ) -> None:
        """Release tentative cooldown reservations on execution failure."""
        for inner in self._iter_inner_guards():
            if isinstance(inner, CooldownGuard):
                try:
                    await inner.release_reservation(decision)
                except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                    reraise_critical(exc)
                    log_exception_redacted(
                        logger,
                        HR_SCALING_EXECUTION_FAILED,
                        exc,
                        action="guard_release_failed",
                        guard=str(inner.name),
                        decision_id=str(decision.id),
                    )

    def record_action(self, record: ScalingActionRecord) -> None:
        """Record an executed scaling action.

        Args:
            record: The action record to store.
        """
        self._recent_actions.append(record)
        if record.outcome == ScalingOutcome.EXECUTED:
            logger.info(
                HR_SCALING_EXECUTED,
                decision_id=str(record.decision_id),
                outcome=record.outcome.value,
            )
        elif record.outcome == ScalingOutcome.FAILED:
            logger.warning(
                HR_SCALING_EXECUTION_FAILED,
                decision_id=str(record.decision_id),
                reason=str(record.reason),
            )

    def get_last_context(self) -> ScalingContext | None:
        """Return the most recently built scaling context (if any).

        Returns:
            The matching ``ScalingContext``, or ``None`` when no match is found.
        """
        return self._last_context

    def get_recent_decisions(self) -> tuple[ScalingDecision, ...]:
        """Get recent scaling decisions.

        Returns:
            Recent decisions (most recent last).
        """
        return tuple(self._recent_decisions)

    @property
    def config(self) -> ScalingConfig:
        """Return the scaling configuration (read-only)."""
        return self._config

    def get_recent_actions(self) -> tuple[ScalingActionRecord, ...]:
        """Get recent scaling action records.

        Returns:
            Recent action records (most recent last).
        """
        return tuple(self._recent_actions)
