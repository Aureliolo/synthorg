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
from typing import Final

from synthorg.approval.enums import ApprovalRiskLevel
from synthorg.config.schema import ProviderConfig
from synthorg.observability import get_logger
from synthorg.observability.events.security import (
    SECURITY_LLM_EVAL_CROSS_FAMILY,
    SECURITY_LLM_EVAL_ERROR,
    SECURITY_LLM_EVAL_NO_PROVIDER,
    SECURITY_LLM_EVAL_SAME_FAMILY,
)
from synthorg.observability.redaction import safe_error_description
from synthorg.providers.base import BaseCompletionProvider
from synthorg.providers.family import get_family, shares_lineage
from synthorg.providers.registry import ProviderRegistry
from synthorg.security.config import (
    ArgumentTruncationStrategy,
    LlmFallbackConfig,
    LlmFallbackErrorPolicy,
    VerdictReasonVisibility,
)
from synthorg.security.models import (
    EvaluationConfidence,
    SecurityVerdict,
    SecurityVerdictType,
)
from synthorg.settings.bound_model import resolve_bound_model_live
from synthorg.settings.model_ref import ModelRef
from synthorg.settings.resolver_protocol import ConfigResolverProtocol

logger = get_logger(__name__)

# Maximum length for serialized arguments in the prompt.
_MAX_ARGS_DISPLAY: Final[int] = 1500

# Per-value truncation limit (chars) when using PER_VALUE or
# KEYS_AND_VALUES strategy.
_MAX_VALUE_LENGTH: Final[int] = 200

_MODEL_NAMESPACE: Final[str] = "security"
_MODEL_KEY: Final[str] = "llm_evaluator_model"


class _LlmEvaluatorSupportMixin:
    """Selection, serialization, reason, and error-policy helpers."""

    _registry: ProviderRegistry
    _configs: Mapping[str, ProviderConfig]
    _config: LlmFallbackConfig
    _config_resolver: ConfigResolverProtocol | None
    #: Last connection name warned about as unregistered, so a static
    #: misconfiguration is reported once rather than once per evaluation.
    _unregistered_warned: str | None = None

    # ------------------------------------------------------------------
    # Provider / model selection
    # ------------------------------------------------------------------

    async def _resolve_binding(
        self,
        agent_provider_name: str | None,
        agent_model_id: str | None = None,
    ) -> tuple[ModelRef, BaseCompletionProvider] | None:
        """Resolve the operator's evaluation pair and its live client.

        The evaluator judges an agent's own output, so it should run on a
        connection from a different vendor family: a jailbreak of one family
        must not also cover its reviewer. That choice belongs to the operator,
        who alone knows which of their connections is which vendor, so a
        family collision is warned about rather than silently re-picked. There
        is no cross-family scan and no first-available pick: substituting a
        connection would bill and rate-limit a judgement against an account
        nobody chose for it.

        Returns:
            The bound ``(ModelRef, driver)`` pair, or ``None`` when the
            assignment is unset or names an unregistered connection.
        """
        ref = await resolve_bound_model_live(
            self._config_resolver,
            namespace=_MODEL_NAMESPACE,
            key=_MODEL_KEY,
            unset_event=SECURITY_LLM_EVAL_NO_PROVIDER,
        )
        if ref is None:
            # No second log: resolve_bound_model_live already emitted this
            # event under unset_event above, and two WARNING lines for one
            # unset pair reads as two separate failures.
            return None
        if ref.provider not in self._registry:
            # Once per distinct name. This runs on every uncertain tool call,
            # and the condition changes only when the operator edits the
            # setting or the registry: repeating it per evaluation buries the
            # transient failures the same event also carries. The family
            # collision below is deliberately not rate-limited, because there
            # the operator's pair is being used and every judgement it decides
            # is worth a line.
            if self._unregistered_warned != ref.provider:
                self._unregistered_warned = ref.provider
                logger.warning(
                    SECURITY_LLM_EVAL_ERROR,
                    note="configured security-evaluation connection is not registered",
                    provider_name=ref.provider,
                    agent_provider=agent_provider_name,
                )
            return None
        self._unregistered_warned = None
        self._warn_on_family_collision(ref, agent_provider_name, agent_model_id)
        return ref, self._registry.get(ref.provider)

    def _warn_on_family_collision(
        self,
        evaluator_ref: ModelRef,
        agent_provider_name: str | None,
        agent_model_id: str | None,
    ) -> None:
        """Warn when the evaluator and the judged agent share a vendor family.

        The cross-family boundary is the point of LLM fallback, so a
        collision is an operator misconfiguration worth surfacing on every
        evaluation rather than a condition to silently work around.

        Both sides are resolved from their ``(provider, model)`` pair rather
        than the provider alone: an aggregating connection serves several
        organisations, so a provider-only comparison reads every model behind
        one endpoint as a single family.
        """
        if agent_provider_name is None:
            return
        evaluator_provider_name = evaluator_ref.provider
        agent_family = get_family(agent_provider_name, self._configs, agent_model_id)
        evaluator_family = get_family(
            evaluator_provider_name, self._configs, evaluator_ref.model_id
        )
        if not shares_lineage(evaluator_family, agent_family):
            logger.debug(
                SECURITY_LLM_EVAL_CROSS_FAMILY,
                selected_provider=evaluator_provider_name,
                agent_provider=agent_provider_name,
                agent_family=agent_family,
            )
            return
        logger.warning(
            SECURITY_LLM_EVAL_SAME_FAMILY,
            selected_provider=evaluator_provider_name,
            agent_provider=agent_provider_name,
            agent_family=agent_family,
            note=(
                "security.llm_evaluator_model shares the judged agent's vendor"
                " family; choose a connection from another family"
            ),
        )

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
        # Cap the total too: per-value truncation alone lets many keys
        # produce an unbounded blob, which would let the caller slice
        # through the untrusted-content fence applied downstream in
        # _build_messages.
        raw = self._safe_json_dumps(truncated)
        if len(raw) > _MAX_ARGS_DISPLAY:
            return raw[:_MAX_ARGS_DISPLAY] + "... [truncated]"
        return raw

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
        except (TypeError, ValueError) as exc:
            logger.debug(
                SECURITY_LLM_EVAL_ERROR,
                note="Failed to JSON-serialize arguments, using str() fallback",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
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
