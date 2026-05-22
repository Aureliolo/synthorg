"""Configuration for the deep CEO interview to project charter flow.

Frozen Pydantic config, opt-in with a safe disabled default. The
interview strategy is pluggable behind a discriminator; the ``llm``
default ships an LLM-backed interviewer.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from synthorg.budget.currency import DEFAULT_CURRENCY, CurrencyCode
from synthorg.core.types import NotBlankStr

# Low sampling temperature keeps the interview turn emitting deterministic
# JSON structure (a question or a charter draft) rather than discursive
# prose; the 0.0/2.0 bounds mirror the provider-agnostic sampler range.
_INTERVIEW_TEMPERATURE_DEFAULT: float = 0.3
_INTERVIEW_TEMPERATURE_MIN: float = 0.0
_INTERVIEW_TEMPERATURE_MAX: float = 2.0
# A charter draft (brief + goals + constraints + criteria + scope +
# envelope) is a larger JSON payload than a single work proposal, so the
# token budget is higher than the propose path; 100 is the floor below
# which even a single elicitation question would not fit.
_INTERVIEW_MAX_TOKENS_DEFAULT: int = 3000
_INTERVIEW_MAX_TOKENS_MIN: int = 100
# Twelve turns is a generous elicitation budget before the interview
# force-closes without converging; 1..40 is the tunable envelope.
_INTERVIEW_MAX_TURNS_DEFAULT: int = 12
_INTERVIEW_MAX_TURNS_MIN: int = 1
_INTERVIEW_MAX_TURNS_MAX: int = 40


class CharterConfig(BaseModel):
    """Configuration for the charter-interview subsystem (opt-in).

    Attributes:
        interview_enabled: Enable the charter-interview interface
            (``/meta/charters``). Disabled by default.
        interview_strategy: Pluggable interview strategy discriminator.
        interview_model: LLM model identifier for interview turns.
        interview_temperature: Sampling temperature for interview turns.
        interview_max_tokens: Token budget for one interview turn.
        interview_max_turns: Maximum elicitation turns before the
            interview force-closes without a charter (prevents an
            unbounded interview loop).
        default_currency: Currency assumed for the budget envelope when
            the interview does not elicit one explicitly; must match the
            live ``budget.currency`` setting for charter approval to
            create the backing forecast.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    interview_enabled: bool = False
    interview_strategy: Literal["llm"] = "llm"
    interview_model: NotBlankStr = Field(
        default=NotBlankStr("example-small-001"),
        description="Model for charter-interview LLM calls",
    )
    interview_temperature: float = Field(
        default=_INTERVIEW_TEMPERATURE_DEFAULT,
        ge=_INTERVIEW_TEMPERATURE_MIN,
        le=_INTERVIEW_TEMPERATURE_MAX,
    )
    interview_max_tokens: int = Field(
        default=_INTERVIEW_MAX_TOKENS_DEFAULT,
        ge=_INTERVIEW_MAX_TOKENS_MIN,
    )
    interview_max_turns: int = Field(
        default=_INTERVIEW_MAX_TURNS_DEFAULT,
        ge=_INTERVIEW_MAX_TURNS_MIN,
        le=_INTERVIEW_MAX_TURNS_MAX,
    )
    default_currency: CurrencyCode = Field(default=DEFAULT_CURRENCY)


__all__ = ["CharterConfig"]
