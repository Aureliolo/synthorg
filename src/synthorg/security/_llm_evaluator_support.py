"""Support mixin for ``LlmSecurityEvaluator``.

Owns provider/model selection, tool-argument serialization, agent-visible
reason computation, and the LLM-failure error policy. Relies on
``_registry``, ``_configs``, and ``_config`` declared on the concrete
evaluator. The untrusted-content prompt construction (``_build_messages``
and the ``wrap_untrusted`` fence) stays on the evaluator itself.
"""

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

from synthorg.approval.enums import ApprovalRiskLevel
from synthorg.observability import get_logger
from synthorg.observability.events.security import (
    SECURITY_LLM_EVAL_CROSS_FAMILY,
    SECURITY_LLM_EVAL_ERROR,
    SECURITY_LLM_EVAL_NO_PROVIDER,
    SECURITY_LLM_EVAL_SAME_FAMILY_FALLBACK,
)
from synthorg.providers.family import get_family, providers_excluding_family
from synthorg.security.config import (
    ArgumentTruncationStrategy,
    LlmFallbackErrorPolicy,
    VerdictReasonVisibility,
)
from synthorg.security.models import (
    EvaluationConfidence,
    SecurityVerdict,
    SecurityVerdictType,
)

if TYPE_CHECKING:
    from synthorg.config.schema import ProviderConfig
    from synthorg.providers.base import BaseCompletionProvider
    from synthorg.providers.registry import ProviderRegistry
    from synthorg.security.config import LlmFallbackConfig

logger = get_logger(__name__)

# Maximum length for serialized arguments in the prompt.
_MAX_ARGS_DISPLAY: Final[int] = 1500

# Per-value truncation limit (chars) when using PER_VALUE or
# KEYS_AND_VALUES strategy.
_MAX_VALUE_LENGTH: Final[int] = 200


