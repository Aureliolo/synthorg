"""LLM-based security evaluator for uncertain rule engine verdicts.

When the rule engine cannot classify an action (no rule matched,
``EvaluationConfidence.LOW``), this evaluator routes the security
context to an LLM from a different provider family for cross-
validation.  The LLM returns a structured verdict via tool calling.

Design invariants:
    - Hard-deny rules always have HIGH confidence and are never
      re-evaluated by the LLM.
    - Only LOW-confidence ALLOW verdicts are re-evaluated -- LOW-
      confidence DENY/ESCALATE verdicts from custom rules are never
      sent to the LLM (enforced by ``SecOpsService``).
    - Full-autonomy mode skips LLM evaluation entirely (enforced
      by ``SecOpsService``, not here).
    - Cross-family selection is best-effort: same-family with a
      warning if no alternative exists.
    - LLM failures apply the configured error policy
      (``LlmFallbackErrorPolicy``).
"""

import asyncio
import json
import re

# ``Mapping``, ``CostTracker``, ``ProviderConfig`` and
# ``ProviderRegistry`` are part of ``LlmSecurityEvaluator.__init__``'s
# public annotation, so they must resolve at runtime when downstream
# tooling evaluates type hints (DI containers, doc generators).
from collections.abc import Mapping  # noqa: TC003
from datetime import UTC, datetime
from types import MappingProxyType
from typing import TYPE_CHECKING, Final

from synthorg.budget.call_category import LLMCallCategory
from synthorg.budget.tracker import CostTracker  # noqa: TC001
from synthorg.config.schema import ProviderConfig  # noqa: TC001
from synthorg.core.clock import Clock, SystemClock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.enums import ApprovalRiskLevel
from synthorg.core.types import NotBlankStr
from synthorg.engine.prompt_safety import (
    TAG_TOOL_ARGUMENTS,
    untrusted_content_directive,
    wrap_untrusted,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.security import (
    SECURITY_LLM_EVAL_COMPLETE,
    SECURITY_LLM_EVAL_CROSS_FAMILY,
    SECURITY_LLM_EVAL_ERROR,
    SECURITY_LLM_EVAL_NO_PROVIDER,
    SECURITY_LLM_EVAL_SAME_FAMILY_FALLBACK,
    SECURITY_LLM_EVAL_START,
    SECURITY_LLM_EVAL_TIMEOUT,
)
from synthorg.providers.cost_recording import cost_recording_scope
from synthorg.providers.enums import MessageRole
from synthorg.providers.family import get_family, providers_excluding_family
from synthorg.providers.models import ChatMessage, CompletionConfig, ToolDefinition
from synthorg.providers.registry import ProviderRegistry  # noqa: TC001
from synthorg.security.config import (
    ArgumentTruncationStrategy,
    LlmFallbackConfig,
    LlmFallbackErrorPolicy,
    VerdictReasonVisibility,
)
from synthorg.security.models import (
    EvaluationConfidence,
    SecurityContext,
    SecurityVerdict,
    SecurityVerdictType,
)

if TYPE_CHECKING:
    from synthorg.providers.base import BaseCompletionProvider
    from synthorg.providers.models import CompletionResponse

logger = get_logger(__name__)

# Maximum length for serialized arguments in the prompt.
_MAX_ARGS_DISPLAY: Final[int] = 1500

# Per-value truncation limit (chars) when using PER_VALUE or
# KEYS_AND_VALUES strategy.
_MAX_VALUE_LENGTH: Final[int] = 200

# Maximum length for LLM-returned reason string (defense against
# prompt injection exfiltration -- the reason flows into audit log
# and approval queue).
_MAX_REASON_LENGTH: Final[int] = 300

_MILLISECONDS_PER_SECOND: Final[float] = 1000.0

# Regex to strip control characters from LLM-returned reason.
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f]")

# Derive valid values and mappings from the source enums so they
# stay in sync automatically when enum members are added.
_VALID_VERDICTS = frozenset(v.value for v in SecurityVerdictType)
_VALID_RISK_LEVELS = frozenset(v.value for v in ApprovalRiskLevel)

_RISK_LEVEL_MAP: MappingProxyType[str, ApprovalRiskLevel] = MappingProxyType(
    {v.value: v for v in ApprovalRiskLevel},
)
_VERDICT_MAP: MappingProxyType[str, SecurityVerdictType] = MappingProxyType(
    {v.value: v for v in SecurityVerdictType},
)

