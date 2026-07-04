"""Approve-to-live consumer for authored tools.

Closes the toolsmith loop: an operator approving a ``proposal:tool_creation``
item in the Approvals queue makes the authored tool go live. The consumer polls
the approval store for APPROVED tool-creation items, rehydrates the persisted
``ToolBlueprint`` by the id the approval gate stamped into the item metadata,
atomically claims the grant (``consume_if_approved``), and applies the blueprint
through :class:`ToolsmithService` (which re-checks the live ``tool_creation``
master gate, so a toggle-off blocks even an approved tool from registering).

Claim-then-apply: the atomic consume marks the one-shot grant used before the
apply runs, so a re-poll never double-applies. A blueprint the applier already
activated is caught by its own state machine (a re-save + re-validate converges
to ACTIVE again), and the gate still guards every registration.
"""

from synthorg.approval.enums import ApprovalStatus
from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.core.approval import ApprovalItem
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.meta.guards.approval_gate import PROPOSAL_GUARD_ACTION_TYPE_PREFIX
from synthorg.meta.models import (
    ImprovementProposal,
    ProposalAltitude,
    ProposalRationale,
    RollbackOperation,
    RollbackPlan,
)
from synthorg.meta.toolsmith.models import ToolBlueprint
from synthorg.meta.toolsmith.service import ToolsmithService
from synthorg.notifications.dispatcher import NotificationDispatcher
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.toolsmith import (
    TOOLSMITH_APPLY_COMPLETED,
    TOOLSMITH_APPLY_FAILED,
)
from synthorg.persistence.tool_blueprint_protocol import DynamicToolRepository

logger = get_logger(__name__)

_TOOL_CREATION_ACTION_TYPE: str = (
    f"{PROPOSAL_GUARD_ACTION_TYPE_PREFIX}{ProposalAltitude.TOOL_CREATION.value}"
)


