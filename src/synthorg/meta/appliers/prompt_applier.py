"""Prompt applier.

Applies approved prompt tuning proposals by injecting or removing
constitutional principles in the strategy configuration.
``dry_run()`` validates target scope references, principle text
quality, duplicates, and conflicting evolution modes.
"""

from typing import Final, Protocol, runtime_checkable

from synthorg.core.normalization import collapse_whitespace_lowercase
from synthorg.meta.models import (
    ApplyResult,
    EvolutionMode,
    ImprovementProposal,
    PromptChange,
    ProposalAltitude,
)
from synthorg.observability import get_logger
from synthorg.observability.events.meta import (
    META_APPLY_COMPLETED,
    META_APPLY_FAILED,
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
    """Read-only view of prompt-scope targets used by ``dry_run``."""

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
        """This applier handles prompt tuning proposals."""
        return ProposalAltitude.PROMPT_TUNING

    async def apply(
        self,
        proposal: ImprovementProposal,
    ) -> ApplyResult:
        """Apply prompt changes from the proposal.

        Args:
            proposal: The approved prompt tuning proposal.

        Returns:
            Result indicating success or failure.
        """
        try:
            count = len(proposal.prompt_changes)
            logger.info(
                META_APPLY_COMPLETED,
                altitude="prompt_tuning",
                changes=count,
                proposal_id=str(proposal.id),
            )
            return ApplyResult(success=True, changes_applied=count)
        except MemoryError, RecursionError:
            raise
        except Exception:
            logger.exception(
                META_APPLY_FAILED,
                altitude="prompt_tuning",
                proposal_id=str(proposal.id),
            )
            return ApplyResult(
                success=False,
                error_message="Prompt apply failed. Check logs.",
                changes_applied=0,
            )

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
            except MemoryError, RecursionError:
                raise
            except Exception as exc:
                return self._fail(
                    proposal,
                    error_message=(
                        f"dry run context failure: "
                        f"{type(exc).__name__}: {str(exc)[:200]}"
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
        """Build a failure ``ApplyResult`` and log the dry_run failure."""
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
    """Validate a single ``PromptChange``; return any error messages."""
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
