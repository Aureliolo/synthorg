"""Tests for budget error hierarchy."""

import pytest

from ai_company.budget.errors import (
    BudgetExhaustedError,
    DailyLimitExceededError,
    QuotaExhaustedError,
)

pytestmark = pytest.mark.timeout(30)


@pytest.mark.unit
class TestBudgetErrorHierarchy:
    """Verify inheritance relationships in the budget error hierarchy."""

    def test_budget_exhausted_is_exception(self) -> None:
        assert issubclass(BudgetExhaustedError, Exception)

    def test_daily_limit_is_budget_exhausted(self) -> None:
        assert issubclass(DailyLimitExceededError, BudgetExhaustedError)
        err = DailyLimitExceededError("daily limit hit")
        assert isinstance(err, BudgetExhaustedError)

    def test_quota_exhausted_is_budget_exhausted(self) -> None:
        assert issubclass(QuotaExhaustedError, BudgetExhaustedError)
        err = QuotaExhaustedError("quota hit")
        assert isinstance(err, BudgetExhaustedError)

    def test_budget_exhausted_not_engine_error(self) -> None:
        """Budget errors are independent of the engine error hierarchy."""
        from ai_company.engine.errors import EngineError

        assert not issubclass(BudgetExhaustedError, EngineError)
        assert not issubclass(DailyLimitExceededError, EngineError)
        assert not issubclass(QuotaExhaustedError, EngineError)

    def test_message_preserved(self) -> None:
        msg = "agent-1 budget exhausted"
        err = BudgetExhaustedError(msg)
        assert str(err) == msg

    def test_except_budget_exhausted_catches_subclasses(self) -> None:
        """Ensure except BudgetExhaustedError catches all subtypes."""
        for exc_cls in (DailyLimitExceededError, QuotaExhaustedError):
            msg = "subclass caught"
            with pytest.raises(BudgetExhaustedError):
                raise exc_cls(msg)
