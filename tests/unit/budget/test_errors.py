"""Tests for budget error hierarchy."""

import pytest

from synthorg.budget.errors import (
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

    @pytest.mark.parametrize(
        ("exc_cls", "msg"),
        [
            (DailyLimitExceededError, "daily limit hit"),
            (QuotaExhaustedError, "quota hit"),
        ],
        ids=["daily_limit", "quota"],
    )
    def test_subclass_is_budget_exhausted(
        self,
        exc_cls: type[BudgetExhaustedError],
        msg: str,
    ) -> None:
        assert issubclass(exc_cls, BudgetExhaustedError)
        err = exc_cls(msg)
        assert isinstance(err, BudgetExhaustedError)

    def test_budget_exhausted_not_engine_error(self) -> None:
        """Budget errors are independent of the engine error hierarchy."""
        from synthorg.engine.errors import EngineError

        assert not issubclass(BudgetExhaustedError, EngineError)
        assert not issubclass(DailyLimitExceededError, EngineError)
        assert not issubclass(QuotaExhaustedError, EngineError)

    def test_message_preserved(self) -> None:
        msg = "agent-1 budget exhausted"
        err = BudgetExhaustedError(msg)
        assert str(err) == msg

    @pytest.mark.parametrize(
        "exc_cls",
        [DailyLimitExceededError, QuotaExhaustedError],
        ids=["daily_limit", "quota"],
    )
    def test_except_budget_exhausted_catches_subclasses(
        self,
        exc_cls: type[BudgetExhaustedError],
    ) -> None:
        """Ensure except BudgetExhaustedError catches all subtypes."""
        msg = "subclass caught"
        with pytest.raises(BudgetExhaustedError):
            raise exc_cls(msg)
