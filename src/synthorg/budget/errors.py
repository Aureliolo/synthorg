"""Budget-layer error hierarchy.

Defines budget-specific exceptions in a leaf module with minimal
intra-project imports, preventing circular dependency chains when
these exceptions are needed by both the budget enforcer and the
engine layer.
"""

from typing import TYPE_CHECKING, ClassVar

from synthorg.core.domain_errors import DomainError
from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode
from synthorg.core.types import NotBlankStr

if TYPE_CHECKING:
    from uuid import UUID

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
        department_id: Optional department identifier the aggregation
            targeted.  Distinct from ``project_id`` so per-department
            rollups (``DepartmentSpending``) can attach the offending
            department name without pretending it is a project.
    """

    status_code: ClassVar[int] = 409
    error_code: ClassVar[ErrorCode] = ErrorCode.MIXED_CURRENCY_AGGREGATION
    error_category: ClassVar[ErrorCategory] = ErrorCategory.CONFLICT
    retryable: ClassVar[bool] = False
    default_message: ClassVar[str] = (
        "Cannot aggregate cost values across different currencies"
    )

    def __init__(  # noqa: PLR0913 -- one optional id per scope dimension
        self,
        msg: str | None = None,
        *,
        currencies: frozenset[str],
        agent_id: NotBlankStr | None = None,
        task_id: NotBlankStr | None = None,
        project_id: NotBlankStr | None = None,
        department_id: NotBlankStr | None = None,
    ) -> None:
        if not currencies:
            detail = (
                "MixedCurrencyAggregationError requires at least one "
                "currency (or the missing-currency sentinel), got an "
                "empty set"
            )
            raise ValueError(detail)
        super().__init__(msg or self.default_message)
        self.currencies = currencies
        self.agent_id = agent_id
        self.task_id = task_id
        self.project_id = project_id
        self.department_id = department_id


class RunHardCeilingExceededError(BudgetExhaustedError):
    """Per-run hard real-money ceiling exceeded mid-execution.

    Raised by the in-loop ``BudgetChecker`` when accumulated cost meets
    or exceeds the per-task ``Task.hard_ceiling`` (or the global
    ``budget.run_hard_ceiling`` setting when the per-task value is
    absent).

    Subclass of :class:`BudgetExhaustedError` so the engine's existing
    ``except BudgetExhaustedError`` catch absorbs it transparently; the
    engine then routes the run to ``ApprovalGate.park_context`` with a
    payload carrying ``accumulated_cost`` and ``ceiling_amount`` so the
    operator can raise the ceiling and resume.

    Attributes:
        ceiling_amount: The hard ceiling that was crossed.
        accumulated_cost: Total cost accumulated at the moment of the
            crossing (inclusive of the turn that pushed past the line).
        currency: ISO 4217 code stamped on both values.
        task_id: Optional task identifier for downstream telemetry.
        forecast_id: Optional forecast row id linking back to the
            pre-flight estimate.
    """

    error_code: ClassVar[ErrorCode] = ErrorCode.RUN_HARD_CEILING_EXCEEDED
    default_message: ClassVar[str] = "Run hard ceiling exceeded"

    def __init__(  # noqa: PLR0913 -- carries the values the resume UI renders
        self,
        msg: str,
        *,
        ceiling_amount: float,
        accumulated_cost: float,
        currency: NotBlankStr,
        task_id: NotBlankStr | None = None,
        forecast_id: UUID | None = None,
    ) -> None:
        super().__init__(msg)
        self.ceiling_amount = ceiling_amount
        self.accumulated_cost = accumulated_cost
        self.currency = currency
        self.task_id = task_id
        self.forecast_id = forecast_id


class CostForecastApprovalRequiredError(DomainError):
    """Pre-flight cost forecast awaiting operator approval.

    Raised by work-entry adapters when ``budget.forecast_required`` is
    enabled and the inbound brief either has no ``forecast_id``, or the
    referenced forecast row is in a non-terminal-approved state
    (``pending`` / ``superseded``). The HTTP envelope carries the
    forecast payload so the operator can decide via the queue UI or
    the inline modal.

    Intentionally a sibling of :class:`BudgetExhaustedError` (not a
    subclass): the engine's ceiling-handler must NOT absorb forecast
    approvals; they are gated upstream at the work-entry seam.

    Attributes:
        forecast_id: The forecast row awaiting decision.
        brief_hash: Canonical hash of the brief that produced the row.
        estimated_cost: Mid-point cost estimate in ``currency``.
        currency: ISO 4217 code stamped on the estimate.
    """

    status_code: ClassVar[int] = 402
    error_code: ClassVar[ErrorCode] = ErrorCode.COST_FORECAST_APPROVAL_REQUIRED
    error_category: ClassVar[ErrorCategory] = ErrorCategory.BUDGET_EXHAUSTED
    retryable: ClassVar[bool] = False
    default_message: ClassVar[str] = "Cost forecast approval required"

    def __init__(
        self,
        msg: str,
        *,
        forecast_id: UUID,
        brief_hash: NotBlankStr,
        estimated_cost: float,
        currency: NotBlankStr,
    ) -> None:
        super().__init__(msg)
        self.forecast_id = forecast_id
        self.brief_hash = brief_hash
        self.estimated_cost = estimated_cost
        self.currency = currency


class CostForecastRejectedError(DomainError):
    """Pre-flight forecast was rejected by the operator.

    Raised by work-entry adapters when ``Task.forecast_id`` maps to a
    row with ``decision=rejected``. Terminal: the work item never
    dispatches; the caller must resubmit the brief.

    Attributes:
        forecast_id: The rejected forecast row.
        brief_hash: Canonical hash of the rejected brief.
    """

    status_code: ClassVar[int] = 402
    error_code: ClassVar[ErrorCode] = ErrorCode.COST_FORECAST_REJECTED
    error_category: ClassVar[ErrorCategory] = ErrorCategory.BUDGET_EXHAUSTED
    retryable: ClassVar[bool] = False
    default_message: ClassVar[str] = "Cost forecast rejected by operator"

    def __init__(
        self,
        msg: str,
        *,
        forecast_id: UUID,
        brief_hash: NotBlankStr,
    ) -> None:
        super().__init__(msg)
        self.forecast_id = forecast_id
        self.brief_hash = brief_hash


class RunHardCeilingTooLowError(DomainError):
    """Operator attempted to raise the ceiling below accumulated cost.

    Raised by the ``raise_ceiling`` API endpoint when the requested new
    ceiling is less than or equal to the cost already accumulated at
    the moment of parking. A new ceiling at or below the accumulated
    cost would re-halt the run immediately on resume, which is almost
    never the operator's intent; the endpoint rejects the request so
    the UI can prompt for a value above the accumulated total.

    Validation-category (HTTP 422) rather than budget-exhausted: this
    is a malformed instruction from the operator, not a budget signal.

    Attributes:
        requested_ceiling: The value the operator attempted to set.
        accumulated_cost: Cost already spent at park time.
        currency: ISO 4217 code stamped on both values.
    """

    status_code: ClassVar[int] = 422
    error_code: ClassVar[ErrorCode] = ErrorCode.RUN_HARD_CEILING_TOO_LOW
    error_category: ClassVar[ErrorCategory] = ErrorCategory.VALIDATION
    retryable: ClassVar[bool] = False
    default_message: ClassVar[str] = (
        "Requested ceiling is not greater than accumulated cost"
    )

    def __init__(
        self,
        msg: str,
        *,
        requested_ceiling: float,
        accumulated_cost: float,
        currency: NotBlankStr,
    ) -> None:
        super().__init__(msg)
        self.requested_ceiling = requested_ceiling
        self.accumulated_cost = accumulated_cost
        self.currency = currency


class UnknownBenchmarkProviderError(DomainError):
    """Raised when the configured ``budget.benchmark_provider`` is unknown.

    A wiring-time misconfiguration: the cost-dial selects the benchmark
    provider from a config discriminator, and an unrecognised value
    fails loudly rather than silently degrading to the stub (which would
    mask a typo'd operator setting).

    Class Attributes:
        status_code: HTTP 500 (server misconfiguration, not a client fault).
        error_code: ``INTERNAL_ERROR``.
        error_category: ``INTERNAL``.
        retryable: ``False`` -- the config must be corrected.
    """

    status_code: ClassVar[int] = 500
    error_code: ClassVar[ErrorCode] = ErrorCode.INTERNAL_ERROR
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    retryable: ClassVar[bool] = False
    default_message: ClassVar[str] = "Unknown benchmark-score provider configured"


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
