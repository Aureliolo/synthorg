"""Sub-component configuration models for ``SecurityConfig``.

Each model configures one security subsystem (LLM fallback, rule engine,
safety classifier, uncertainty check, red-team gate, vision verifier, MCP
self-consumer bridge). Assembled by :class:`SecurityConfig` in the package
``__init__``.
"""

from typing import Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.approval.enums import ApprovalRiskLevel
from synthorg.core.types import ModelTier, NotBlankStr
from synthorg.security.config._enums import (
    ArgumentTruncationStrategy,
    LlmFallbackErrorPolicy,
    McpSelfConsumerMode,
    VerdictReasonVisibility,
    VisionVerifierKind,
)
from synthorg.security.models import SecurityVerdictType
from synthorg.settings.definitions.security import (
    RED_TEAM_TIMEOUT_DEFAULT_SECONDS,
    RED_TEAM_TIMEOUT_MAX_SECONDS,
)

VISION_TIMEOUT_DEFAULT_SECONDS: Final[float] = 60.0
VISION_TIMEOUT_MAX_SECONDS: Final[float] = 600.0
VISION_DEFAULT_COLOUR_TOLERANCE: Final[float] = 0.15


class LlmFallbackConfig(BaseModel):
    """Configuration for LLM-based security evaluation fallback.

    When enabled, actions that the rule engine cannot classify
    (no rule matched, low confidence) are routed to an LLM from
    a different provider family for cross-validation.

    Attributes:
        enabled: Whether LLM fallback is active.
        model: Explicit model ID for security evaluation.  When
            ``None``, the evaluator picks the first model from
            the selected provider (cross-family preferred,
            same-family fallback).
        timeout_seconds: Maximum time for the LLM call.
        max_input_tokens: Token budget cap for security eval prompts.
        max_output_tokens: Response token budget for the LLM verdict.
        on_error: Policy when the LLM call fails.
        reason_visibility: How much of the LLM reason is visible
            to the evaluated agent.
        argument_truncation: Strategy for truncating large tool
            arguments in the LLM prompt.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    enabled: bool = False
    model: NotBlankStr | None = None
    timeout_seconds: float = Field(default=10.0, gt=0.0)
    max_input_tokens: int = Field(default=2000, gt=0)
    max_output_tokens: int = Field(default=256, gt=0, le=4096)
    on_error: LlmFallbackErrorPolicy = LlmFallbackErrorPolicy.ESCALATE
    reason_visibility: VerdictReasonVisibility = VerdictReasonVisibility.GENERIC
    argument_truncation: ArgumentTruncationStrategy = (
        ArgumentTruncationStrategy.PER_VALUE
    )
    # Sampling parameters for the security-evaluation completion call.
    # Pinned to deterministic defaults so verdicts stay reproducible
    # across runs; operators tuning this MUST re-run the golden eval
    # suite (``tests/prompts/golden/llm_security_evaluator``).
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    top_p: float = Field(default=1.0, ge=0.0, le=1.0)


class SecurityPolicyRule(BaseModel):
    """A single configurable security policy rule.

    Attributes:
        name: Rule name (used in matched_rules lists).
        description: Human-readable description.
        action_types: Action types this rule applies to (``category:action``).
        verdict: Verdict to return when rule matches.
        risk_level: Risk level to assign.
        enabled: Whether this rule is active.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    name: NotBlankStr
    description: str = ""
    action_types: tuple[str, ...] = ()
    verdict: SecurityVerdictType = SecurityVerdictType.DENY
    risk_level: ApprovalRiskLevel = ApprovalRiskLevel.MEDIUM
    enabled: bool = True

    @model_validator(mode="after")
    def _check_action_type_format(self) -> Self:
        """Validate that action_types entries use ``category:action`` format.

        Requires exactly one colon with non-empty, non-whitespace
        segments on each side.

        Returns:
            The validated policy.

        Raises:
            ValueError: If an entry lacks exactly one ':' or has an empty
                segment on either side.
        """
        for at in self.action_types:
            parts = at.split(":")
            if len(parts) != 2:  # noqa: PLR2004
                msg = (
                    f"action_type {at!r} in policy {self.name!r} must "
                    "contain exactly one ':' (category:action)"
                )
                raise ValueError(msg)
            category, action = parts
            if not category.strip() or not action.strip():
                msg = (
                    f"action_type {at!r} in policy {self.name!r} has "
                    "empty or whitespace-only category or action segment"
                )
                raise ValueError(msg)
        return self