# Tool schema for structured LLM response.  Enum arrays are derived
# from the source enums to prevent drift.
_SECURITY_VERDICT_TOOL = ToolDefinition(
    name="security_verdict",
    description=(
        "Submit your security evaluation verdict for the proposed "
        "agent action.  You MUST call this tool with your assessment."
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "verdict": {
                "type": "string",
                "enum": sorted(_VALID_VERDICTS),
                "description": (
                    "Security verdict: allow (safe), deny (unsafe), "
                    "or escalate (needs human review)."
                ),
            },
            "risk_level": {
                "type": "string",
                "enum": sorted(_VALID_RISK_LEVELS),
                "description": "Assessed risk level of the action.",
            },
            "reason": {
                "type": "string",
                "description": ("Brief explanation for the verdict (1-2 sentences)."),
            },
        },
        "required": ["verdict", "risk_level", "reason"],
        "additionalProperties": False,
    },
)

_SYSTEM_PROMPT = (
    "You are a security evaluator for an AI agent orchestration system. "
    "An agent is requesting to execute a tool action.  The rule-based "
    "security engine could not classify this action and needs your "
    "assessment.\n\n"
    "Evaluate whether the action is safe to execute.  Consider:\n"
    "- Could this action expose credentials or sensitive data?\n"
    "- Could this action cause data loss or destruction?\n"
    "- Are the arguments suspicious or potentially malicious?\n"
    "- Is this action appropriate for the stated context?\n\n"
    "You MUST call the security_verdict tool with your assessment.  "
    "Do not respond with text -- only use the tool.\n\n"
    + untrusted_content_directive((TAG_TOOL_ARGUMENTS,))
)


