"""Prompt applier.

Validates approved prompt tuning proposals (constitutional principles
injected or removed in the strategy configuration); ``apply()`` is a
documented stub pending the meta-apply mutation epic (see
:meth:`PromptApplier.apply`), matching the architecture / config
appliers. ``dry_run()`` validates target scope references, principle
text quality, duplicates, and conflicting evolution modes.
"""

from typing import Final, Protocol, runtime_checkable

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.normalization import collapse_whitespace_lowercase
from synthorg.meta.models import (
    ApplyResult,
    EvolutionMode,
    ImprovementProposal,
    PromptChange,
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

_PRINCIPLE_MIN_CHARS: Final[int] = 10
_PRINCIPLE_MAX_CHARS: Final[int] = 4000
_SCOPE_ALL = "all"


@runtime_checkable
class PromptApplierContext(Protocol):
    """Prompt-scope view + durable write seam for the prompt applier.

    The read methods (sync, served from an in-memory snapshot) back
    ``dry_run`` validation; the write methods (async) back the real
    ``apply`` path so a snapshot-and-rollback application can create durable
    active principles and undo them on partial failure.
    """

    def known_roles(self) -> frozenset[str]:
        """Return all registered role names."""
        ...

    def known_departments(self) -> frozenset[str]:
        """Return all registered department names."""
        ...

    def existing_principles(self, scope: str) -> frozenset[str]:
        """Return the set of already-registered principle texts in ``scope``.

        Callers normalize the principle text (case-insensitive, whitespace
        collapsed) before returning, so callers of this protocol can do
        a direct membership check.
        """
        ...

    def scope_overridden(self, scope: str) -> bool:
        """Return True when an ``OVERRIDE`` principle already exists at ``scope``."""
        ...

    async def create_principle(self, change: PromptChange) -> str:
        """Persist a durable active principle from ``change``.

        Returns:
            The new principle's id, for reverse-order rollback.

        Raises:
            Exception: On a durable-write failure (the applier rolls back).
        """
        ...

    async def delete_principle(self, principle_id: str) -> None:
        """Delete a previously-created active principle (rollback).

        Raises:
            Exception: On a durable-delete failure.
        """
        ...

    async def refresh_snapshot(self) -> None:
        """Reload the cached read snapshot after a successful apply."""
        ...


class PromptApplier:
    """Applies prompt tuning proposals.

    Args:
        context: Read-only view of prompt-scope targets.  Required for
            ``dry_run``; without it dry_run rejects with an explicit
            error so operators are never silently auto-approved.
    """

    def __init__(
        self,
        *,
        context: PromptApplierContext | None = None,
    ) -> None:
        """Store the read-only context."""
        self._context = context

    @property
    def altitude(self) -> ProposalAltitude:
        """This applier handles prompt tuning proposals.

        Returns:
            ``ProposalAltitude`` instance.
        """
        return ProposalAltitude.PROMPT_TUNING

    async def apply(
        self,
        proposal: ImprovementProposal,
    ) -> ApplyResult:
        """Apply prompt changes by persisting durable active principles.

        Each ``PromptChange`` is written through the context's durable write
        seam as an active principle. The application is transactional in the
        :class:`ConfigApplier` mould: created ids are tracked in order, and a
        mid-list failure triggers a reverse-order rollback that deletes the
        already-created principles before returning a failure result. On
        success the cached read snapshot is refreshed so the next prompt build
        sees the new principles without a restart.

        Args:
            proposal: The approved prompt tuning proposal.

        Returns:
            Result indicating success or failure.
        """
        if self._context is None:
            logger.warning(
                META_APPLY_FAILED,
                altitude="prompt_tuning",
                proposal_id=str(proposal.id),
                reason="no_context",
            )
            return ApplyResult(
                success=False,
                error_message=(
                    "PromptApplier.apply requires a PromptApplierContext; "
                    "none was injected"
                ),
                changes_applied=0,
            )
        context = self._context
        logger.info(
            META_APPLY_STARTED,
            altitude="prompt_tuning",
            proposal_id=str(proposal.id),
            changes=len(proposal.prompt_changes),
        )
        applied: list[str] = []
        try:
            for change in proposal.prompt_changes:
                principle_id = await context.create_principle(change)
                applied.append(principle_id)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            failures = await self._rollback(applied, proposal=proposal)
            log_exception_redacted(
                logger,
                META_APPLY_FAILED,
                exc,
                altitude="prompt_tuning",
                proposal_id=str(proposal.id),
                applied=len(applied),
                rollback_failures=failures,
            )
            return ApplyResult(
                success=False,
                error_message="Prompt apply failed and was rolled back. Check logs.",
                changes_applied=0,
            )
        await context.refresh_snapshot()
        logger.info(
            META_APPLY_COMPLETED,
            altitude="prompt_tuning",
            changes=len(applied),
            proposal_id=str(proposal.id),
        )
        return ApplyResult(success=True, changes_applied=len(applied))

    async def _rollback(
        self,
        applied: list[str],
        *,
        proposal: ImprovementProposal,
    ) -> int:
        """Delete previously-created principles after a failed apply.

        A rollback delete that itself fails is logged and skipped so one
        bad id cannot abort the rest of the restoration.

        Returns:
            The number of rollback deletes that failed; ``0`` means the
            store was fully restored.
        """
        if self._context is None:
            return 0
        context = self._context
        failures = 0
        for principle_id in reversed(applied):
            try:
                await context.delete_principle(principle_id)
            except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                reraise_critical(exc)
                failures += 1
                logger.warning(
                    META_APPLY_FAILED,
                    altitude="prompt_tuning",
                    proposal_id=str(proposal.id),
                    reason="rollback_delete_failed",
                    principle_id=principle_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
        return failures

    async def dry_run(
        self,
        proposal: ImprovementProposal,
    ) -> ApplyResult:
        """Validate prompt changes without applying.

        Args:
            proposal: The proposal to validate.

        Returns:
            Result indicating whether apply would succeed.
        """
        logger.info(
            META_DRY_RUN_STARTED,
            altitude="prompt_tuning",
            proposal_id=str(proposal.id),
            changes=len(proposal.prompt_changes),
        )
        if self._context is None:
            return self._fail(
                proposal,
                error_message=(
                    "PromptApplier.dry_run requires a PromptApplierContext; "
                    "none was injected"
                ),
            )
        if proposal.altitude != ProposalAltitude.PROMPT_TUNING:
            return self._fail(
                proposal,
                error_message=(
                    f"Expected PROMPT_TUNING altitude, got {proposal.altitude.value}"
                ),
            )
        if not proposal.prompt_changes:
            return self._fail(
                proposal,
                error_message="Proposal has no prompt changes",
            )

        errors: list[str] = []
        scopes_to_override: set[str] = set()
        seen_texts: dict[str, set[str]] = {}
        context = self._context

        for change in proposal.prompt_changes:
            try:
                errors.extend(
                    _validate_prompt_change(
                        change,
                        context=context,
                        scopes_to_override=scopes_to_override,
                        seen_texts=seen_texts,
                    )
                )
            except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                reraise_critical(exc)
                return self._fail(
                    proposal,
                    error_message=(
                        f"dry run context failure: "
                        f"{type(exc).__name__}: {safe_error_description(exc)[:200]}"
                    ),
                )

        if errors:
            return self._fail(proposal, error_message="; ".join(errors))

        logger.info(
            META_DRY_RUN_COMPLETED,
            altitude="prompt_tuning",
            proposal_id=str(proposal.id),
            changes=len(proposal.prompt_changes),
        )
        return ApplyResult(
            success=True,
            changes_applied=len(proposal.prompt_changes),
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
            altitude="prompt_tuning",
            proposal_id=str(proposal.id),
            reason=error_message,
        )
        return ApplyResult(
            success=False,
            error_message=error_message,
            changes_applied=0,
        )


def _validate_prompt_change(
    change: PromptChange,
    *,
    context: PromptApplierContext,
    scopes_to_override: set[str],
    seen_texts: dict[str, set[str]],
) -> list[str]:
    """Validate a single ``PromptChange``; return any error messages.

    Returns:
        List of the declared element type.
    """
    errors: list[str] = []

    scope = change.target_scope
    scope_is_valid = scope == _SCOPE_ALL or (
        scope in context.known_roles() or scope in context.known_departments()
    )
    if not scope_is_valid:
        errors.append(
            f"Unknown target_scope {scope!r}; "
            "expected 'all', a registered role name, "
            "or a registered department name"
        )
        # Skip downstream context lookups that would otherwise be
        # evaluated against an unknown scope and can legitimately raise.
        return errors

    text = change.principle_text
    normalized = collapse_whitespace_lowercase(text)
    # Length bounds run against the normalized content so excessive
    # whitespace cannot slip past ``_PRINCIPLE_MIN_CHARS`` nor shadow the
    # cap while collapsing down to the same canonical form used for
    # duplicate detection.
    if len(normalized) < _PRINCIPLE_MIN_CHARS:
        errors.append(
            f"principle_text too short (normalized len={len(normalized)} "
            f"< {_PRINCIPLE_MIN_CHARS})"
        )
    if len(normalized) > _PRINCIPLE_MAX_CHARS:
        errors.append(
            f"principle_text too long (normalized len={len(normalized)} "
            f"> {_PRINCIPLE_MAX_CHARS})"
        )

    in_proposal = seen_texts.setdefault(scope, set())
    if normalized in in_proposal:
        errors.append(f"Duplicate principle_text in proposal at scope {scope!r}")
    elif normalized in context.existing_principles(scope):
        errors.append(f"Principle already exists at scope {scope!r}")
    else:
        in_proposal.add(normalized)

    if change.evolution_mode == EvolutionMode.OVERRIDE:
        if scope in scopes_to_override:
            errors.append(f"Duplicate OVERRIDE for scope {scope!r} in proposal")
        elif context.scope_overridden(scope):
            errors.append(f"Scope {scope!r} already has an active OVERRIDE")
        else:
            scopes_to_override.add(scope)

    return errors
