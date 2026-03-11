"""Quota and subscription models for provider cost tracking.

Defines quota windows, subscription configurations, degradation
strategies, and quota check result models for providers that operate
under subscription plans, local deployments, or pay-as-you-go billing.
"""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from ai_company.core.types import NotBlankStr  # noqa: TC001
from ai_company.observability import get_logger
from ai_company.observability.events.config import CONFIG_VALIDATION_FAILED

logger = get_logger(__name__)


class QuotaWindow(StrEnum):
    """Time window for quota enforcement."""

    PER_MINUTE = "per_minute"
    PER_HOUR = "per_hour"
    PER_DAY = "per_day"
    PER_MONTH = "per_month"


class QuotaLimit(BaseModel):
    """A single quota limit for a time window.

    Attributes:
        window: Time window for this limit.
        max_requests: Maximum requests in the window (0 = unlimited).
        max_tokens: Maximum tokens in the window (0 = unlimited).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    window: QuotaWindow = Field(description="Time window for this limit")
    max_requests: int = Field(
        default=0,
        ge=0,
        description="Maximum requests in the window (0 = unlimited)",
    )
    max_tokens: int = Field(
        default=0,
        ge=0,
        description="Maximum tokens in the window (0 = unlimited)",
    )

    @model_validator(mode="after")
    def _at_least_one_limit(self) -> Self:
        """Ensure at least one of max_requests or max_tokens is set."""
        if self.max_requests == 0 and self.max_tokens == 0:
            msg = "At least one of max_requests or max_tokens must be > 0"
            logger.warning(
                CONFIG_VALIDATION_FAILED,
                model="QuotaLimit",
                field="max_requests/max_tokens",
                reason=msg,
            )
            raise ValueError(msg)
        return self


class ProviderCostModel(StrEnum):
    """How a provider charges for usage.

    Members:
        PER_TOKEN: Standard pay-as-you-go; cost computed from
            cost_per_1k_input/output.
        SUBSCRIPTION: Monthly flat fee; individual calls are pre-paid.
        LOCAL: Zero monetary cost; only hardware constraints.
    """

    PER_TOKEN = "per_token"  # noqa: S105 — billing concept, not a secret
    SUBSCRIPTION = "subscription"
    LOCAL = "local"


class SubscriptionConfig(BaseModel):
    """Subscription and quota configuration for a provider.

    Attributes:
        plan_name: Name of the subscription plan.
        cost_model: How the provider charges for usage.
        monthly_cost: Fixed monthly subscription fee in USD.
        quotas: Rate/token/request limits per time window.
        hardware_limits: Free-text hardware constraints for local models.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    plan_name: NotBlankStr = Field(
        default="pay_as_you_go",
        description="Subscription plan name",
    )
    cost_model: ProviderCostModel = Field(
        default=ProviderCostModel.PER_TOKEN,
        description="How the provider charges for usage",
    )
    monthly_cost: float = Field(
        default=0.0,
        ge=0.0,
        description="Fixed monthly subscription fee in USD",
    )
    quotas: tuple[QuotaLimit, ...] = Field(
        default=(),
        description="Rate/token/request limits per time window",
    )
    hardware_limits: str | None = Field(
        default=None,
        description="Free-text hardware constraints for local models",
    )

    @model_validator(mode="after")
    def _validate_quotas_unique_windows(self) -> Self:
        """Ensure quota windows are unique."""
        seen: set[QuotaWindow] = set()
        dupes: set[str] = set()
        for q in self.quotas:
            if q.window in seen:
                dupes.add(q.window.value)
            seen.add(q.window)
        if dupes:
            msg = f"Duplicate quota windows: {sorted(dupes)}"
            logger.warning(
                CONFIG_VALIDATION_FAILED,
                model="SubscriptionConfig",
                field="quotas",
                reason=msg,
            )
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_cost_model_constraints(self) -> Self:
        """Validate cost_model-specific constraints."""
        if self.cost_model == ProviderCostModel.LOCAL and self.monthly_cost > 0:
            msg = (
                f"LOCAL cost_model must have monthly_cost=0.0, got {self.monthly_cost}"
            )
            raise ValueError(msg)

        if self.cost_model == ProviderCostModel.SUBSCRIPTION and self.monthly_cost <= 0:
            logger.warning(
                CONFIG_VALIDATION_FAILED,
                model="SubscriptionConfig",
                field="monthly_cost",
                reason=(
                    "SUBSCRIPTION cost_model typically has monthly_cost > 0; "
                    f"got {self.monthly_cost}"
                ),
            )

        return self


class DegradationAction(StrEnum):
    """Action to take when a provider's quota is exhausted.

    Members:
        FALLBACK: Route to a fallback provider.
        QUEUE: Queue for later (not yet implemented).
        ALERT: Raise error and alert user.
    """

    FALLBACK = "fallback"
    QUEUE = "queue"
    ALERT = "alert"


