"""Budget-layer error hierarchy.

Defines budget-specific exceptions in a leaf module with minimal
intra-project imports, preventing circular dependency chains when
these exceptions are needed by both the budget enforcer and the
engine layer.
"""

from typing import TYPE_CHECKING, ClassVar

from synthorg.core.domain_errors import DomainError
from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode
from synthorg.core.types import NotBlankStr  # noqa: TC001

if TYPE_CHECKING:
    from synthorg.budget.quota import DegradationAction


class BudgetExhaustedError(DomainError):
    """Budget exhaustion signal.

    Used in two contexts:

    1. Raised directly by :meth:`BudgetEnforcer.check_can_execute`
       when pre-flight budget checks fail (e.g., monthly hard stop,
       daily limit, or provider quota exceeded).
    2. Caught by the engine layer (``AgentEngine.run``) and used to
       build an ``AgentRunResult`` with
       ``TerminationReason.BUDGET_EXHAUSTED``.

    Class Attributes:
        status_code: HTTP 402 Payment Required.
        error_code: ``BUDGET_EXHAUSTED``.
        error_category: ``BUDGET_EXHAUSTED``.
        retryable: ``False`` -- caller must adjust budget or wait for
            period reset.
        default_message: Generic message safe for user-facing responses.
    """

    status_code: ClassVar[int] = 402
    error_code: ClassVar[ErrorCode] = ErrorCode.BUDGET_EXHAUSTED
    error_category: ClassVar[ErrorCategory] = ErrorCategory.BUDGET_EXHAUSTED
    retryable: ClassVar[bool] = False
    default_message: ClassVar[str] = "Budget exhausted"


class DailyLimitExceededError(BudgetExhaustedError):
    """Per-agent daily spending limit exceeded."""

    error_code: ClassVar[ErrorCode] = ErrorCode.DAILY_LIMIT_EXCEEDED
    default_message: ClassVar[str] = "Daily spending limit exceeded"


class RiskBudgetExhaustedError(BudgetExhaustedError):
    """Raised when cumulative risk budget is exhausted.

    Subclass of ``BudgetExhaustedError`` so existing engine-level
    catch handlers cover it transparently.

    Attributes:
        agent_id: The agent that exceeded the limit, or ``None``.
        task_id: The task during which the limit was exceeded, or ``None``.
        risk_units_used: Cumulative risk units consumed.
        risk_limit: The limit that was exceeded.
    """

    error_code: ClassVar[ErrorCode] = ErrorCode.RISK_BUDGET_EXHAUSTED
    default_message: ClassVar[str] = "Risk budget exhausted"

    def __init__(
        self,
        msg: str,
        *,
        agent_id: NotBlankStr | None = None,
        task_id: NotBlankStr | None = None,
        risk_units_used: float = 0.0,
        risk_limit: float = 0.0,
    ) -> None:
        super().__init__(msg)
        self.agent_id = agent_id
        self.task_id = task_id
        self.risk_units_used = risk_units_used
        self.risk_limit = risk_limit


class ProjectBudgetExhaustedError(BudgetExhaustedError):
    """Project-level budget limit exceeded.

    Attributes:
        project_id: The project whose budget was exceeded.
        project_budget: The project's total budget.
        project_spent: Amount already spent on the project.
    """

    error_code: ClassVar[ErrorCode] = ErrorCode.PROJECT_BUDGET_EXHAUSTED
    default_message: ClassVar[str] = "Project budget exhausted"

    def __init__(
        self,
        msg: str,
        *,
        project_id: NotBlankStr,
        project_budget: float = 0.0,
        project_spent: float = 0.0,
    ) -> None:
        super().__init__(msg)
        self.project_id = project_id
        self.project_budget = project_budget
        self.project_spent = project_spent


class MixedCurrencyAggregationError(DomainError):
    """Raised when cost values in different currencies would be aggregated.

    Cost summation, averaging, and budget checks only produce meaningful
    results when every contributing row carries the same currency.  This
    error signals that the caller handed an aggregator a mix of
    currencies; the fix is to partition records by currency first (or
    apply an FX conversion -- out of scope for the initial release).

    Intentionally a sibling of :class:`BudgetExhaustedError`, not a
    subclass: this is a data-integrity / caller-contract violation, not
    a budget-exhaustion signal, so the engine layer's
    ``BudgetExhaustedError`` catch block must not absorb it.

    Class Attributes:
        status_code: HTTP 409 Conflict.
        error_code: ``MIXED_CURRENCY_AGGREGATION``.
        error_category: ``CONFLICT``.
        retryable: ``False`` -- retrying without partitioning the input
            produces the same error.
        default_message: Generic message safe for user-facing responses.

    Instance Attributes:
        currencies: The set of distinct currency codes observed in the
            input.  Exposed so structured logs and error envelopes can
            surface the conflicting codes without inspecting the
            offending records directly.
        agent_id: Optional agent identifier the aggregation targeted.
        task_id: Optional task identifier the aggregation targeted.
        project_id: Optional project identifier the aggregation targeted.
    """

    status_code: ClassVar[int] = 409
    error_code: ClassVar[ErrorCode] = ErrorCode.MIXED_CURRENCY_AGGREGATION
    error_category: ClassVar[ErrorCategory] = ErrorCategory.CONFLICT
    retryable: ClassVar[bool] = False
    default_message: ClassVar[str] = (
        "Cannot aggregate cost values across different currencies"
    )

    def __init__(
        self,
        msg: str | None = None,
        *,
        currencies: frozenset[str],
        agent_id: NotBlankStr | None = None,
        task_id: NotBlankStr | None = None,
        project_id: NotBlankStr | None = None,
    ) -> None:
        if len(currencies) < 2:  # noqa: PLR2004
            detail = (
                f"MixedCurrencyAggregationError requires at least 2 distinct "
                f"currencies, got {sorted(currencies)!r}"
            )
            raise ValueError(detail)
        super().__init__(msg or self.default_message)
        self.currencies = currencies
        self.agent_id = agent_id
        self.task_id = task_id
        self.project_id = project_id


class QuotaExhaustedError(BudgetExhaustedError):
    """Raised when provider quota is exhausted and unresolvable.

    Covers all terminal degradation outcomes: ALERT strategy
    (intentional immediate raise), failed FALLBACK (no providers
    available or all exhausted), and failed QUEUE (wait exceeded
    or still exhausted after waiting).

    Attributes:
        provider_name: The provider whose quota was exhausted,
            or ``None`` when not available.
        degradation_action: The degradation strategy that was
            attempted, or ``None`` when not available.
    """

    error_code: ClassVar[ErrorCode] = ErrorCode.QUOTA_EXHAUSTED
    default_message: ClassVar[str] = "Provider quota exhausted"

    def __init__(
        self,
        msg: str,
        *,
        provider_name: NotBlankStr | None = None,
        degradation_action: DegradationAction | None = None,
    ) -> None:
        super().__init__(msg)
        self.provider_name = provider_name
        self.degradation_action = degradation_action
