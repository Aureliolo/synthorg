"""Architecture applier.

Applies approved architecture proposals by creating new roles,
departments, or modifying workflows in the appropriate registries.
``dry_run()`` validates each ``ArchitectureChange`` against a
read-only view of those registries, so operators can preview whether
``apply()`` would succeed without mutating state. The per-operation
validators live in ``_architecture_validators``.
"""

from synthorg.core.critical_errors import reraise_critical
from synthorg.meta.appliers._architecture_validators import (
    ArchitectureApplierContext,
    ArchitectureUndo,
    _PendingChanges,
    _validate_change,
)
from synthorg.meta.models import (
    ApplyResult,
    ImprovementProposal,
    ProposalAltitude,
)
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.events.meta import (
    META_APPLY_COMPLETED,
    META_APPLY_FAILED,
    META_APPLY_STARTED,
    META_DRY_RUN_COMPLETED,
    META_DRY_RUN_FAILED,
    META_DRY_RUN_STARTED,
)

logger = get_logger(__name__)


class ArchitectureApplier:
    """Applies architecture proposals.

    Args:
        context: Read-only registry view.  Required for ``dry_run``;
            when absent dry_run rejects proposals with an explicit
            error rather than silently passing.
    """

    def __init__(
        self,
        *,
        context: ArchitectureApplierContext | None = None,
    ) -> None:
        """Store the registry context."""
        self._context = context

    @property
    def altitude(self) -> ProposalAltitude:
        """This applier handles architecture proposals.

        Returns:
            ``ProposalAltitude`` instance.
        """
        return ProposalAltitude.ARCHITECTURE

    async def apply(
        self,
        proposal: ImprovementProposal,
    ) -> ApplyResult:
        """Apply architecture changes through the durable registry seam.

        Each ``ArchitectureChange`` is applied via the context's
        ``apply_change``, which returns a per-change undo closure. The
        application is transactional in the :class:`ConfigApplier` mould:
        undos are tracked in order, and a mid-list failure triggers a
        reverse-order rollback that reverses every already-applied change
        before returning a failure result. ``modify_workflow`` reuses the
        already-durable ``WorkflowService.update_definition()`` inside the
        context. On success the cached read snapshot is refreshed.

        Args:
            proposal: The approved architecture proposal.

        Returns:
            Result indicating success or failure.
        """
        if self._context is None:
            logger.warning(
                META_APPLY_FAILED,
                altitude="architecture",
                proposal_id=str(proposal.id),
                reason="no_context",
            )
            return ApplyResult(
                success=False,
                error_message=(
                    "ArchitectureApplier.apply requires an "
                    "ArchitectureApplierContext; none was injected"
                ),
                changes_applied=0,
            )
        context = self._context
        logger.info(
            META_APPLY_STARTED,
            altitude="architecture",
            proposal_id=str(proposal.id),
            changes=len(proposal.architecture_changes),
        )
        undos: list[ArchitectureUndo] = []
        try:
            for change in proposal.architecture_changes:
                undo = await context.apply_change(change)
                undos.append(undo)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            failures = await self._rollback(undos, proposal=proposal)
            log_exception_redacted(
                logger,
                META_APPLY_FAILED,
                exc,
                altitude="architecture",
                proposal_id=str(proposal.id),
                applied=len(undos),
                rollback_failures=failures,
            )
            return ApplyResult(
                success=False,
                error_message=(
                    "Architecture apply failed and was rolled back. Check logs."
                ),
                changes_applied=0,
            )
        await context.refresh_snapshot()
        logger.info(
            META_APPLY_COMPLETED,
            altitude="architecture",
            changes=len(undos),
            proposal_id=str(proposal.id),
        )
        return ApplyResult(success=True, changes_applied=len(undos))

    async def _rollback(
        self,
        undos: list[ArchitectureUndo],
        *,
        proposal: ImprovementProposal,
    ) -> int:
        """Reverse previously-applied changes after a failed apply.

        A rollback step that itself fails is logged and skipped so one bad
        undo cannot abort the rest of the restoration.

        Returns:
            The number of rollback steps that failed; ``0`` means the
            registries were fully restored.
        """
        failures = 0
        for undo in reversed(undos):
            try:
                await undo()
            except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                reraise_critical(exc)
                failures += 1
                logger.warning(
                    META_APPLY_FAILED,
                    altitude="architecture",
                    proposal_id=str(proposal.id),
                    reason="rollback_step_failed",
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
        return failures

    async def dry_run(
        self,
        proposal: ImprovementProposal,
    ) -> ApplyResult:
        """Validate architecture changes without applying.

        Args:
            proposal: The proposal to validate.

        Returns:
            Result indicating whether apply would succeed.
        """
        logger.info(
            META_DRY_RUN_STARTED,
            altitude="architecture",
            proposal_id=str(proposal.id),
            changes=len(proposal.architecture_changes),
        )
        context = self._context
        if context is None:
            return self._fail(
                proposal,
                error_message=(
                    "ArchitectureApplier.dry_run requires an "
                    "ArchitectureApplierContext; none was injected"
                ),
            )
        if proposal.altitude != ProposalAltitude.ARCHITECTURE:
            return self._fail(
                proposal,
                error_message=(
                    f"Expected ARCHITECTURE altitude, got {proposal.altitude.value}"
                ),
            )
        if not proposal.architecture_changes:
            return self._fail(
                proposal,
                error_message="Proposal has no architecture changes",
            )

        pending = _PendingChanges()
        errors = self._collect_change_errors(proposal, context, pending)
        if errors:
            return self._fail(proposal, error_message="; ".join(errors))

        logger.info(
            META_DRY_RUN_COMPLETED,
            altitude="architecture",
            proposal_id=str(proposal.id),
            changes=len(proposal.architecture_changes),
        )
        return ApplyResult(
            success=True,
            changes_applied=len(proposal.architecture_changes),
        )

    def _collect_change_errors(
        self,
        proposal: ImprovementProposal,
        context: ArchitectureApplierContext,
        pending: _PendingChanges,
    ) -> list[str]:
        """Validate each architecture change, collecting failure strings.

        Returns:
            One error string per change that fails validation; empty when
            every change validates cleanly.
        """
        errors: list[str] = []
        for change in proposal.architecture_changes:
            try:
                errors.extend(
                    _validate_change(change, context=context, pending=pending)
                )
            except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                reraise_critical(exc)
                detail = (
                    f"context raised {type(exc).__name__}: "
                    f"{safe_error_description(exc)[:200]}"
                )
                logger.warning(
                    META_DRY_RUN_FAILED,
                    altitude="architecture",
                    proposal_id=str(proposal.id),
                    change_operation=change.operation,
                    change_target=change.target_name,
                    reason=detail,
                )
                errors.append(f"{change.operation}({change.target_name!r}): {detail}")
        return errors

    def _fail(
        self,
        proposal: ImprovementProposal,
        *,
        error_message: str,
    ) -> ApplyResult:
        """Build a failure ``ApplyResult`` and log the dry_run failure.

        Returns:
            ``ApplyResult`` instance.
        """
        logger.warning(
            META_DRY_RUN_FAILED,
            altitude="architecture",
            proposal_id=str(proposal.id),
            reason=error_message,
        )
        return ApplyResult(
            success=False,
            error_message=error_message,
            changes_applied=0,
        )


__all__ = ["ArchitectureApplier", "ArchitectureApplierContext"]