class DegradationConfig(BaseModel):
    """Configuration for graceful degradation when quota is exhausted.

    Attributes:
        strategy: What to do when quota is exhausted.
        fallback_providers: Ordered fallback provider names.
        queue_max_wait_seconds: Max seconds to wait when queueing.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    strategy: DegradationAction = Field(
        default=DegradationAction.ALERT,
        description="Degradation strategy when quota exhausted",
    )
    fallback_providers: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Ordered fallback provider names",
    )
    queue_max_wait_seconds: int = Field(
        default=300,
        ge=0,
        le=3600,
        description="Max wait seconds when queueing",
    )

    @model_validator(mode="after")
    def _validate_fallback_providers(self) -> Self:
        """Warn if FALLBACK strategy has no fallback providers."""
        if self.strategy == DegradationAction.FALLBACK and not self.fallback_providers:
            logger.warning(
                CONFIG_VALIDATION_FAILED,
                model="DegradationConfig",
                field="fallback_providers",
                reason=(
                    "FALLBACK strategy specified but no fallback_providers configured"
                ),
            )
        return self


class QuotaSnapshot(BaseModel):
    """Point-in-time snapshot of quota usage for a provider window.

    Attributes:
        provider_name: Provider this snapshot belongs to.
        window: Time window for this snapshot.
        requests_used: Requests consumed in this window.
        requests_limit: Maximum requests allowed (0 = unlimited).
        tokens_used: Tokens consumed in this window.
        tokens_limit: Maximum tokens allowed (0 = unlimited).
        window_resets_at: When the current window resets.
        captured_at: When this snapshot was captured.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    provider_name: NotBlankStr = Field(description="Provider name")
    window: QuotaWindow = Field(description="Time window")
    requests_used: int = Field(default=0, ge=0, description="Requests used")
    requests_limit: int = Field(
        default=0,
        ge=0,
        description="Requests limit (0 = unlimited)",
    )
    tokens_used: int = Field(default=0, ge=0, description="Tokens used")
    tokens_limit: int = Field(
        default=0,
        ge=0,
        description="Tokens limit (0 = unlimited)",
    )
    window_resets_at: datetime | None = Field(
        default=None,
        description="When the current window resets",
    )
    captured_at: datetime = Field(description="When snapshot was captured")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def requests_remaining(self) -> int | None:
        """Remaining requests in this window.

        Returns ``None`` when the limit is not enforced (unlimited).
        Returns 0 when fully consumed.
        """
        if self.requests_limit == 0:
            return None
        return max(0, self.requests_limit - self.requests_used)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def tokens_remaining(self) -> int | None:
        """Remaining tokens in this window.

        Returns ``None`` when the limit is not enforced (unlimited).
        Returns 0 when fully consumed.
        """
        if self.tokens_limit == 0:
            return None
        return max(0, self.tokens_limit - self.tokens_used)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_exhausted(self) -> bool:
        """Whether any enforced limit in this window is exhausted."""
        if self.requests_limit > 0 and self.requests_used >= self.requests_limit:
            return True
        return self.tokens_limit > 0 and self.tokens_used >= self.tokens_limit


class QuotaCheckResult(BaseModel):
    """Result of a pre-flight quota check.

    Attributes:
        allowed: Whether the request is allowed.
        provider_name: Provider that was checked.
        reason: Human-readable reason (set when denied).
        exhausted_windows: Which windows are exhausted (if any).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    allowed: bool = Field(description="Whether the request is allowed")
    provider_name: NotBlankStr = Field(description="Provider checked")
    reason: str = Field(default="", description="Reason (set when denied)")
    exhausted_windows: tuple[QuotaWindow, ...] = Field(
        default=(),
        description="Exhausted windows",
    )

    @model_validator(mode="after")
    def _validate_denied_has_reason(self) -> Self:
        """Ensure denied results have a non-empty reason."""
        if not self.allowed and not self.reason:
            msg = "Denied QuotaCheckResult must have a non-empty reason"
            raise ValueError(msg)
        if self.allowed and self.exhausted_windows:
            msg = "Allowed QuotaCheckResult must not have exhausted_windows"
            raise ValueError(msg)
        return self


def window_start(
    window: QuotaWindow,
    *,
    now: datetime | None = None,
) -> datetime:
    """Compute the UTC-aware start of the current quota window.

    Args:
        window: Which time window to compute.
        now: Reference timestamp. Defaults to ``datetime.now(UTC)``.
            Must be timezone-aware; naive datetimes are rejected.

    Returns:
        UTC-aware datetime at the start of the current window.

    Raises:
        ValueError: If *now* is a naive (timezone-unaware) datetime.
    """
    if now is None:
        now = datetime.now(UTC)
    elif now.tzinfo is None:
        msg = "now must be timezone-aware, got naive datetime"
        logger.warning(
            CONFIG_VALIDATION_FAILED,
            model="window_start",
            field="now",
            reason=msg,
        )
        raise ValueError(msg)
    else:
        now = now.astimezone(UTC)

    if window == QuotaWindow.PER_MINUTE:
        return datetime(
            now.year,
            now.month,
            now.day,
            now.hour,
            now.minute,
            tzinfo=UTC,
        )
    if window == QuotaWindow.PER_HOUR:
        return datetime(
            now.year,
            now.month,
            now.day,
            now.hour,
            tzinfo=UTC,
        )
    if window == QuotaWindow.PER_DAY:
        return datetime(
            now.year,
            now.month,
            now.day,
            tzinfo=UTC,
        )
    # PER_MONTH — first day of the month
    return datetime(now.year, now.month, 1, tzinfo=UTC)


def effective_cost_per_1k(
    cost_per_1k_input: float,
    cost_per_1k_output: float,
    cost_model: ProviderCostModel,
) -> float:
    """Compute effective cost per 1k tokens based on cost model.

    Returns 0.0 for SUBSCRIPTION and LOCAL models (pre-paid / free).
    Returns ``cost_per_1k_input + cost_per_1k_output`` for PER_TOKEN.

    Args:
        cost_per_1k_input: Cost per 1k input tokens.
        cost_per_1k_output: Cost per 1k output tokens.
        cost_model: The provider's cost model.

    Returns:
        Effective cost per 1k tokens.
    """
    if cost_model in (ProviderCostModel.SUBSCRIPTION, ProviderCostModel.LOCAL):
        return 0.0
    return cost_per_1k_input + cost_per_1k_output
