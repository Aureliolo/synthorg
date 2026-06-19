# module-kind: service
"""Promotion service orchestrator.

Central service for managing agent promotions and demotions,
including criteria evaluation, approval decisions, model mapping,
and trust integration.
"""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from pydantic import AwareDatetime

from synthorg.approval.enums import ApprovalRiskLevel, ApprovalStatus
from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.core.agent import AgentIdentity
from synthorg.core.concurrency.refcounted_lock_map import RefcountedLockMap
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.hr.enums import PromotionDirection
from synthorg.hr.errors import (
    PromotionApprovalRequiredError,
    PromotionCooldownError,
    PromotionError,
)
from synthorg.hr.performance.tracker import PerformanceTracker
from synthorg.hr.promotion.approval_protocol import PromotionApprovalStrategy
from synthorg.hr.promotion.config import PromotionConfig
from synthorg.hr.promotion.criteria_protocol import PromotionCriteriaStrategy
from synthorg.hr.promotion.model_mapping_protocol import ModelMappingStrategy
from synthorg.hr.promotion.models import (
    PromotionEvaluation,
    PromotionRecord,
    PromotionRequest,
)
from synthorg.hr.registry import AgentRegistryService
from synthorg.hr.seniority import SeniorityLevel
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.hr import HR_AGENT_STATUS_TRANSITIONED
from synthorg.observability.events.promotion import (
    DEMOTION_APPLIED,
    PROMOTION_APPLIED,
    PROMOTION_APPROVAL_SUBMITTED,
    PROMOTION_COOLDOWN_ACTIVE,
    PROMOTION_EVALUATE_COMPLETE,
    PROMOTION_EVALUATE_FAILED,
    PROMOTION_EVALUATE_START,
    PROMOTION_MODEL_CHANGED,
    PROMOTION_NOTIFICATION_SENT,
    PROMOTION_REJECTED,
    PROMOTION_REQUESTED,
)
from synthorg.security.trust.service import TrustService

logger = get_logger(__name__)


_SYSTEM_INITIATOR = NotBlankStr("system")

# Callback type for promotion/demotion notifications.
# The communication layer can supply a concrete callback
# (e.g. via MessageBus.publish) to notify agents and teams.
PromotionNotificationCallback = Callable[
    ["PromotionRecord"],
    "Awaitable[None]",
]


def _next_level(level: SeniorityLevel) -> SeniorityLevel | None:
    """Get the next higher seniority level, or None at top.

    Returns:
        The resulting ``SeniorityLevel``, or ``None`` when unavailable.
    """
    members = list(SeniorityLevel)
    idx = members.index(level)
    if idx + 1 >= len(members):
        return None
    return members[idx + 1]


def _prev_level(level: SeniorityLevel) -> SeniorityLevel | None:
    """Get the next lower seniority level, or None at bottom.

    Returns:
        The resulting ``SeniorityLevel``, or ``None`` when unavailable.
    """
    members = list(SeniorityLevel)
    idx = members.index(level)
    if idx <= 0:
        return None
    return members[idx - 1]


