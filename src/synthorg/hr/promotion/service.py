"""Promotion service orchestrator.

Central service for managing agent promotions and demotions,
including criteria evaluation, approval decisions, model mapping,
and trust integration.
"""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

from synthorg.core.enums import (
    ApprovalRiskLevel,
    ApprovalStatus,
    SeniorityLevel,
)
from synthorg.core.types import NotBlankStr
from synthorg.hr.enums import PromotionDirection
from synthorg.hr.errors import (
    PromotionApprovalRequiredError,
    PromotionCooldownError,
    PromotionError,
)
from synthorg.hr.promotion.models import (
    PromotionEvaluation,
    PromotionRecord,
    PromotionRequest,
)
from synthorg.observability import get_logger
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

if TYPE_CHECKING:
    from pydantic import AwareDatetime

    from synthorg.api.approval_store import ApprovalStore
    from synthorg.hr.performance.tracker import PerformanceTracker
    from synthorg.hr.promotion.approval_protocol import (
        PromotionApprovalStrategy,
    )
    from synthorg.hr.promotion.config import PromotionConfig
    from synthorg.hr.promotion.criteria_protocol import (
        PromotionCriteriaStrategy,
    )
    from synthorg.hr.promotion.model_mapping_protocol import (
        ModelMappingStrategy,
    )
    from synthorg.hr.registry import AgentRegistryService
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
    """Get the next higher seniority level, or None at top."""
    members = list(SeniorityLevel)
    idx = members.index(level)
    if idx + 1 >= len(members):
        return None
    return members[idx + 1]


def _prev_level(level: SeniorityLevel) -> SeniorityLevel | None:
    """Get the next lower seniority level, or None at bottom."""
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
        approval_store: ApprovalStore | None = None,
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

        decision = await self._approval.decide(
            evaluation=evaluation,
            agent_identity=identity,
        )

        now = datetime.now(UTC)
        approval_id: NotBlankStr | None = None
        status = ApprovalStatus.PENDING

        if decision.auto_approve:
            status = ApprovalStatus.APPROVED
        elif decision.requires_human:
            if self._approval_store is None:
                msg = (
                    f"Promotion for agent {agent_id!r} requires human "
                    f"approval but no approval store is configured"
                )
                logger.warning(
                    PROMOTION_REQUESTED,
                    agent_id=agent_id,
                    error=msg,
                )
                raise PromotionError(msg)
            approval_id = await self._create_approval(
                agent_id=agent_id,
                evaluation=evaluation,
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
            created_at=now,
            approval_id=approval_id,
        )

        logger.info(
            PROMOTION_REQUESTED,
            agent_id=agent_id,
            direction=evaluation.direction.value,
            status=status.value,
        )
        return request

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

        identity = await self._registry.get(request.agent_id)
        if identity is None:
            msg = f"Agent {request.agent_id!r} not found"
            logger.warning(
                PROMOTION_APPLIED,
                agent_id=request.agent_id,
                error=msg,
            )
            raise PromotionError(msg)

        # Resolve model mapping
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

        await self._registry.update_identity(
            request.agent_id,
            **updates,
        )

        now = datetime.now(UTC)
        record = PromotionRecord(
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

        self._promotion_history.setdefault(
            str(request.agent_id),
            [],
        ).append(record)

        if self._config.cooldown_hours > 0:
            self._cooldown_until[str(request.agent_id)] = now + timedelta(
                hours=self._config.cooldown_hours
            )

        # Best-effort trust re-evaluation — promotion is already applied,
        # so failures here must not prevent the record from being returned.
        if self._trust_service is not None:
            try:
                snapshot = await self._tracker.get_snapshot(request.agent_id)
                await self._trust_service.evaluate_agent(
                    request.agent_id,
                    snapshot,
                )
            except Exception:
                logger.warning(
                    PROMOTION_APPLIED,
                    agent_id=request.agent_id,
                    error="Trust re-evaluation failed after promotion; "
                    "promotion still applied",
                )

        event = (
            PROMOTION_APPLIED
            if request.direction == PromotionDirection.PROMOTION
            else DEMOTION_APPLIED
        )
        logger.info(
            event,
            agent_id=request.agent_id,
            old_level=record.old_level.value,
            new_level=record.new_level.value,
            model_changed=record.model_changed,
        )

        # Notify agent and team — best-effort, must not block the record.
        if self._on_notification is not None:
            try:
                await self._on_notification(record)
                logger.debug(
                    PROMOTION_NOTIFICATION_SENT,
                    agent_id=request.agent_id,
                    direction=request.direction.value,
                )
            except Exception:
                logger.warning(
                    PROMOTION_NOTIFICATION_SENT,
                    agent_id=request.agent_id,
                    error="Notification callback failed; promotion still applied",
                )

        return record

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
        """Create an approval item for a promotion requiring human review."""
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
            id=approval_id,
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
