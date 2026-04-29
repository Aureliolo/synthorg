"""Tests for budget error hierarchy."""

import pytest

from synthorg.budget.errors import (
    BudgetExhaustedError,
    DailyLimitExceededError,
    MixedCurrencyAggregationError,
    QuotaExhaustedError,
)
from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode


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


@pytest.mark.unit
class TestMixedCurrencyAggregationError:
    """Validation + taxonomy contract for ``MixedCurrencyAggregationError``."""

    def test_not_a_budget_exhausted_subclass(self) -> None:
        """Data-integrity error must not be caught by BudgetExhaustedError."""
        assert not issubclass(
            MixedCurrencyAggregationError,
            BudgetExhaustedError,
        )

    def test_classvar_http_metadata(self) -> None:
        """ClassVars match the RFC 9457 conflict taxonomy."""
        assert MixedCurrencyAggregationError.status_code == 409
        assert (
            MixedCurrencyAggregationError.error_code
            == ErrorCode.MIXED_CURRENCY_AGGREGATION
        )
        assert MixedCurrencyAggregationError.error_category == ErrorCategory.CONFLICT
        assert MixedCurrencyAggregationError.retryable is False

    def test_requires_at_least_two_distinct_currencies(self) -> None:
        """Constructor rejects inputs that do not actually mix currencies."""
        with pytest.raises(ValueError, match="at least 2 distinct currencies"):
            MixedCurrencyAggregationError(
                "only one code",
                currencies=frozenset({"EUR"}),
            )
        with pytest.raises(ValueError, match="at least 2 distinct currencies"):
            MixedCurrencyAggregationError(
                "empty set",
                currencies=frozenset(),
            )

    def test_exposes_currency_set_and_context(self) -> None:
        err = MixedCurrencyAggregationError(
            currencies=frozenset({"EUR", "JPY"}),
            agent_id="agent-1",
            task_id="task-9",
            project_id="proj-42",
        )
        assert err.currencies == frozenset({"EUR", "JPY"})
        assert err.agent_id == "agent-1"
        assert err.task_id == "task-9"
        assert err.project_id == "proj-42"
        assert str(err) == MixedCurrencyAggregationError.default_message

    def test_custom_message_preserved(self) -> None:
        err = MixedCurrencyAggregationError(
            "custom detail",
            currencies=frozenset({"EUR", "USD"}),
        )
        assert str(err) == "custom detail"
