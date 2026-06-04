"""Architecture applier.

Applies approved architecture proposals by creating new roles,
departments, or modifying workflows in the appropriate registries.
``dry_run()`` validates each ``ArchitectureChange`` against a
read-only view of those registries, so operators can preview whether
``apply()`` would succeed without mutating state. The per-operation
validators live in ``_architecture_validators``.
"""

from typing import Protocol, runtime_checkable

from synthorg.core.critical_errors import reraise_critical
from synthorg.meta.appliers._architecture_validators import (
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
    META_DRY_RUN_COMPLETED,
    META_DRY_RUN_FAILED,
    META_DRY_RUN_STARTED,
)

logger = get_logger(__name__)


@runtime_checkable
class ArchitectureApplierContext(Protocol):
    """Read-only view of role/department/workflow registries."""

    def has_role(self, name: str) -> bool:
        """Return True when a role with ``name`` is registered."""
        ...

    def has_department(self, name: str) -> bool:
        """Return True when a department with ``name`` is registered."""
        ...

    def has_workflow(self, name: str) -> bool:
        """Return True when a workflow with ``name`` is registered."""
        ...

    def role_in_use(self, name: str) -> bool:
        """Return True when removing the role would dangle references."""
        ...

    def department_in_use(self, name: str) -> bool:
        """Return True when removing the department would dangle references."""
        ...


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
        """Apply architecture changes from the proposal.

        .. warning::
            Registry mutation is **not** implemented here.  The
            ``dry_run`` validator is shipped; the mutating ``apply``
            path still needs a mutation protocol on
            ``ArchitectureApplierContext`` and a transactional registry
            writer -- tracked separately.  For now ``apply()`` counts
            the changes and logs ``META_APPLY_COMPLETED`` so the
            meta-loop's bookkeeping stays consistent with the other
            appliers (config / prompt) that follow the same pattern.
            Callers that need real state changes must not rely on this
            method yet.

        Args:
            proposal: The approved architecture proposal.

        Returns:
            Result indicating the count of changes "applied" and,
            until real apply lands, ``success=True`` with no side
            effects.  Raises only ``MemoryError`` / ``RecursionError``.
        """
        try:
            count = len(proposal.architecture_changes)
            logger.info(
                META_APPLY_COMPLETED,
                altitude="architecture",
                changes=count,
                proposal_id=str(proposal.id),
                note="registry mutation not yet implemented",
            )
            return ApplyResult(success=True, changes_applied=count)
        except Exception as exc:
            reraise_critical(exc)
            log_exception_redacted(
                logger,
                META_APPLY_FAILED,
                exc,
                altitude="architecture",
                proposal_id=str(proposal.id),
            )
            return ApplyResult(
                success=False,
                error_message="Architecture apply failed. Check logs.",
                changes_applied=0,
            )

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
        errors: list[str] = []
        for change in proposal.architecture_changes:
            try:
                errors.extend(
                    _validate_change(change, context=context, pending=pending)
                )
            except Exception as exc:
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
