"""Unit tests for gateway controller error mapping."""

import pytest

from synthorg.api.gateway.controller import _error_response
from synthorg.core.error_taxonomy import ErrorCode
from synthorg.llm.gateway_errors import (
    GatewayBudgetExhaustedError,
    GatewayModelUnboundError,
    GatewayTokenInvalidError,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (GatewayTokenInvalidError(), 401, ErrorCode.GATEWAY_TOKEN_INVALID),
        (GatewayModelUnboundError(), 422, ErrorCode.GATEWAY_MODEL_UNBOUND),
        (GatewayBudgetExhaustedError(), 402, ErrorCode.GATEWAY_BUDGET_EXHAUSTED),
    ],
)
def test_error_response_maps_status_and_openai_error_shape(
    error: GatewayTokenInvalidError | GatewayModelUnboundError,
    status: int,
    code: ErrorCode,
) -> None:
    response = _error_response(error)

    assert response.status_code == status
    body = response.content
    assert isinstance(body, dict)
    assert body["error"]["code"] == int(code)
    assert "message" in body["error"]