class _LlmEvaluatorSupportMixin:
    """Selection, serialization, reason, and error-policy helpers."""

    _registry: ProviderRegistry
    _configs: Mapping[str, ProviderConfig]
    _config: LlmFallbackConfig

    # ------------------------------------------------------------------
    # Provider / model selection
    # ------------------------------------------------------------------

    def _select_provider(
        self,
        agent_provider_name: str | None,
    ) -> tuple[str | None, BaseCompletionProvider | None]:
        """Select a provider for security evaluation.

        Prefers a provider from a different family than the agent's.
        Falls back to same-family with a warning if needed.

        Returns:
            ``(provider_name, driver)`` or ``(None, None)`` if no
            provider is available.
        """
        available = self._registry.list_providers()
        if not available:
            logger.warning(
                SECURITY_LLM_EVAL_NO_PROVIDER,
                agent_provider=agent_provider_name,
            )
            return None, None

        if agent_provider_name is not None:
            result = self._try_cross_family(
                agent_provider_name,
                available,
            )
            if result is not None:
                return result

        name = available[0]
        logger.debug(
            SECURITY_LLM_EVAL_CROSS_FAMILY,
            selected_provider=name,
            agent_provider=agent_provider_name,
            note="Using first available provider",
        )
        return name, self._registry.get(name)

    def _try_cross_family(
        self,
        agent_provider_name: str,
        available: tuple[str, ...],
    ) -> tuple[str, BaseCompletionProvider] | None:
        """Try to select a cross-family provider.

        Returns:
            A ``(name, driver)`` pair for a provider in a different
            family, or ``None`` to fall back to the first available
            provider.
        """
        agent_family = get_family(agent_provider_name, self._configs)
        cross_family = providers_excluding_family(
            agent_family,
            self._configs,
        )
        cross_family = tuple(p for p in cross_family if p in available)
        if cross_family:
            name = cross_family[0]
            logger.debug(
                SECURITY_LLM_EVAL_CROSS_FAMILY,
                selected_provider=name,
                agent_provider=agent_provider_name,
                agent_family=agent_family,
            )
            return name, self._registry.get(name)

        logger.warning(
            SECURITY_LLM_EVAL_SAME_FAMILY_FALLBACK,
            agent_provider=agent_provider_name,
            agent_family=agent_family,
            note="No cross-family provider available",
        )
        return None

    def _select_model(self, provider_name: str) -> str:
        """Select the model to use for security evaluation.

        Uses explicit config model if set, otherwise picks the first
        model from the selected provider's config.

        Returns:
            The model alias or id to evaluate with; falls back to the
            provider name when no model is configured.
        """
        if self._config.model is not None:
            return self._config.model

        config = self._configs.get(provider_name)
        if config is not None and config.models:
            first = config.models[0]
            return first.alias or first.id

        # Last resort: use provider name as model hint (likely to
        # fail at the driver level; error policy will handle it).
        logger.warning(
            SECURITY_LLM_EVAL_ERROR,
            note=(
                f"No model configured for provider {provider_name!r}, "
                "using provider name as model hint"
            ),
            provider_name=provider_name,
        )
        return provider_name

    # ------------------------------------------------------------------
    # Argument serialization
    # ------------------------------------------------------------------

    def _serialize_arguments(
        self,
        arguments: dict[str, object],
    ) -> str:
        """Serialize tool arguments using the configured strategy.

        Returns:
            The serialised (and truncated) argument string.
        """
        strategy = self._config.argument_truncation

        if strategy in (
            ArgumentTruncationStrategy.PER_VALUE,
            ArgumentTruncationStrategy.KEYS_AND_VALUES,
        ):
            return self._serialize_per_value(arguments)

        # WHOLE_STRING (legacy): truncate the serialized JSON.
        return self._serialize_whole_string(arguments)

    def _serialize_whole_string(
        self,
        arguments: dict[str, object],
    ) -> str:
        """Serialize and truncate the full JSON string.

        Returns:
            The JSON string, truncated to ``_MAX_ARGS_DISPLAY``.
        """
        raw = self._safe_json_dumps(arguments)
        if len(raw) > _MAX_ARGS_DISPLAY:
            return raw[:_MAX_ARGS_DISPLAY] + "... [truncated]"
        return raw

    def _serialize_per_value(
        self,
        arguments: dict[str, object],
    ) -> str:
        """Truncate each value individually, preserving all keys.

        Returns:
            The JSON string with per-value truncation applied.
        """
        truncated: dict[str, object] = {}
        for key, value in arguments.items():
            str_val = self._safe_json_dumps(value)
            if len(str_val) > _MAX_VALUE_LENGTH:
                truncated[key] = str_val[:_MAX_VALUE_LENGTH] + "...[cut]"
            else:
                truncated[key] = value
        return self._safe_json_dumps(truncated)

    def _safe_json_dumps(self, obj: object) -> str:
        """JSON-serialize with fallback to str() on failure.

        Returns:
            The JSON string, or ``str(obj)`` if serialisation fails.
        """
        try:
            return json.dumps(
                obj,
                indent=None,
                default=str,
                ensure_ascii=False,
            )
        except TypeError, ValueError:
            logger.debug(
                SECURITY_LLM_EVAL_ERROR,
                note="Failed to JSON-serialize arguments, using str() fallback",
            )
            return str(obj)

    # ------------------------------------------------------------------
    # Agent-visible reason + error policy
    # ------------------------------------------------------------------

    def _compute_agent_reason(
        self,
        verdict: SecurityVerdictType,
        risk_level: ApprovalRiskLevel,
        full_reason: str,
    ) -> str:
        """Compute the reason string visible to the evaluated agent.

        Returns:
            The reason string at the configured visibility level (full,
            category-only, or generic).
        """
        visibility = self._config.reason_visibility

        if visibility == VerdictReasonVisibility.FULL:
            return full_reason

        if visibility == VerdictReasonVisibility.CATEGORY:
            return f"Security evaluation: {verdict.value} (risk: {risk_level.value})"

        # GENERIC (default): no details.
        action = (
            "denied"
            if verdict == SecurityVerdictType.DENY
            else (
                "escalated for review"
                if verdict == SecurityVerdictType.ESCALATE
                else "evaluated"
            )
        )
        return f"Security evaluation {action} this action."

    def _apply_error_policy(
        self,
        rule_verdict: SecurityVerdict,
        reason: str,
    ) -> SecurityVerdict:
        """Apply the configured error policy.

        Args:
            rule_verdict: Original rule engine verdict to fall back to.
            reason: Why the LLM evaluation failed.

        Returns:
            A ``SecurityVerdict`` based on the error policy.
        """
        policy = self._config.on_error
        now = datetime.now(UTC)

        if policy == LlmFallbackErrorPolicy.ESCALATE:
            return SecurityVerdict(
                verdict=SecurityVerdictType.ESCALATE,
                reason=f"{reason} -- escalated per error policy",
                risk_level=ApprovalRiskLevel.HIGH,
                confidence=EvaluationConfidence.LOW,
                evaluated_at=now,
                evaluation_duration_ms=0.0,
            )

        if policy == LlmFallbackErrorPolicy.DENY:
            return SecurityVerdict(
                verdict=SecurityVerdictType.DENY,
                reason=f"{reason} -- denied per error policy",
                risk_level=ApprovalRiskLevel.HIGH,
                confidence=EvaluationConfidence.LOW,
                evaluated_at=now,
                evaluation_duration_ms=0.0,
            )

        # USE_RULE_VERDICT: return original verdict with failure context.
        return rule_verdict.model_copy(
            update={
                "reason": (f"{rule_verdict.reason} (LLM fallback failed: {reason})"),
            },
        )