class ToolApprovalConsumer:
    """Applies operator-approved tool-creation blueprints, making them live.

    Args:
        service: The toolsmith service whose ``apply`` live-registers a
            blueprint (and re-checks the live tool-creation master gate).
        blueprint_repo: Durable blueprint store the consumer rehydrates from.
        approval_store: Approval store the consumer polls + atomically claims.
        notification_dispatcher: Optional ops-alert sink. The claim is
            one-shot, so a failed apply silently burns an operator's
            approval; a dispatcher surfaces that instead of a log-only trace.
    """

    def __init__(
        self,
        *,
        service: ToolsmithService,
        blueprint_repo: DynamicToolRepository,
        approval_store: ApprovalStoreProtocol,
        notification_dispatcher: NotificationDispatcher | None = None,
    ) -> None:
        self._service = service
        self._blueprint_repo = blueprint_repo
        self._approval_store = approval_store
        self._notification_dispatcher = notification_dispatcher

    async def consume(self) -> int:
        """Apply every approved tool-creation blueprint; return the count applied.

        Best-effort per item: a single failed apply logs and is skipped rather
        than aborting the batch. A no-op when tool creation is disabled live.

        Returns:
            The number of tools successfully registered live this pass.
        """
        approved = await self._approval_store.list_items(
            status=ApprovalStatus.APPROVED,
            action_type=NotBlankStr(_TOOL_CREATION_ACTION_TYPE),
        )
        applied = 0
        for item in approved:
            if await self._consume_one(item):
                applied += 1
        return applied

    async def _consume_one(self, item: ApprovalItem) -> bool:
        """Rehydrate, claim, and apply one approved item.

        Returns:
            ``True`` iff the tool was registered live this pass.
        """
        blueprint_id = item.metadata.get("blueprint_id")
        # ``NotBlankStr(...)`` is a no-op outside a Pydantic boundary (it is an
        # annotated alias, so it just forwards to ``str``), so guard the blank
        # case explicitly here rather than rely on the wrapper below.
        if not blueprint_id or not blueprint_id.strip():
            return False
        blueprint = await self._blueprint_repo.get(NotBlankStr(blueprint_id))
        if blueprint is None:
            return False
        claimed = await self._approval_store.consume_if_approved(
            NotBlankStr(str(item.id)),
        )
        if claimed is None:
            # Not APPROVED, already consumed, or a concurrent claim won.
            return False
        return await self._apply_claimed(blueprint)

    async def _apply_claimed(self, blueprint: ToolBlueprint) -> bool:
        """Apply a claimed blueprint through the service (best-effort).

        Returns:
            ``True`` iff the apply reported success.
        """
        try:
            result = await self._service.apply(_apply_proposal_for(blueprint))
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                TOOLSMITH_APPLY_FAILED,
                tool_name=blueprint.name,
                note="approve_to_live_apply_raised",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            await self._notify_apply_failed(blueprint, safe_error_description(exc))
            return False
        if not result.success:
            logger.warning(
                TOOLSMITH_APPLY_FAILED,
                tool_name=blueprint.name,
                note="approve_to_live_apply_rejected",
                detail=str(result.error_message or ""),
            )
            await self._notify_apply_failed(
                blueprint, str(result.error_message or "validation gate rejected")
            )
            return False
        logger.info(
            TOOLSMITH_APPLY_COMPLETED,
            tool_name=blueprint.name,
            note="approve_to_live",
        )
        return True

    async def _notify_apply_failed(self, blueprint: ToolBlueprint, reason: str) -> None:
        """Surface a burned approval to the operator (best-effort).

        The one-shot grant is already consumed by the time apply runs, so a
        failed apply leaves the approval spent with the tool never live. An
        ops alert lets the operator re-propose rather than discovering it only
        by log-grep. A no-op when no dispatcher is wired.
        """
        if self._notification_dispatcher is None:
            return
        from synthorg.notifications.models import (  # noqa: PLC0415
            Notification,
            NotificationCategory,
            NotificationSeverity,
        )

        body = (
            f"Approved tool {blueprint.name!r} (capability "
            f"{blueprint.capability!r}) failed to go live: {reason}. The "
            "approval is spent; re-propose the tool to try again."
        )
        try:
            await self._notification_dispatcher.dispatch(
                Notification(
                    category=NotificationCategory.SYSTEM,
                    severity=NotificationSeverity.WARNING,
                    title="Approved tool failed to go live",
                    body=body,
                    source="meta.toolsmith",
                ),
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                TOOLSMITH_APPLY_FAILED,
                tool_name=blueprint.name,
                note="apply_failed_alert_dispatch_failed",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )


def _apply_proposal_for(blueprint: ToolBlueprint) -> ImprovementProposal:
    """Wrap a rehydrated blueprint in a minimal apply-only proposal.

    The applier reads only ``tool_changes``; the remaining fields carry
    audit-legible context for the apply log without re-deriving the original
    gap (which is not persisted with the approval).

    Returns:
        An :class:`ImprovementProposal` the applier can consume.
    """
    rollback = RollbackPlan(
        operations=(
            RollbackOperation(
                operation_type="retire_tool",
                target=blueprint.name,
                description=f"Retire and unregister authored tool {blueprint.name!r}.",
            ),
        ),
        validation_check=f"tool {blueprint.name!r} is no longer registered",
    )
    return ImprovementProposal(
        altitude=ProposalAltitude.TOOL_CREATION,
        title=f"Apply approved tool {blueprint.capability}",
        description=(
            f"Operator-approved authored tool {blueprint.name!r} "
            f"(capability {blueprint.capability!r}) going live."
        ),
        rationale=ProposalRationale(
            signal_summary="operator approved a proposed tool",
            pattern_detected="approved capability gap",
            expected_impact=f"org can perform {blueprint.capability}",
            confidence_reasoning="human approval granted",
        ),
        tool_changes=(blueprint,),
        rollback_plan=rollback,
        confidence=1.0,
        source_rule=NotBlankStr("approve_to_live"),
    )


__all__ = ["ToolApprovalConsumer"]