class RuleEngineConfig(BaseModel):
    """Configuration for the synchronous rule engine.

    Attributes:
        credential_patterns_enabled: Detect credentials in arguments.
        data_leak_detection_enabled: Detect sensitive file paths / PII.
        destructive_op_detection_enabled: Detect destructive operations.
        path_traversal_detection_enabled: Detect path traversal attacks.
        max_argument_length: Maximum argument string length for scanning.
        custom_allow_bypasses_detectors: When ``True``, custom ALLOW
            policies are placed before detectors, allowing them to
            short-circuit security scanning.  When ``False`` (default),
            custom policies are placed after all detectors so security
            scanning always runs first.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    credential_patterns_enabled: bool = True
    data_leak_detection_enabled: bool = True
    destructive_op_detection_enabled: bool = True
    path_traversal_detection_enabled: bool = True
    max_argument_length: int = Field(default=100_000, gt=0)
    custom_allow_bypasses_detectors: bool = False


class SafetyClassifierConfig(BaseModel):
    """Configuration for the two-stage safety classifier at approval gates.

    When enabled, escalated actions are processed through two stages:
    Stage 1 strips PII, secrets, and internal IDs from the reviewer
    view.  Stage 2 runs an LLM classifier to categorize the action
    as safe, suspicious, or blocked.

    Attributes:
        enabled: Whether the safety classifier is active.
        model: Explicit model ID for classification.  When ``None``,
            the classifier picks the first model from the selected
            provider (cross-family preferred, same-family fallback).
        timeout_seconds: Maximum time for the LLM classification call.
        max_input_tokens: Token budget cap for classification prompts.
        max_output_tokens: Response token budget for the classifier verdict.
        auto_reject_blocked: Automatically reject actions classified
            as BLOCKED (returns DENY verdict without creating an
            approval item).
        max_consecutive_denials: Maximum consecutive denials before
            escalation to human review.  Used by ``DenialTracker``.
        max_total_denials: Maximum total denials across the agent's
            lifetime before escalation.  Used by ``DenialTracker``.
        safe_tool_categories: Action types that bypass the safety
            classifier entirely (permission tier: SAFE_TOOL).
            Matched against the ``action_type`` field of the
            security context.
        temperature: Sampling temperature for the classifier call.
            Pinned to ``0.0`` by default for a deterministic verdict.
        top_p: Nucleus-sampling cap for the classifier call. Pinned to
            ``1.0`` by default so determinism rests on ``temperature``.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    enabled: bool = False
    model: NotBlankStr | None = None
    timeout_seconds: float = Field(default=10.0, gt=0.0)
    max_input_tokens: int = Field(default=2000, gt=0)
    max_output_tokens: int = Field(default=256, gt=0, le=4096)
    auto_reject_blocked: bool = True
    max_consecutive_denials: int = Field(default=3, ge=1)
    max_total_denials: int = Field(default=20, ge=1)
    safe_tool_categories: tuple[str, ...] = ("code:read", "docs:write")
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    top_p: float = Field(default=1.0, ge=0.0, le=1.0)


