"""Escalation / approval-item creation mixin for ``SecOpsService``.

Owns ``_handle_escalation``. Builds on
:class:`~synthorg.security.service_safety.SecOpsServiceSafetyMixin` (for
``_run_safety_classifier``, ``_run_uncertainty_check``, and
``_build_deny_reason``) and relies on ``_approval_store`` declared on the
concrete service.
"""

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from synthorg.approval.enums import ApprovalStatus
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, log_exception_redacted
from synthorg.observability.events.security import (
    SECURITY_ESCALATION_CREATED,
    SECURITY_ESCALATION_STORE_ERROR,
    SECURITY_VERDICT_DENY,
)
from synthorg.security.models import (
    SecurityContext,
    SecurityVerdict,
    SecurityVerdictType,
)
from synthorg.security.service_safety import SecOpsServiceSafetyMixin

if TYPE_CHECKING:
    from synthorg.approval.protocol import ApprovalStoreProtocol

logger = get_logger(__name__)


class SecOpsServiceEscalationMixin(SecOpsServiceSafetyMixin):
    """Approval-item creation for ``SecOpsService`` ESCALATE verdicts."""

    _approval_store: ApprovalStoreProtocol | None

    async def _handle_escalation(
        self,
        context: SecurityContext,
        verdict: SecurityVerdict,
    ) -> SecurityVerdict:
        """Create an approval item in the approval store.

        When a safety classifier is configured, the action is
        classified before creating the approval item.  BLOCKED
        actions are auto-rejected (returned as DENY).  SUSPICIOUS
        actions get a warning badge via metadata.  When an
        uncertainty checker is configured, a cross-provider
        confidence score is attached.

        Falls back to DENY if no approval store is configured or if
        the store raises an exception.

        Returns:
            An ESCALATE verdict carrying the new approval id, or a DENY
            verdict when escalation is unavailable or the action was
            classified as blocked.
        """
        if self._approval_store is None:
            logger.warning(
                SECURITY_VERDICT_DENY,
                tool_name=context.tool_name,
                original_verdict="escalate",
                note="no approval store -- converting to DENY",
            )
            return verdict.model_copy(
                update={
                    "verdict": SecurityVerdictType.DENY,
                    "reason": (f"{verdict.reason} (escalation unavailable -- denied)"),
                },
            )

        approval_id = str(uuid.uuid4())
        now = datetime.now(UTC)
        description = verdict.reason
        metadata: dict[str, str] = {
            "tool_name": context.tool_name,
            "tool_category": context.tool_category.value,
        }

        # Stage 1+2: safety classification (if configured).
        if self._safety_classifier is not None:
            auto_rejected = await self._run_safety_classifier(
                context,
                verdict,
                metadata,
            )
            if auto_rejected:
                deny_reason = self._build_deny_reason(
                    verdict.reason,
                    metadata,
                )
                return verdict.model_copy(
                    update={
                        "verdict": SecurityVerdictType.DENY,
                        "reason": deny_reason,
                    },
                )
            # Use stripped description for the reviewer view.
            stripped = metadata.get("stripped_description")
            if stripped:
                description = stripped

        # Cross-provider uncertainty check (if configured).
        # Only run when a stripped description is available;
        # never broadcast raw verdict.reason (may contain PII).
        stripped_for_check = metadata.get("stripped_description")
        if self._uncertainty_checker is not None and stripped_for_check:
            await self._run_uncertainty_check(
                stripped_for_check,
                metadata,
            )

        # Local import breaks an import cycle:
        # core.approval -> ontology.__init__ -> persistence ->
        # budget -> security -> security.service -> core.approval.
        # Keeping this import function-local avoids re-entering
        # core.approval while it is still being initialized.
        from synthorg.approval.enums import ApprovalSource  # noqa: PLC0415
        from synthorg.core.approval import ApprovalItem  # noqa: PLC0415

        item = ApprovalItem(
            id=uuid.UUID(approval_id),
            action_type=context.action_type,
            title=f"Security escalation: {context.tool_name}",
            description=description,
            requested_by=context.agent_id or "system",
            risk_level=verdict.risk_level,
            # A SecOps escalation parks the agent's execution context;
            # the decision resumes that parked run, so route it via the
            # mid-execution resume path deterministically.
            source=ApprovalSource.PARKED_CONTEXT,
            status=ApprovalStatus.PENDING,
            created_at=now,
            task_id=context.task_id,
            metadata=metadata,
        )
        try:
            await self._approval_store.add(item)
        except Exception as exc:
            reraise_critical(exc)
            log_exception_redacted(
                logger,
                SECURITY_ESCALATION_STORE_ERROR,
                exc,
                approval_id=approval_id,
                tool_name=context.tool_name,
                agent_id=context.agent_id,
            )
            return verdict.model_copy(
                update={
                    "verdict": SecurityVerdictType.DENY,
                    "reason": (f"{verdict.reason} (escalation store error -- denied)"),
                },
            )
        logger.info(
            SECURITY_ESCALATION_CREATED,
            approval_id=approval_id,
            tool_name=context.tool_name,
            agent_id=context.agent_id,
        )
        return verdict.model_copy(
            update={"approval_id": approval_id},
        )
