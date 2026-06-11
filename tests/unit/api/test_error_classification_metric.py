"""Tests for ``synthorg_api_error_classification_total`` emission.

``_build_response`` (in ``synthorg.api.exception_handlers``) fires
``record_api_error`` on every 4xx/5xx response it builds, so every
structured error out of the API surface increments the counter
partitioned by RFC 9457 category and HTTP status class.
"""

from typing import Any
from unittest.mock import MagicMock

import pytest
from litestar import Request
from litestar.testing import RequestFactory

from synthorg.api import exception_handlers
from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode

pytestmark = pytest.mark.unit


def _fake_request() -> Request[Any, Any, Any]:  # type: ignore[explicit-any]  # litestar RequestFactory yields Request[Any, Any, Any]
    """Build a real ``Request`` that accepts ``application/json``.

    ``_build_response`` calls ``_wants_problem_json(request)``, which
    inspects ``request.accept.best_match``; an ``application/json`` Accept
    header negotiates to the plain-JSON envelope branch (not problem+json),
    which is the path these metric-emission assertions exercise.
    """
    return RequestFactory().get(path="/", headers={"accept": "application/json"})


# Exhaustive matrix: every ErrorCategory value paired with a canonical
# status code in its natural HTTP range.  Using ErrorCode.PROVIDER_ERROR
# as the error_code for every row keeps the focus on the category /
# status_class wiring; the wire ``error_code`` is orthogonal to the
# metric labels.
@pytest.mark.parametrize(
    ("category", "status_code"),
    [
        (ErrorCategory.AUTH, 401),
        (ErrorCategory.VALIDATION, 422),
        (ErrorCategory.NOT_FOUND, 404),
        (ErrorCategory.CONFLICT, 409),
        (ErrorCategory.RATE_LIMIT, 429),
        (ErrorCategory.BUDGET_EXHAUSTED, 402),
        (ErrorCategory.PROVIDER_ERROR, 502),
        (ErrorCategory.INTERNAL, 500),
    ],
)
def test_build_response_emits_api_error_metric_per_category(
    monkeypatch: pytest.MonkeyPatch,
    category: ErrorCategory,
    status_code: int,
) -> None:
    """Every ErrorCategory value emits the metric with its category label."""
    recorder = MagicMock()
    monkeypatch.setattr(exception_handlers, "record_api_error", recorder)

    exception_handlers._build_response(
        _fake_request(),
        detail="boom",
        error_code=ErrorCode.PROVIDER_ERROR,
        error_category=category,
        status_code=status_code,
    )

    recorder.assert_called_once_with(category=category.value, status_code=status_code)


def test_build_response_skips_metric_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-error (< 400) status codes must not touch the counter."""
    recorder = MagicMock()
    monkeypatch.setattr(exception_handlers, "record_api_error", recorder)

    exception_handlers._build_response(
        _fake_request(),
        detail="ok",
        error_code=ErrorCode.PROVIDER_ERROR,
        error_category=ErrorCategory.INTERNAL,
        status_code=200,
    )

    recorder.assert_not_called()