class PromotionService:
    """Orchestrates agent promotions and demotions.

    Coordinates criteria evaluation, approval decisions, model
    mapping, registry updates, and optional trust re-evaluation.

    Args:
        criteria_strategy: Strategy for evaluating promotion criteria.
        approval_strategy: Strategy for approval decisions.
        model_mapping_strategy: Strategy for model resolution.
        registry: Agent registry service.
        tracker: Performance tracker.
        config: Promotion configuration.
        approval_store: Optional approval store for human approval.
        trust_service: Optional trust service for re-evaluation.
        on_notification: Optional callback to notify agents/teams of
            promotion or demotion events. Wired by the communication
            layer when available.
    """

    def __init__(  # noqa: PLR0913
        self,
        *,
        criteria_strategy: PromotionCriteriaStrategy,
        approval_strategy: PromotionApprovalStrategy,
        model_mapping_strategy: ModelMappingStrategy,
        registry: AgentRegistryService,
        tracker: PerformanceTracker,
        config: PromotionConfig,
        approval_store: ApprovalStoreProtocol | None = None,
        trust_service: TrustService | None = None,
        on_notification: PromotionNotificationCallback | None = None,
    ) -> None:
        self._criteria = criteria_strategy
        self._approval = approval_strategy
        self._model_mapping = model_mapping_strategy
        self._registry = registry
        self._tracker = tracker
        self._config = config
        self._approval_store = approval_store
        self._trust_service = trust_service
        self._on_notification = on_notification
        self._promotion_history: dict[str, list[PromotionRecord]] = {}
        self._cooldown_until: dict[str, AwareDatetime] = {}
        # Per-agent lock serialising ``apply_promotion``: the cooldown
        # check and set straddle awaits (registry update, history append),
        # so two concurrent approved requests for one agent could both
        # apply and double-promote. The lock makes the re-check-and-apply
        # atomic per agent; a different agent never contends.
        self._apply_locks = RefcountedLockMap[str]()

    async def evaluate_promotion(
        self,
        agent_id: NotBlankStr,
    ) -> PromotionEvaluation:
        """Evaluate whether an agent qualifies for promotion.

        Args:
            agent_id: Agent to evaluate.

        Returns:
            Promotion evaluation result.

        Raises:
            PromotionError: If the agent cannot be promoted.
        """
        identity = await self._registry.get(agent_id)
        if identity is None:
            msg = f"Agent {agent_id!r} not found"
            logger.warning(
                PROMOTION_EVALUATE_FAILED,
                agent_id=agent_id,
                error=msg,
            )
            raise PromotionError(msg)

        target = _next_level(identity.level)
        if target is None:
            msg = f"Agent {agent_id!r} is already at maximum seniority"
            logger.warning(
                PROMOTION_EVALUATE_FAILED,
                agent_id=agent_id,
                current_level=identity.level.value,
                error=msg,
            )
            raise PromotionError(msg)

        logger.debug(
            PROMOTION_EVALUATE_START,
            agent_id=agent_id,
            current_level=identity.level.value,
            target_level=target.value,
        )

        snapshot = await self._tracker.get_snapshot(agent_id)

        evaluation = await self._criteria.evaluate(
            agent_id=agent_id,
            current_level=identity.level,
            target_level=target,
            snapshot=snapshot,
        )

        logger.debug(
            PROMOTION_EVALUATE_COMPLETE,
            agent_id=agent_id,
            eligible=evaluation.eligible,
        )
        return evaluation

    async def evaluate_demotion(
        self,
        agent_id: NotBlankStr,
    ) -> PromotionEvaluation:
        """Evaluate whether an agent should be demoted.

        Args:
            agent_id: Agent to evaluate.

        Returns:
            Demotion evaluation result.

        Raises:
            PromotionError: If the agent cannot be demoted.
        """
        identity = await self._registry.get(agent_id)
        if identity is None:
            msg = f"Agent {agent_id!r} not found"
            logger.warning(
                PROMOTION_EVALUATE_FAILED,
                agent_id=agent_id,
                error=msg,
            )
            raise PromotionError(msg)

        target = _prev_level(identity.level)
        if target is None:
            msg = f"Agent {agent_id!r} is already at minimum seniority"
            logger.warning(
                PROMOTION_EVALUATE_FAILED,
                agent_id=agent_id,
                current_level=identity.level.value,
                error=msg,
            )
            raise PromotionError(msg)

        logger.debug(
            PROMOTION_EVALUATE_START,
            agent_id=agent_id,
            current_level=identity.level.value,
            target_level=target.value,
            direction="demotion",
        )

        snapshot = await self._tracker.get_snapshot(agent_id)

        return await self._criteria.evaluate(
            agent_id=agent_id,
            current_level=identity.level,
            target_level=target,
            snapshot=snapshot,
        )

    async def request_promotion(
        self,
        agent_id: NotBlankStr,
        evaluation: PromotionEvaluation,
        *,
        initiated_by: NotBlankStr = _SYSTEM_INITIATOR,
    ) -> PromotionRequest:
        """Create a promotion/demotion request.

        Checks cooldown, evaluates approval decision, and creates
        an approval item if human approval is needed.

        Args:
            agent_id: Agent to promote/demote.
            evaluation: The evaluation result.
            initiated_by: Who initiated the request.

        Returns:
            Promotion request.

        Raises:
            PromotionCooldownError: If in cooldown period.
            PromotionError: If agent not found.
        """
        if not evaluation.eligible:
            msg = f"Agent {agent_id!r} is not eligible for {evaluation.direction.value}"
            logger.warning(
                PROMOTION_EVALUATE_FAILED,
                agent_id=agent_id,
                error=msg,
            )
            raise PromotionError(msg)

        if self.is_in_cooldown(agent_id):
            until = self._cooldown_until.get(str(agent_id))
            msg = f"Agent {agent_id!r} is in cooldown until {until}"
            logger.info(
                PROMOTION_COOLDOWN_ACTIVE,
                agent_id=agent_id,
                until=str(until),
                error_type=PromotionCooldownError.__name__,
            )
            raise PromotionCooldownError(msg)

        identity = await self._registry.get(agent_id)
        if identity is None:
            msg = f"Agent {agent_id!r} not found"
            logger.warning(
                PROMOTION_REQUESTED,
                agent_id=agent_id,
                error=msg,
            )
            raise PromotionError(msg)

        status, approval_id = await self._resolve_request_status(
            agent_id=agent_id,
            evaluation=evaluation,
            identity=identity,
            initiated_by=initiated_by,
        )

        request = PromotionRequest(
            agent_id=agent_id,
            agent_name=identity.name,
            current_level=evaluation.current_level,
            target_level=evaluation.target_level,
            direction=evaluation.direction,
            evaluation=evaluation,
            status=status,
            created_at=datetime.now(UTC),
            approval_id=approval_id,
        )

        logger.info(
            PROMOTION_REQUESTED,
            agent_id=agent_id,
            direction=evaluation.direction.value,
            status=status.value,
        )
        return request

    async def _resolve_request_status(
        self,
        *,
        agent_id: NotBlankStr,
        evaluation: PromotionEvaluation,
        identity: AgentIdentity,
        initiated_by: NotBlankStr,
    ) -> tuple[ApprovalStatus, NotBlankStr | None]:
        """Decide a request's approval status, creating an approval item.

        Returns:
            The resolved ``(status, approval_id)`` pair; ``approval_id``
            is set only when human approval was gated.

        Raises:
            PromotionError: Human approval is required but no approval
                store is configured.
        """
        decision = await self._approval.decide(
            evaluation=evaluation,
            agent_identity=identity,
        )
        if decision.auto_approve:
            return ApprovalStatus.APPROVED, None
        if decision.requires_human:
            if self._approval_store is None:
                msg = (
                    f"Promotion for agent {agent_id!r} requires human "
                    f"approval but no approval store is configured"
                )
                logger.warning(PROMOTION_REQUESTED, agent_id=agent_id, error=msg)
                raise PromotionError(msg)
            approval_id = await self._create_approval(
                agent_id=agent_id,
                evaluation=evaluation,
                initiated_by=initiated_by,
            )
            return ApprovalStatus.PENDING, approval_id
        return ApprovalStatus.PENDING, None

    async def apply_promotion(
        self,
        request: PromotionRequest,
        *,
        initiated_by: NotBlankStr = _SYSTEM_INITIATOR,
    ) -> PromotionRecord:
        """Apply a promotion/demotion from an approved request.

        Updates the agent's seniority level, resolves model mapping,
        triggers trust re-evaluation, and records the lifecycle event.

        Args:
            request: Approved promotion request.
            initiated_by: Who initiated the application.

        Returns:
            Promotion record.

        Raises:
            PromotionApprovalRequiredError: If request is not approved.
            PromotionError: If agent not found.
            PromotionCooldownError: If a concurrent apply put the agent
                into cooldown before this one acquired the per-agent lock.
        """
        if request.status != ApprovalStatus.APPROVED:
            event = (
                PROMOTION_REJECTED
                if request.status == ApprovalStatus.REJECTED
                else PROMOTION_REQUESTED
            )
            logger.warning(
                event,
                agent_id=request.agent_id,
                status=request.status.value,
            )
            msg = f"Cannot apply promotion: request status is {request.status.value}"
            raise PromotionApprovalRequiredError(msg)

        await self._verify_approval(request)
        record = await self._apply_level_change(request, initiated_by=initiated_by)
        await self._reevaluate_trust_best_effort(request.agent_id)
        self._log_applied(record, request.direction)
        await self._notify_promotion_best_effort(record, request.direction)
        return record

    async def _apply_level_change(
        self,
        request: PromotionRequest,
        *,
        initiated_by: NotBlankStr,
    ) -> PromotionRecord:
        """Mutate the agent's level + model under the per-agent lock.

        Returns:
            The recorded promotion/demotion.

        Raises:
            PromotionCooldownError: A concurrent apply put the agent into
                cooldown before this one acquired the lock.
            PromotionError: The agent was not found.
        """
        async with self._apply_locks.acquire(str(request.agent_id)):
            self._recheck_cooldown_locked(request.agent_id)
            identity = await self._registry.get(request.agent_id)
            if identity is None:
                msg = f"Agent {request.agent_id!r} not found"
                logger.warning(
                    PROMOTION_APPLIED,
                    agent_id=request.agent_id,
                    error=msg,
                )
                raise PromotionError(msg)

            new_model_id = self._model_mapping.resolve_model(
                agent_identity=identity,
                new_level=request.target_level,
            )
            updates: dict[str, object] = {"level": request.target_level}
            if new_model_id is not None:
                updates["model"] = identity.model.model_copy(
                    update={"model_id": NotBlankStr(new_model_id)},
                )
                logger.info(
                    PROMOTION_MODEL_CHANGED,
                    agent_id=request.agent_id,
                    old_model=str(identity.model.model_id),
                    new_model=new_model_id,
                )
            await self._registry.update_identity(request.agent_id, **updates)
            logger.info(
                HR_AGENT_STATUS_TRANSITIONED,
                agent_id=request.agent_id,
                from_status=request.current_level.value,
                to_status=request.target_level.value,
            )

            now = datetime.now(UTC)
            record = self._build_promotion_record(
                request,
                identity=identity,
                new_model_id=new_model_id,
                initiated_by=initiated_by,
                now=now,
            )
            self._promotion_history.setdefault(str(request.agent_id), []).append(record)
            if self._config.cooldown_hours > 0:
                self._cooldown_until[str(request.agent_id)] = now + timedelta(
                    hours=self._config.cooldown_hours
                )
        return record

    def _recheck_cooldown_locked(self, agent_id: NotBlankStr) -> None:
        """Reject an apply if the agent entered cooldown before the lock.

        A concurrent apply may have promoted the agent and set the
        cooldown between this request's approval and acquiring the lock;
        applying again would double-promote.

        Raises:
            PromotionCooldownError: The agent is in an active cooldown.
        """
        if not self.is_in_cooldown(agent_id):
            return
        until = self._cooldown_until.get(str(agent_id))
        msg = (
            f"Agent {agent_id!r} entered cooldown (until {until})"
            f" before this promotion could apply"
        )
        logger.info(
            PROMOTION_COOLDOWN_ACTIVE,
            agent_id=agent_id,
            until=str(until),
            error_type=PromotionCooldownError.__name__,
        )
        raise PromotionCooldownError(msg)

    def _build_promotion_record(
        self,
        request: PromotionRequest,
        *,
        identity: AgentIdentity,
        new_model_id: str | None,
        initiated_by: NotBlankStr,
        now: datetime,
    ) -> PromotionRecord:
        """Construct the immutable promotion record for an applied change.

        Returns:
            The promotion record.
        """
        return PromotionRecord(
            agent_id=request.agent_id,
            agent_name=request.agent_name,
            old_level=request.current_level,
            new_level=request.target_level,
            direction=request.direction,
            evaluation=request.evaluation,
            approved_by=(
                NotBlankStr("auto")
                if request.approval_id is None
                else NotBlankStr("human")
            ),
            approval_id=request.approval_id,
            effective_at=now,
            initiated_by=initiated_by,
            model_changed=new_model_id is not None,
            old_model_id=(
                identity.model.model_id if new_model_id is not None else None
            ),
            new_model_id=(
                NotBlankStr(new_model_id) if new_model_id is not None else None
            ),
        )

    async def _reevaluate_trust_best_effort(self, agent_id: NotBlankStr) -> None:
        """Re-evaluate trust after a promotion; never block the record.

        The promotion is already applied, so a trust-evaluation failure
        is logged and swallowed (criticals re-raised) rather than
        propagating.
        """
        if self._trust_service is None:
            return
        try:
            snapshot = await self._tracker.get_snapshot(agent_id)
            await self._trust_service.evaluate_agent(agent_id, snapshot)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                PROMOTION_APPLIED,
                agent_id=agent_id,
                note="trust re-evaluation failed; promotion still applied",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

    def _log_applied(
        self,
        record: PromotionRecord,
        direction: PromotionDirection,
    ) -> None:
        """Emit the applied promotion/demotion lifecycle event."""
        event = (
            PROMOTION_APPLIED
            if direction == PromotionDirection.PROMOTION
            else DEMOTION_APPLIED
        )
        logger.info(
            event,
            agent_id=record.agent_id,
            old_level=record.old_level.value,
            new_level=record.new_level.value,
            model_changed=record.model_changed,
        )

    async def _notify_promotion_best_effort(
        self,
        record: PromotionRecord,
        direction: PromotionDirection,
    ) -> None:
        """Fire the promotion notification callback; never block the record."""
        if self._on_notification is None:
            return
        try:
            await self._on_notification(record)
            logger.debug(
                PROMOTION_NOTIFICATION_SENT,
                agent_id=record.agent_id,
                direction=direction.value,
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                PROMOTION_NOTIFICATION_SENT,
                agent_id=record.agent_id,
                note="notification callback failed; promotion still applied",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

    async def run_cycle(self) -> tuple[PromotionRecord, ...]:
        """Scan active agents and apply auto-approved seniority changes.

        Delegates to :func:`synthorg.hr.promotion.cycle.run_promotion_cycle`,
        which evaluates every active agent for promotion (then demotion),
        requests eligible changes, applies the auto-approved ones, and
        leaves human-gated changes pending as approval items.

        Returns:
            The records for changes applied during this cycle.
        """
        from synthorg.hr.promotion.cycle import run_promotion_cycle  # noqa: PLC0415

        return await run_promotion_cycle(self)

    @property
    def enabled(self) -> bool:
        """Whether the promotion subsystem is enabled.

        Returns:
            ``True`` when the configured subsystem is enabled.
        """
        return self._config.enabled

    @property
    def registry(self) -> AgentRegistryService:
        """The agent registry the service reads and mutates.

        Returns:
            The wired agent registry service.
        """
        return self._registry

    def get_promotion_history(
        self,
        agent_id: NotBlankStr,
    ) -> tuple[PromotionRecord, ...]:
        """Get promotion/demotion history for an agent.

        Args:
            agent_id: Agent identifier.

        Returns:
            Tuple of promotion records.
        """
        return tuple(self._promotion_history.get(str(agent_id), []))

    def is_in_cooldown(self, agent_id: NotBlankStr) -> bool:
        """Check whether an agent is in the promotion cooldown period.

        Args:
            agent_id: Agent identifier.

        Returns:
            True if in cooldown.
        """
        until = self._cooldown_until.get(str(agent_id))
        if until is None:
            return False
        return datetime.now(UTC) < until

    async def _verify_approval(
        self,
        request: PromotionRequest,
    ) -> None:
        """Verify approval status from store (defense-in-depth).

        If the request has an approval_id and an approval store is
        configured, verify that the stored approval is actually approved.
        Prevents crafted requests from bypassing human approval gates.

        Raises:
            PromotionApprovalRequiredError: If the related operation fails.
        """
        if request.approval_id is None or self._approval_store is None:
            return

        item = await self._approval_store.get(request.approval_id)
        if item is None or item.status != ApprovalStatus.APPROVED:
            msg = (
                f"Approval {request.approval_id!r} not found or "
                f"not approved in approval store"
            )
            logger.warning(
                PROMOTION_REJECTED,
                agent_id=request.agent_id,
                approval_id=request.approval_id,
                error=msg,
            )
            raise PromotionApprovalRequiredError(msg)

    async def _create_approval(
        self,
        *,
        agent_id: NotBlankStr,
        evaluation: PromotionEvaluation,
        initiated_by: NotBlankStr,
    ) -> NotBlankStr:
        """Create an approval item for a promotion requiring human review.

        Returns:
            Result of type ``NotBlankStr``.

        Raises:
            PromotionError: If the related operation fails.
        """
        # Defense-in-depth: caller already checks, but guard against
        # direct invocation without an approval store.
        if self._approval_store is None:
            msg = "Cannot create approval: no approval store configured"
            logger.warning(
                PROMOTION_APPROVAL_SUBMITTED,
                agent_id=agent_id,
                error=msg,
            )
            raise PromotionError(msg)

        from synthorg.core.approval import ApprovalItem  # noqa: PLC0415

        approval_id = NotBlankStr(str(uuid4()))
        now = datetime.now(UTC)

        approval = ApprovalItem(
            id=UUID(approval_id),
            action_type="org:promote",
            title=(
                f"{evaluation.direction.value.title()}: "
                f"{evaluation.current_level.value} -> "
                f"{evaluation.target_level.value}"
            ),
            description=(
                f"Agent {agent_id!r} evaluated for "
                f"{evaluation.direction.value}. "
                f"Criteria met: {evaluation.criteria_met_count}/"
                f"{len(evaluation.criteria_results)}"
            ),
            requested_by=initiated_by,
            risk_level=ApprovalRiskLevel.MEDIUM,
            created_at=now,
            metadata={
                "agent_id": str(agent_id),
                "direction": evaluation.direction.value,
                "current_level": evaluation.current_level.value,
                "target_level": evaluation.target_level.value,
            },
        )
        await self._approval_store.add(approval)

        logger.info(
            PROMOTION_APPROVAL_SUBMITTED,
            agent_id=agent_id,
            approval_id=approval_id,
        )
        return approval_id
