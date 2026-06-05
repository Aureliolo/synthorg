"""Operational lifecycle mixin for the self-improvement service.

Startup prerequisite validation (GitHub token check), decided-proposal
outcome recording for Chief of Staff learning + cross-deployment
analytics, and resource teardown. The cycle orchestration lives in
``service``; this mixin owns the non-cycle operational surface.
"""

from collections.abc import Mapping
from typing import TYPE_CHECKING, Literal

from synthorg.core.critical_errors import reraise_critical
from synthorg.meta.chief_of_staff.models import ProposalOutcome
from synthorg.meta.models import (
    ImprovementProposal,
    ProposalAltitude,
    ProposalStatus,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.chief_of_staff import (
    COS_OUTCOME_RECORD_FAILED,
    COS_OUTCOME_SKIPPED,
)
from synthorg.observability.events.cross_deployment import (
    XDEPLOY_EVENT_EMIT_FAILED,
)
from synthorg.observability.events.meta import (
    META_CODE_GITHUB_CREDS_INVALID,
    META_CODE_GITHUB_CREDS_VALID,
    META_SERVICE_CLOSE_FAILED,
)

if TYPE_CHECKING:
    # Deferred to break a genuine import cycle: synthorg.meta.config pulls in
    # the config -> engine.coordination -> budget graph, which re-enters the
    # service package that imports this mixin. PEP 649 keeps these resolvable
    # for typing without the runtime import.
    from synthorg.meta.chief_of_staff.outcome_store import (
        MemoryBackendOutcomeStore,
    )
    from synthorg.meta.config import SelfImprovementConfig
    from synthorg.meta.protocol import ProposalApplier
    from synthorg.meta.telemetry.protocol import AnalyticsEmitter

logger = get_logger(__name__)


class SelfImprovementLifecycleMixin:
    """Startup validation, decision recording, and teardown.

    Relies on the concrete :class:`SelfImprovementService` to supply the
    config, appliers, outcome store, and analytics emitter.
    """

    _config: SelfImprovementConfig
    _appliers: Mapping[ProposalAltitude, ProposalApplier]
    _outcome_store: MemoryBackendOutcomeStore | None
    _analytics_emitter: AnalyticsEmitter | None

    async def validate_prerequisites(self) -> None:
        """Validate startup prerequisites.

        Verifies the GitHub token when code modification is enabled
        by pinging the GitHub API.

        Raises:
            GitHubAuthError: If the GitHub token is invalid.
            GitHubAPIError: On other GitHub API failures.
        """
        if not self._config.code_modification_enabled:
            return
        from synthorg.meta.appliers.code_applier import (  # noqa: PLC0415
            CodeApplier,
        )

        applier = self._appliers.get(ProposalAltitude.CODE_MODIFICATION)
        if applier is None or not isinstance(applier, CodeApplier):
            return
        from synthorg.meta.appliers.github_client import (  # noqa: PLC0415
            GitHubAPIError,
        )

        try:
            await applier.verify_github_token()
        except GitHubAPIError as exc:
            # Never let raw exception text leak into telemetry on
            # credential-bearing paths -- the GitHub API client may
            # include the bearer header in error messages.
            # ``safe_error_description`` is the project-wide redactor
            # mandated by CLAUDE.md ``## Logging``.
            logger.warning(
                META_CODE_GITHUB_CREDS_INVALID,
                reason="token_verification_failed",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise
        logger.info(META_CODE_GITHUB_CREDS_VALID)

    async def record_decision(
        self,
        proposal: ImprovementProposal,
    ) -> None:
        """Record a decided proposal for learning and analytics.

        Called by the approval API after a human approves or
        rejects a proposal. Emits analytics telemetry independently
        of Chief of Staff learning (outcome_store).

        Args:
            proposal: The decided proposal.
        """
        if proposal.decided_at is None or proposal.decided_by is None:
            logger.info(
                COS_OUTCOME_SKIPPED,
                proposal_id=str(proposal.id),
                reason="missing_decision_context",
            )
            return
        if proposal.status not in (
            ProposalStatus.APPROVED,
            ProposalStatus.REJECTED,
        ):
            logger.info(
                COS_OUTCOME_SKIPPED,
                proposal_id=str(proposal.id),
                reason="non_terminal_status",
                status=proposal.status.value,
            )
            return
        decision: Literal["approved", "rejected"] = (
            "approved" if proposal.status is ProposalStatus.APPROVED else "rejected"
        )
        outcome = ProposalOutcome(
            proposal_id=proposal.id,
            title=proposal.title,
            altitude=proposal.altitude,
            source_rule=proposal.source_rule,
            decision=decision,
            confidence_at_decision=proposal.confidence,
            decided_at=proposal.decided_at,
            decided_by=proposal.decided_by,
            decision_reason=proposal.decision_reason,
        )

        # Record outcome for Chief of Staff learning (if enabled).
        if self._outcome_store is not None:
            try:
                await self._outcome_store.record_outcome(outcome)
            except Exception as exc:
                reraise_critical(exc)
                logger.warning(
                    COS_OUTCOME_RECORD_FAILED,
                    proposal_id=str(proposal.id),
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )

        # Emit anonymized event for cross-deployment analytics.
        # emit_decision handles its own exceptions internally.
        if self._analytics_emitter is not None:
            await self._analytics_emitter.emit_decision(
                outcome,
                proposal=proposal,
            )

    async def close(self) -> None:
        """Flush analytics emitter, close appliers, and release resources."""
        for applier in self._appliers.values():
            close = getattr(applier, "aclose", None)
            if close is not None:
                try:
                    await close()
                except Exception as exc:
                    reraise_critical(exc)
                    logger.warning(
                        META_SERVICE_CLOSE_FAILED,
                        reason="applier_close_failed",
                        altitude=str(applier.altitude),
                        error_type=type(exc).__name__,
                        error=safe_error_description(exc),
                    )
        if self._analytics_emitter is not None:
            try:
                await self._analytics_emitter.aclose()
            except Exception as exc:
                reraise_critical(exc)
                logger.warning(
                    XDEPLOY_EVENT_EMIT_FAILED,
                    reason="emitter_close_failed",
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