class UncertaintyCheckConfig(BaseModel):
    """Configuration for cross-provider uncertainty checks.

    When enabled, the same prompt is sent to multiple providers and
    the responses are compared via keyword overlap and TF-IDF cosine
    similarity.  Low agreement produces a low confidence score,
    signaling potential hallucination.

    Attributes:
        enabled: Whether the uncertainty check is active.
        model_ref: Model alias to resolve via
            ``ModelResolver.resolve_all`` for multi-provider
            candidates.  When ``None``, the check is skipped.
        min_providers: Minimum number of providers required to run
            the check.  If fewer candidates are available the check
            is skipped and confidence defaults to 1.0.
        low_confidence_threshold: Confidence scores below this
            threshold are flagged as potentially hallucinated.
        timeout_seconds: Maximum time per provider call.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    enabled: bool = False
    model_ref: NotBlankStr | None = None
    min_providers: int = Field(default=2, ge=2)
    low_confidence_threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    timeout_seconds: float = Field(default=15.0, gt=0.0)


class RedTeamConfig(BaseModel):
    """Adversarial red-team gate configuration (opt-in subsystem).

    OFF by default. When ``enabled`` the gate fires as the last
    adversarial check before IN_REVIEW -> COMPLETED.

    Attributes:
        enabled: Master switch (``False`` builds no gate at boot).
        grounding_checker_kind: Grounding discriminator. ``"heuristic"``
            (default, LOW-capped stub) or ``"knowledge_substrate"`` (LLM
            entailment checker that escalates to HIGH; degrades when off).
        timeout_seconds: Per-evaluation cap on the inline AgentEngine.
        on_missing_deliverable: Posture when the gate is enabled but no
            deliverable is retrievable. ``"block"`` (default) fails
            closed (rework) so the gate never silently depends on flight
            recording being on; ``"skip"`` allows completion.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    enabled: bool = False
    grounding_checker_kind: Literal["heuristic", "knowledge_substrate"] = "heuristic"
    timeout_seconds: float = Field(
        default=RED_TEAM_TIMEOUT_DEFAULT_SECONDS,
        gt=0.0,
        le=RED_TEAM_TIMEOUT_MAX_SECONDS,
    )
    on_missing_deliverable: Literal["block", "skip"] = "block"


class VisionVerifyConfig(BaseModel):
    """Vision verifier gate configuration (opt-in subsystem).

    The vision gate is OFF by default. When ``enabled``, it fires as an
    adversarial check after ``ReviewPipeline`` PASS and the red-team
    gate, before the deliverable transitions IN_REVIEW -> COMPLETED.

    Attributes:
        enabled: Master switch. When ``False`` (default), no vision gate
            is constructed at boot and the ReviewGateService
            short-circuits as if the gate were absent.
        verifier_kind: Strategy discriminator. ``noop`` is inert,
            ``heuristic`` runs deterministic colour / rule checks,
            ``llm_vision`` calls a multimodal model.
        model_tier: Provider tier resolved for the ``llm_vision`` verifier.
        timeout_seconds: Per-evaluation cap on the verifier call.
        colour_tolerance: Default normalised RGB distance tolerance the
            heuristic applies when an expectation omits its own.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    enabled: bool = False
    verifier_kind: VisionVerifierKind = VisionVerifierKind.NOOP
    model_tier: ModelTier = "medium"
    timeout_seconds: float = Field(
        default=VISION_TIMEOUT_DEFAULT_SECONDS,
        gt=0.0,
        le=VISION_TIMEOUT_MAX_SECONDS,
    )
    colour_tolerance: float = Field(
        default=VISION_DEFAULT_COLOUR_TOLERANCE,
        ge=0.0,
        le=1.0,
    )


class McpSelfConsumerConfig(BaseModel):
    """Agent -> SynthOrg-MCP self-consumer bridge configuration.

    Attributes:
        mode: Bridge dispatch mode (default ``DISABLED``: no bridge).
        elevated_capabilities: Capability patterns granted to an agent
            whose earned trust level is ``ELEVATED`` (default ``("*",)``
            -- the full MCP surface, still gated behind ELEVATED trust
            and the per-handler admin guardrails).
        read_tool_allowlist: Explicit MCP tool names a sub-ELEVATED
            agent may call. Empty (default) means a low-trust agent
            gets no MCP access -- the safest posture; operators opt in
            by naming tools. An explicit allowlist sidesteps the
            ``*:read`` heuristic, whose pattern would miss
            ``list``/``get``/``status`` actions.
        denied_tools: MCP tool names always excluded regardless of
            trust level or allowlist (highest-priority denylist).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    mode: McpSelfConsumerMode = McpSelfConsumerMode.DISABLED
    elevated_capabilities: tuple[NotBlankStr, ...] = ("*",)
    read_tool_allowlist: tuple[NotBlankStr, ...] = ()
    denied_tools: tuple[NotBlankStr, ...] = ()
