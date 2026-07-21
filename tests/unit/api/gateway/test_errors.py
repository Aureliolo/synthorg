"""Unit tests for the gateway error hierarchy."""

import pytest

from synthorg.budget.errors import BudgetExhaustedError
from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode
from synthorg.llm.gateway_errors import (
    GatewayBudgetExhaustedError,
    GatewayModelUnboundError,
    GatewayTokenInvalidError,
)

pytestmark = pytest.mark.unit


def test_token_invalid_is_a_401_auth_error() -> None:
    error = GatewayTokenInvalidError()

    assert error.status_code == 401
    assert error.error_category is ErrorCategory.AUTH
    assert error.error_code is ErrorCode.GATEWAY_TOKEN_INVALID


def test_model_unbound_is_a_422_validation_error() -> None:
    error = GatewayModelUnboundError()

    assert error.status_code == 422
    assert error.error_category is ErrorCategory.VALIDATION
    assert error.error_code is ErrorCode.GATEWAY_MODEL_UNBOUND


def test_budget_exhausted_inherits_budget_handling() -> None:
    error = GatewayBudgetExhaustedError()

    assert isinstance(error, BudgetExhaustedError)
    assert error.status_code == 402
    assert error.error_category is ErrorCategory.BUDGET_EXHAUSTED
    assert error.error_code is ErrorCode.GATEWAY_BUDGET_EXHAUSTED
    assert error.retryable is False
