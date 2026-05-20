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
# 0.0/2.0 bounds mirror the OpenAI sampler range every Claude/LiteLLM
# provider passes through.
_PROPOSE_TEMPERATURE_DEFAULT: float = 0.3
_PROPOSE_TEMPERATURE_MIN: float = 0.0
_PROPOSE_TEMPERATURE_MAX: float = 2.0
# 2000 tokens fits a JSON payload of up to ~5 clarify+propose items
# (the per-turn fan-out cap below) without truncation on Claude/GPT-4o
# class models; 100 is the floor below which even a minimal clarifying
# question would not fit.
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
