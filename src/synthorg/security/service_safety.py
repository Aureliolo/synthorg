"""Safety classifier + uncertainty check mixin for ``SecOpsService``.

Owns ``_build_deny_reason``, ``_run_safety_classifier``,
``_process_classification``, ``_handle_blocked_denial``, and
``_run_uncertainty_check``.  Relies on ``_safety_classifier``,
``_denial_tracker``, ``_uncertainty_checker``, and ``_config``
declared on the concrete service.
"""

from typing import TYPE_CHECKING

from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, log_exception_redacted
from synthorg.observability.events.security import (
    SECURITY_SAFETY_CLASSIFY_BLOCKED,
    SECURITY_SAFETY_CLASSIFY_ERROR,
    SECURITY_SAFETY_CLASSIFY_SUSPICIOUS,
    SECURITY_TIER_SAFE_TOOL,
    SECURITY_UNCERTAINTY_CHECK_ERROR,
)
from synthorg.security.denial_tracker import DenialAction, DenialTracker
from synthorg.security.safety_classifier import (
    PermissionTier,
    SafetyClassification,
)

if TYPE_CHECKING:
    from synthorg.security.config import SecurityConfig
    from synthorg.security.models import SecurityContext, SecurityVerdict
    from synthorg.security.safety_classifier import SafetyClassifier
    from synthorg.security.uncertainty import UncertaintyChecker

logger = get_logger(__name__)


class SecOpsServiceSafetyMixin:
    """Safety classifier + uncertainty check for ``SecOpsService``."""

    _config: SecurityConfig
    _safety_classifier: SafetyClassifier | None
    _denial_tracker: DenialTracker | None
    _uncertainty_checker: UncertaintyChecker | None

    @staticmethod
    def _build_deny_reason(
        base_reason: str,
        metadata: dict[str, str],
    ) -> str:
        """Build DENY reason -- retry hint when tracker signals RETRY."""
        if metadata.get("denial_action") == DenialAction.RETRY:
            return f"{base_reason} (blocked -- agent may retry with safer approach)"
        return f"{base_reason} (auto-rejected: safety classifier blocked)"

    async def _run_safety_classifier(
        self,
        context: SecurityContext,
        verdict: SecurityVerdict,
        metadata: dict[str, str],
    ) -> bool:
        """Run the safety classifier and populate metadata.

        Returns ``True`` if the action should be auto-rejected,
        ``False`` otherwise.  SAFE_TOOL tier bypasses the classifier.
        On error, returns ``False`` (fail-safe: proceed to review).
        """
        assert self._safety_classifier is not None  # noqa: S101 -- caller guarantees

        tier = self._safety_classifier.classify_tier(context.action_type)
        if tier == PermissionTier.SAFE_TOOL:
            logger.info(
                SECURITY_TIER_SAFE_TOOL,
                tool_name=context.tool_name,
                action_type=context.action_type,
            )
            return False

        try:
            result = await self._safety_classifier.classify(
                verdict.reason,
                context.action_type,
                context.tool_name,
                verdict.risk_level,
            )
            metadata["safety_classification"] = result.classification.value
            metadata["stripped_description"] = result.stripped_description
            metadata["safety_reason"] = result.reason

            return self._process_classification(
                result.classification,
                context,
                result.reason,
                metadata,
            )
        except Exception as exc:
            reraise_critical(exc)
            log_exception_redacted(
                logger,
                SECURITY_SAFETY_CLASSIFY_ERROR,
                exc,
                tool_name=context.tool_name,
                note="Safety classifier failed -- proceeding without classification",
            )
        return False

    def _process_classification(
        self,
        classification: SafetyClassification,
        context: SecurityContext,
        reason: str,
        metadata: dict[str, str],
    ) -> bool:
        """Process classification: denial tracking + consecutive reset."""
        agent_id = context.agent_id or "unknown"

        if (
            classification == SafetyClassification.BLOCKED
            and self._config.safety_classifier.auto_reject_blocked
        ):
            return self._handle_blocked_denial(
                agent_id,
                context.tool_name,
                reason,
                metadata,
            )

        if self._denial_tracker is not None:
            self._denial_tracker.reset_consecutive(agent_id)

        if classification == SafetyClassification.SUSPICIOUS:
            logger.warning(
                SECURITY_SAFETY_CLASSIFY_SUSPICIOUS,
                tool_name=context.tool_name,
                reason=reason,
            )
        return False

    def _handle_blocked_denial(
        self,
        agent_id: str,
        tool_name: str,
        reason: str,
        metadata: dict[str, str],
    ) -> bool:
        """Handle BLOCKED with denial tracking.

        Returns ``True`` when the request should be auto-rejected
        and ``False`` when max denials are reached and the action
        should proceed to human approval instead.
        """
        if self._denial_tracker is None:
            logger.warning(
                SECURITY_SAFETY_CLASSIFY_BLOCKED,
                tool_name=tool_name,
                reason=reason,
            )
            return True

        action = self._denial_tracker.record_denial(agent_id)
        metadata["denial_action"] = action.value
        consecutive, total = self._denial_tracker.get_counts(agent_id)
        metadata["denial_consecutive"] = str(consecutive)
        metadata["denial_total"] = str(total)

        if action == DenialAction.ESCALATE:
            logger.warning(
                SECURITY_SAFETY_CLASSIFY_BLOCKED,
                tool_name=tool_name,
                reason=reason,
                note="max denials reached -- escalating to human",
                consecutive=consecutive,
                total=total,
            )
            return False

        logger.warning(
            SECURITY_SAFETY_CLASSIFY_BLOCKED,
            tool_name=tool_name,
            reason=f"{reason} -- agent may retry with safer approach",
            consecutive=consecutive,
            total=total,
        )
        return True

    async def _run_uncertainty_check(
        self,
        prompt: str,
        metadata: dict[str, str],
    ) -> None:
        """Run the uncertainty checker and populate metadata."""
        assert self._uncertainty_checker is not None  # noqa: S101 -- caller guarantees
        try:
            result = await self._uncertainty_checker.check(
                prompt,
            )
            metadata["uncertainty_provider_count"] = str(
                result.provider_count,
            )
            if result.provider_count >= 2:  # noqa: PLR2004
                metadata["confidence_score"] = str(
                    result.confidence_score,
                )
                threshold = self._config.uncertainty_check.low_confidence_threshold
                if result.confidence_score < threshold:
                    metadata["low_confidence"] = "true"
            else:
                metadata["uncertainty_check_skipped"] = "true"
            if result.keyword_overlap is not None:
                metadata["keyword_overlap"] = str(result.keyword_overlap)
            if result.embedding_similarity is not None:
                metadata["embedding_similarity"] = str(
                    result.embedding_similarity,
                )
        except Exception as exc:
            reraise_critical(exc)
            log_exception_redacted(
                logger,
                SECURITY_UNCERTAINTY_CHECK_ERROR,
                exc,
                note="Uncertainty check failed -- proceeding without score",
            )
            metadata["uncertainty_check_error"] = "true"
