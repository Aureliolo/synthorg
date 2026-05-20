"""Tests for the cost-dial domain-error hierarchy (#1982)."""

from uuid import uuid4

import pytest

from synthorg.budget.errors import (
    BudgetExhaustedError,
    CostForecastApprovalRequiredError,
    CostForecastRejectedError,
    RunHardCeilingExceededError,
    RunHardCeilingTooLowError,
)
from synthorg.core.domain_errors import DomainError
from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode


def test_run_hard_ceiling_exceeded_inherits_budget_exhausted() -> None:
    """Engine's existing `except BudgetExhaustedError` must absorb the ceiling.

    Without subclass inheritance the engine would need a new explicit
    handler and the safety net documented in :class:`AgentEngine`'s
    catch block would silently bypass ceiling halts.
    """
    err = RunHardCeilingExceededError(
        "ceiling crossed",
        ceiling_amount=1.50,
        accumulated_cost=1.50,
        currency="USD",
    )
    assert isinstance(err, BudgetExhaustedError)
    assert isinstance(err, DomainError)


def test_run_hard_ceiling_exceeded_metadata() -> None:
    """The error carries the values the resume UI needs to render."""
    forecast_id = uuid4()
    err = RunHardCeilingExceededError(
        "crossed at task tx",
        ceiling_amount=2.00,
        accumulated_cost=2.15,
        currency="USD",
        task_id="task-tx",
        forecast_id=forecast_id,
    )
    assert err.ceiling_amount == 2.00
    assert err.accumulated_cost == 2.15
    assert err.currency == "USD"
    assert err.task_id == "task-tx"
    assert err.forecast_id == forecast_id
    assert err.status_code == 402
    assert err.error_code is ErrorCode.RUN_HARD_CEILING_EXCEEDED
    assert err.error_category is ErrorCategory.BUDGET_EXHAUSTED


def test_cost_forecast_approval_required_is_sibling_not_subclass() -> None:
    """The forecast gate is NOT absorbed by the engine's ceiling catch.

    Approval gating runs in the work-entry adapter; making this a
    sibling of :class:`BudgetExhaustedError` rather than a subclass
    keeps the two surfaces independent so a future engine handler
    cannot accidentally swallow operator-approval signals.
    """
    err = CostForecastApprovalRequiredError(
        "awaiting approval",
        forecast_id=uuid4(),
        brief_hash="0" * 64,
        estimated_cost=0.75,
        currency="USD",
    )
    assert isinstance(err, DomainError)
    assert not isinstance(err, BudgetExhaustedError)
    assert err.status_code == 402
    assert err.error_code is ErrorCode.COST_FORECAST_APPROVAL_REQUIRED
    assert err.error_category is ErrorCategory.BUDGET_EXHAUSTED


def test_cost_forecast_rejected_terminal() -> None:
    """The rejected error is terminal: not a BudgetExhaustedError subclass."""
    err = CostForecastRejectedError(
        "operator rejected",
        forecast_id=uuid4(),
        brief_hash="a" * 64,
    )
    assert isinstance(err, DomainError)
    assert not isinstance(err, BudgetExhaustedError)
    assert err.status_code == 402
    assert err.error_code is ErrorCode.COST_FORECAST_REJECTED
    assert err.retryable is False


def test_run_hard_ceiling_too_low_is_validation_error() -> None:
    """Raising a ceiling at or below accumulated cost is a 422, not 402.

    The endpoint must reject the request rather than producing an
    immediate re-halt on resume; this error signals "fix the input"
    not "budget exhausted".
    """
    err = RunHardCeilingTooLowError(
        "1.10 is not above 1.20",
        requested_ceiling=1.10,
        accumulated_cost=1.20,
        currency="USD",
    )
    assert isinstance(err, DomainError)
    assert not isinstance(err, BudgetExhaustedError)
    assert err.status_code == 422
    assert err.error_code is ErrorCode.RUN_HARD_CEILING_TOO_LOW
    assert err.error_category is ErrorCategory.VALIDATION


def test_run_hard_ceiling_too_low_carries_values() -> None:
    """The endpoint surfaces the values needed for the inline message."""
    err = RunHardCeilingTooLowError(
        "below accumulated",
        requested_ceiling=0.5,
        accumulated_cost=1.0,
        currency="GBP",
    )
    assert err.requested_ceiling == 0.5
    assert err.accumulated_cost == 1.0
    assert err.currency == "GBP"


@pytest.mark.parametrize(
    "error_cls",
    [
        RunHardCeilingExceededError,
        CostForecastApprovalRequiredError,
        CostForecastRejectedError,
        RunHardCeilingTooLowError,
    ],
)
def test_cost_dial_errors_are_not_retryable(error_cls: type[DomainError]) -> None:
    """Cost-dial errors are not retryable; the operator must adjust state."""
    assert error_cls.retryable is False
