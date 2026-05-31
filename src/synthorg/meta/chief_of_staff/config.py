"""Configuration for Chief of Staff advanced capabilities.

Defines frozen Pydantic config for proposal outcome learning,
proactive alerts, and the chat interface. All capabilities are
opt-in with safe defaults (disabled).
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.enums import ApprovalRiskLevel
from synthorg.core.types import NotBlankStr
from synthorg.meta.models import RuleSeverity

# Sampling temperature stays low (0.3) so the clarify/propose path
# emits deterministic JSON structure rather than discursive text; the
# 0.0/2.0 bounds mirror the provider-agnostic sampler range every
# runtime integration passes through.
_PROPOSE_TEMPERATURE_DEFAULT: float = 0.3
_PROPOSE_TEMPERATURE_MIN: float = 0.0
_PROPOSE_TEMPERATURE_MAX: float = 2.0
# 2000 tokens fits a JSON payload of up to ~5 clarify+propose items
# (the per-turn fan-out cap below) without truncation on typical
# large-capability models; 100 is the floor below which even a minimal
# clarifying question would not fit.
_PROPOSE_MAX_TOKENS_DEFAULT: int = 2000
_PROPOSE_MAX_TOKENS_MIN: int = 100
# Five proposals per turn bounds the approval-queue fan-out a single
# conversation turn can create; the 1..20 envelope is the same range
# the model_validator on ProposeDecision enforces for the model's own
# JSON output.
_PROPOSE_MAX_PROPOSALS_DEFAULT: int = 5
_PROPOSE_MAX_PROPOSALS_MIN: int = 1
_PROPOSE_MAX_PROPOSALS_MAX: int = 20
# Five clarifying turns is the cap before the conversation force-closes
# with _CAP_MESSAGE; 1..20 is the same envelope as the per-turn cap so
# operators tuning one routinely tune the other in tandem.
_PROPOSE_MAX_CLARIFICATION_DEFAULT: int = 5
_PROPOSE_MAX_CLARIFICATION_MIN: int = 1
_PROPOSE_MAX_CLARIFICATION_MAX: int = 20
# Concern routing runs a deterministic classification pass (temperature
# 0.0) so the topic/role/confidence JSON is stable; the 0.0/2.0 bounds
# mirror the same provider-agnostic sampler range as the propose path.
_ROUTING_TEMPERATURE_DEFAULT: float = 0.0
# A classification reply is a single small JSON object (topic + role +
# confidence); 200 tokens fits it comfortably and 50 is the floor below
# which even that minimal object risks truncation.
_ROUTING_MAX_TOKENS_DEFAULT: int = 200
_ROUTING_MAX_TOKENS_MIN: int = 50
# Below 0.6 classifier confidence the request falls back to the generic
# Chief of Staff rather than routing to a possibly-wrong role; the
# 0.0/1.0 envelope is the natural probability range.
_ROUTING_CONFIDENCE_FLOOR_DEFAULT: float = 0.6


class ChiefOfStaffConfig(BaseModel):
    """Configuration for Chief of Staff advanced capabilities.

    Three capability groups, all opt-in:

    - **Learning**: Track proposal approval/rejection patterns,
      adjust future proposal confidence scores.
    - **Alerts**: Detect org-level signal inflections between
      scheduled cycles, emit proactive alerts.
    - **Chat**: LLM-powered natural language explanations of
      proposals, alerts, and signal interactions.

    Attributes:
        learning_enabled: Enable proposal outcome learning.
        adjuster_strategy: Confidence adjustment algorithm.
        ema_alpha: Blend factor for EMA adjuster (0 = full
            history, 1 = full base confidence).
        min_outcomes: Minimum decision count before adjusting.
        alerts_enabled: Enable proactive org-level alerts.
        inflection_check_interval_minutes: Minutes between
            inflection detection checks.
        inflection_severity_threshold: Minimum severity to
            emit an alert.
        chat_enabled: Enable the chat explanation interface.
        chat_model: LLM model identifier for chat responses.
        chat_temperature: Sampling temperature for chat.
        chat_max_tokens: Token budget for chat responses.
        propose_enabled: Enable the clarify-and-propose interface
            (``/meta/chat/propose``). Independent of ``chat_enabled``.
        propose_model: LLM model identifier for clarify/propose turns.
        propose_temperature: Sampling temperature for propose turns.
        propose_max_tokens: Token budget for a propose turn.
        propose_max_proposals_per_turn: Upper bound on work items a
            single turn may emit (bounds approval-queue fan-out).
        propose_max_clarification_turns: Maximum clarifying questions
            before the model must either propose or yield a terminal
            turn (prevents an unbounded clarify loop).
        propose_default_risk_level: Risk level stamped on the approval
            item created for each proposed work item.
        routing_enabled: Enable concern-routing in front of the
            clarify-and-propose loop. When off, every turn is answered by
            the generic Chief of Staff persona (v1 behaviour).
        routing_strategy: Which ``RoleRouter`` strategy to build:
            ``"llm"`` (a concern classifier) or ``"keyword"`` (a static
            keyword-to-role map).
        routing_model: LLM model identifier for the concern classifier.
        routing_temperature: Sampling temperature for the classifier.
        routing_max_tokens: Token budget for one classification reply.
        routing_confidence_floor: Minimum classifier confidence (0-1) to
            route to a role; below it the turn falls back to the generic
            Chief of Staff.
        routing_default_role: Role to try when the classifier is
            confident but names a role with no active agent; falls back
            to the generic persona when that role is also absent.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    # ── Learning ──────────────────────────────────────────────────

    learning_enabled: bool = False
    adjuster_strategy: Literal["ema", "bayesian"] = "ema"
    ema_alpha: float = Field(default=0.5, ge=0.0, le=1.0)
    min_outcomes: int = Field(default=3, ge=1)

    # ── Proactive alerts ──────────────────────────────────────────

    alerts_enabled: bool = False
    inflection_check_interval_minutes: int = Field(default=15, ge=5)
    inflection_severity_threshold: RuleSeverity = RuleSeverity.WARNING

    # ── Chat ──────────────────────────────────────────────────────

    chat_enabled: bool = False
    chat_model: NotBlankStr = Field(
        default=NotBlankStr("example-small-001"),
        description="Model for chat explanation LLM calls",
    )
    chat_temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    chat_max_tokens: int = Field(default=2000, ge=100)

    # ── Clarify + propose ─────────────────────────────────────────

    propose_enabled: bool = False
    propose_model: NotBlankStr = Field(
        default=NotBlankStr("example-small-001"),
        description="Model for clarify-and-propose LLM calls",
    )
    propose_temperature: float = Field(
        default=_PROPOSE_TEMPERATURE_DEFAULT,
        ge=_PROPOSE_TEMPERATURE_MIN,
        le=_PROPOSE_TEMPERATURE_MAX,
    )
    propose_max_tokens: int = Field(
        default=_PROPOSE_MAX_TOKENS_DEFAULT,
        ge=_PROPOSE_MAX_TOKENS_MIN,
    )
    propose_max_proposals_per_turn: int = Field(
        default=_PROPOSE_MAX_PROPOSALS_DEFAULT,
        ge=_PROPOSE_MAX_PROPOSALS_MIN,
        le=_PROPOSE_MAX_PROPOSALS_MAX,
    )
    propose_max_clarification_turns: int = Field(
        default=_PROPOSE_MAX_CLARIFICATION_DEFAULT,
        ge=_PROPOSE_MAX_CLARIFICATION_MIN,
        le=_PROPOSE_MAX_CLARIFICATION_MAX,
    )
    propose_default_risk_level: ApprovalRiskLevel = ApprovalRiskLevel.MEDIUM

    # ── Concern routing (#1969) ───────────────────────────────────

    routing_enabled: bool = False
    routing_strategy: Literal["llm", "keyword"] = "llm"
    routing_model: NotBlankStr = Field(
        default=NotBlankStr("example-small-001"),
        description="Model for the concern-routing classifier LLM calls",
    )
    routing_temperature: float = Field(
        default=_ROUTING_TEMPERATURE_DEFAULT,
        ge=_PROPOSE_TEMPERATURE_MIN,
        le=_PROPOSE_TEMPERATURE_MAX,
    )
    routing_max_tokens: int = Field(
        default=_ROUTING_MAX_TOKENS_DEFAULT,
        ge=_ROUTING_MAX_TOKENS_MIN,
    )
    routing_confidence_floor: float = Field(
        default=_ROUTING_CONFIDENCE_FLOOR_DEFAULT,
        ge=0.0,
        le=1.0,
    )
    routing_default_role: NotBlankStr = Field(
        default=NotBlankStr("CEO"),
        description="Role to try when a confident classification names "
        "a role with no active agent",
    )