class LlmSecurityEvaluator:
    """Evaluates uncertain security contexts using an LLM.

    Selects a model from a different provider family than the agent
    being evaluated, sends a structured prompt, and parses the tool-
    call response into a ``SecurityVerdict``.

    Args:
        provider_registry: Registry of provider drivers.
        provider_configs: Provider config dict for family lookup.
        config: LLM fallback configuration.
    """

    def __init__(
        self,
        *,
        provider_registry: ProviderRegistry,
        provider_configs: Mapping[str, ProviderConfig],
        config: LlmFallbackConfig,
        cost_tracker: CostTracker | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._registry = provider_registry
        self._configs = provider_configs
        self._config = config
        self._cost_tracker = cost_tracker
        self._clock: Clock = clock or SystemClock()

    async def evaluate(
        self,
        context: SecurityContext,
        rule_verdict: SecurityVerdict,
    ) -> SecurityVerdict:
        """Run LLM-based security evaluation.

        Args:
            context: The tool invocation security context.
            rule_verdict: The original rule engine verdict (LOW
                confidence).

        Returns:
            A ``SecurityVerdict``.  On successful LLM evaluation the
            verdict has ``EvaluationConfidence.HIGH``.  On failure
            the result depends on the configured error policy: the
            original rule engine verdict (``USE_RULE_VERDICT``), a
            ``DENY`` verdict, or an ``ESCALATE`` verdict -- all with
            LOW confidence.
        """
        start = self._clock.monotonic()
        logger.info(
            SECURITY_LLM_EVAL_START,
            tool_name=context.tool_name,
            action_type=context.action_type,
            agent_provider=context.agent_provider_name,
        )

        provider_name, driver = self._select_provider(
            context.agent_provider_name,
        )
        if provider_name is None or driver is None:
            return self._apply_error_policy(
                rule_verdict,
                "No provider available for LLM security evaluation",
            )

        model = self._select_model(provider_name)
        response = await self._call_llm(
            driver,
            model,
            context,
            rule_verdict,
            start,
        )
        if isinstance(response, SecurityVerdict):
            return response  # Error policy already applied.

        verdict = self._parse_llm_response(response, rule_verdict, start)
        self._log_completion(context, verdict, response, start)
        return verdict

    # ------------------------------------------------------------------
    # LLM invocation
    # ------------------------------------------------------------------

    async def _call_llm(
        self,
        driver: BaseCompletionProvider,
        model: str,
        context: SecurityContext,
        rule_verdict: SecurityVerdict,
        start: float,
    ) -> CompletionResponse | SecurityVerdict:
        """Call the LLM with the security context.

        Returns the ``CompletionResponse`` on success, or a
        ``SecurityVerdict`` (from error policy) on failure.
        """
        messages = self._build_messages(context)
        # Per-task task_id when the calling context carries one so
        # cost rollups split per evaluated task instead of collapsing
        # every security evaluation into one synthetic bucket.  Falls
        # back to the synthetic id for callers that don't set
        # ``context.task_id`` (rare -- most paths attribute through).
        scope_task_id = (
            context.task_id
            if context.task_id is not None
            else NotBlankStr("system:security:llm_evaluator")
        )
        scope_agent_id = (
            context.agent_id if context.agent_id is not None else NotBlankStr("system")
        )
        try:
            async with cost_recording_scope(
                cost_tracker=self._cost_tracker,
                agent_id=scope_agent_id,
                task_id=scope_task_id,
                call_category=LLMCallCategory.SYSTEM,
            ):
                return await asyncio.wait_for(
                    driver.complete(
                        messages,
                        model,
                        tools=[_SECURITY_VERDICT_TOOL],
                        config=CompletionConfig(
                            temperature=self._config.temperature,
                            top_p=self._config.top_p,
                            max_tokens=256,
                        ),
                    ),
                    timeout=self._config.timeout_seconds,
                )
        except TimeoutError:
            return self._on_llm_timeout(context, rule_verdict, start)
        except Exception as exc:
            reraise_critical(exc)
            return self._on_llm_error(
                context,
                rule_verdict,
                start,
                exc,
            )

    def _on_llm_timeout(
        self,
        context: SecurityContext,
        rule_verdict: SecurityVerdict,
        start: float,
    ) -> SecurityVerdict:
        """Handle LLM call timeout."""
        duration_ms = (self._clock.monotonic() - start) * _MILLISECONDS_PER_SECOND
        logger.warning(
            SECURITY_LLM_EVAL_TIMEOUT,
            tool_name=context.tool_name,
            timeout_seconds=self._config.timeout_seconds,
            duration_ms=duration_ms,
        )
        return self._apply_error_policy(
            rule_verdict,
            f"LLM evaluation timed out after {self._config.timeout_seconds}s",
        )

    def _on_llm_error(
        self,
        context: SecurityContext,
        rule_verdict: SecurityVerdict,
        start: float,
        exc: Exception,
    ) -> SecurityVerdict:
        """Handle unexpected LLM call errors."""
        duration_ms = (self._clock.monotonic() - start) * _MILLISECONDS_PER_SECOND
        # Use ``logger.warning`` + ``safe_error_description``
        # instead of ``logger.exception`` so we don't emit a
        # traceback that could leak credential-bearing locals
        # (provider auth headers, request bodies) on this
        # credential-bearing path.
        logger.warning(
            SECURITY_LLM_EVAL_ERROR,
            tool_name=context.tool_name,
            duration_ms=duration_ms,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return self._apply_error_policy(
            rule_verdict,
            "LLM evaluation failed",
        )

    def _log_completion(
        self,
        context: SecurityContext,
        verdict: SecurityVerdict,
        response: CompletionResponse,
        start: float,
    ) -> None:
        """Log a successful LLM evaluation completion."""
        duration_ms = (self._clock.monotonic() - start) * _MILLISECONDS_PER_SECOND
        logger.info(
            SECURITY_LLM_EVAL_COMPLETE,
            tool_name=context.tool_name,
            verdict=verdict.verdict.value,
            risk_level=verdict.risk_level.value,
            confidence=verdict.confidence.value,
            duration_ms=duration_ms,
            cost=response.usage.cost,
            model=response.model,
        )

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

        Returns ``(name, driver)`` on success, or ``None`` to fall
        back to the first available provider.
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
    # Prompt construction
    # ------------------------------------------------------------------

    def _build_messages(
        self,
        context: SecurityContext,
    ) -> list[ChatMessage]:
        """Build the LLM prompt messages from the security context."""
        args_str = self._serialize_arguments(context.arguments)
        # The serialised tool arguments are the only
        # attacker-controllable field in this prompt -- wrap them in
        # the ``<tool-arguments>`` fence declared by
        # ``_SYSTEM_PROMPT`` so the model treats them as data, and a
        # literal ``</tool-arguments>`` inside ``args_str`` cannot
        # break out of the fence.
        fenced_args = wrap_untrusted(TAG_TOOL_ARGUMENTS, args_str)

        # Structural ``<action>...</action>`` framing groups the trusted
        # metadata (tool name, category, agent/task ids) around the
        # fenced argument payload.
        user_content = (
            "<action>\n"
            f"  <tool>{context.tool_name}</tool>\n"
            f"  <type>{context.action_type}</type>\n"
            f"  <category>{context.tool_category.value}</category>\n"
            f"  <agent>{context.agent_id or 'unknown'}</agent>\n"
            f"  <task>{context.task_id or 'unknown'}</task>\n"
            f"  <arguments>\n{fenced_args}\n  </arguments>\n"
            "</action>"
        )

        # Enforce max_input_tokens budget (approx. 4 chars per token).
        max_chars = self._config.max_input_tokens * 4
        if len(user_content) > max_chars:
            user_content = user_content[:max_chars] + "\n... [truncated]"

        return [
            ChatMessage(role=MessageRole.SYSTEM, content=_SYSTEM_PROMPT),
            ChatMessage(role=MessageRole.USER, content=user_content),
        ]

    def _serialize_arguments(
        self,
        arguments: dict[str, object],
    ) -> str:
        """Serialize tool arguments using the configured strategy."""
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
        """Serialize and truncate the full JSON string."""
        raw = self._safe_json_dumps(arguments)
        if len(raw) > _MAX_ARGS_DISPLAY:
            return raw[:_MAX_ARGS_DISPLAY] + "... [truncated]"
        return raw

    def _serialize_per_value(
        self,
        arguments: dict[str, object],
    ) -> str:
        """Truncate each value individually, preserving all keys."""
        truncated: dict[str, object] = {}
        for key, value in arguments.items():
            str_val = self._safe_json_dumps(value)
            if len(str_val) > _MAX_VALUE_LENGTH:
                truncated[key] = str_val[:_MAX_VALUE_LENGTH] + "...[cut]"
            else:
                truncated[key] = value
        return self._safe_json_dumps(truncated)

    def _safe_json_dumps(self, obj: object) -> str:
        """JSON-serialize with fallback to str() on failure."""
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
    # Response parsing
    # ------------------------------------------------------------------

    def _parse_llm_response(
        self,
        response: CompletionResponse,
        rule_verdict: SecurityVerdict,
        start: float,
    ) -> SecurityVerdict:
        """Parse the LLM response into a SecurityVerdict.

        Falls back to error policy on parse failure.
        """
        for tc in response.tool_calls:
            if tc.name == "security_verdict":
                return self._parse_tool_call_args(
                    tc.arguments,
                    rule_verdict,
                    start,
                )

        logger.warning(
            SECURITY_LLM_EVAL_ERROR,
            note="LLM did not call security_verdict tool",
            tool_calls=[tc.name for tc in response.tool_calls],
        )
        return self._apply_error_policy(
            rule_verdict,
            "LLM did not call the security_verdict tool",
        )

    def _parse_tool_call_args(
        self,
        args: dict[str, object],
        rule_verdict: SecurityVerdict,
        start: float,
    ) -> SecurityVerdict:
        """Parse tool call arguments into a SecurityVerdict."""
        raw_verdict = args.get("verdict", "")
        raw_risk = args.get("risk_level", "")
        raw_reason = args.get("reason", "")

        if raw_verdict not in _VALID_VERDICTS:
            logger.warning(
                SECURITY_LLM_EVAL_ERROR,
                note=f"Invalid verdict value: {raw_verdict!r}",
            )
            return self._apply_error_policy(
                rule_verdict,
                f"LLM returned invalid verdict: {raw_verdict!r}",
            )

        if raw_risk not in _VALID_RISK_LEVELS:
            logger.warning(
                SECURITY_LLM_EVAL_ERROR,
                note=f"Invalid risk_level value: {raw_risk!r}",
            )
            return self._apply_error_policy(
                rule_verdict,
                f"LLM returned invalid risk_level: {raw_risk!r}",
            )

        reason = self._sanitize_reason(raw_reason)
        duration_ms = (self._clock.monotonic() - start) * _MILLISECONDS_PER_SECOND
        full_reason = f"LLM security eval: {reason}"

        return SecurityVerdict(
            verdict=_VERDICT_MAP[str(raw_verdict)],
            reason=full_reason,
            risk_level=_RISK_LEVEL_MAP[str(raw_risk)],
            confidence=EvaluationConfidence.HIGH,
            matched_rules=("security_verdict",),
            evaluated_at=datetime.now(UTC),
            evaluation_duration_ms=duration_ms,
            agent_visible_reason=self._compute_agent_reason(
                _VERDICT_MAP[str(raw_verdict)],
                _RISK_LEVEL_MAP[str(raw_risk)],
                full_reason,
            ),
        )

    def _sanitize_reason(self, raw_reason: object) -> str:
        """Sanitize and truncate the LLM-returned reason string."""
        reason_raw = (
            str(raw_reason).strip() if raw_reason else "LLM security evaluation"
        )
        # Strip control characters to prevent log injection.
        reason_clean = _CONTROL_CHAR_RE.sub(" ", reason_raw)
        return reason_clean[:_MAX_REASON_LENGTH]

    def _compute_agent_reason(
        self,
        verdict: SecurityVerdictType,
        risk_level: ApprovalRiskLevel,
        full_reason: str,
    ) -> str:
        """Compute the reason string visible to the evaluated agent."""
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

    # ------------------------------------------------------------------
    # Error policy
    # ------------------------------------------------------------------

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
