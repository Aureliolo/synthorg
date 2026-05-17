"""Configuration for Chief of Staff advanced capabilities.

Defines frozen Pydantic config for proposal outcome learning,
proactive alerts, and the chat interface. All capabilities are
opt-in with safe defaults (disabled).
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr
from synthorg.meta.models import RuleSeverity


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
