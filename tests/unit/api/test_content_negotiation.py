"""Tests for RFC 9457 content negotiation (application/problem+json)."""

from typing import Any
from unittest.mock import MagicMock

import pytest
from litestar import get
from litestar.exceptions import HTTPException, ValidationException
from litestar.testing import TestClient

from synthorg.api.exception_handlers import _wants_problem_json
from synthorg.core.domain_errors import ServiceUnavailableError, UnauthorizedError
from synthorg.core.error_taxonomy import (
    ErrorCategory,
    ErrorCode,
    category_title,
    category_type_uri,
)
from synthorg.core.persistence_errors import DuplicateRecordError, RecordNotFoundError
from tests.unit.api.conftest import make_exception_handler_app

pytestmark = pytest.mark.unit

_PROBLEM_JSON = "application/problem+json"


class TestContentNegotiation:
    """Tests for application/problem+json content negotiation."""

    def _assert_problem_detail(  # noqa: PLR0913
        self,
        body: dict[str, Any],
        *,
        status: int,
        error_code: int,
        error_category: str,
        retryable: bool,
        retry_after: int | None = None,
    ) -> None:
        """Assert bare RFC 9457 ProblemDetail structure."""
        # Must have RFC 9457 fields
        assert body["status"] == status
        assert body["error_code"] == error_code
        assert body["error_category"] == error_category
        assert body["retryable"] is retryable
        assert body["retry_after"] == retry_after
        assert isinstance(body["title"], str)
        assert len(body["title"]) > 0
        assert isinstance(body["type"], str)
        assert body["type"].startswith("https://")
        assert isinstance(body["detail"], str)
        assert len(body["detail"]) > 0
        assert isinstance(body["instance"], str)
        assert len(body["instance"]) > 0
        # Must NOT have envelope keys
        assert "data" not in body
        assert "error" not in body
        assert "success" not in body
        assert "error_detail" not in body

    @pytest.mark.parametrize(
        ("exc_factory", "status", "error_code", "error_category", "retryable"),
        [
            (
                lambda: RecordNotFoundError("gone"),
                404,
                ErrorCode.RECORD_NOT_FOUND,
                ErrorCategory.NOT_FOUND,
                False,
            ),
            (
                lambda: UnauthorizedError("bad token"),
                401,
                ErrorCode.UNAUTHORIZED,
                ErrorCategory.AUTH,
                False,
            ),
            (
                lambda: DuplicateRecordError("already exists"),
                409,
                ErrorCode.DUPLICATE_RECORD,
                ErrorCategory.CONFLICT,
                False,
            ),
            (
                ValidationException,
                400,
                ErrorCode.REQUEST_VALIDATION_ERROR,
                ErrorCategory.VALIDATION,
                False,
            ),
            (
                lambda: RuntimeError("boom"),
                500,
                ErrorCode.INTERNAL_ERROR,
                ErrorCategory.INTERNAL,
                False,
            ),
            (
                ServiceUnavailableError,
                503,
                ErrorCode.SERVICE_UNAVAILABLE,
                ErrorCategory.INTERNAL,
                True,
            ),
        ],
        ids=[
            "not_found",
            "auth",
            "conflict",
            "validation",
            "unexpected",
            "retryable",
        ],
    )
    def test_problem_json_error_mapping(
        self,
        exc_factory: Any,
        status: int,
        error_code: ErrorCode,
        error_category: ErrorCategory,
        retryable: bool,
    ) -> None:
        """Accept: application/problem+json returns correct RFC 9457 body."""

        @get("/test")
        async def handler() -> None:
            raise exc_factory()

        with TestClient(make_exception_handler_app(handler)) as client:
            resp = client.get(
                "/test",
                headers={"Accept": _PROBLEM_JSON},
            )
            assert resp.status_code == status
            assert _PROBLEM_JSON in resp.headers.get("content-type", "")
            body = resp.json()
            self._assert_problem_detail(
                body,
                status=status,
                error_code=error_code,
                error_category=error_category,
                retryable=retryable,
            )
            assert body["title"] == category_title(error_category)
            assert body["type"] == category_type_uri(error_category)

    def test_problem_json_5xx_scrubs_detail(self) -> None:
        """5xx problem+json responses scrub internal details."""

        @get("/test")
        async def handler() -> None:
            msg = "Connection pool exhausted: 10.0.0.5:5432"
            raise ServiceUnavailableError(msg)

        with TestClient(make_exception_handler_app(handler)) as client:
            resp = client.get(
                "/test",
                headers={"Accept": _PROBLEM_JSON},
            )
            assert resp.status_code == 503
            body = resp.json()
            assert body["detail"] == "Service unavailable"
            assert "10.0.0.5" not in body["detail"]

    def test_problem_json_for_http_exception_headers(self) -> None:
        """HTTPException safe headers pass through in problem+json."""

        @get("/test")
        async def handler() -> None:
            raise HTTPException(
                status_code=429,
                detail="Slow down",
                headers={"Retry-After": "60", "X-Internal": "secret"},
            )

        with TestClient(make_exception_handler_app(handler)) as client:
            resp = client.get(
                "/test",
                headers={"Accept": _PROBLEM_JSON},
            )
            assert resp.status_code == 429
            assert _PROBLEM_JSON in resp.headers.get("content-type", "")
            assert resp.headers.get("retry-after") == "60"
            assert "x-internal" not in resp.headers

    def test_default_accept_returns_envelope(self) -> None:
        """No Accept header returns the envelope format."""

        @get("/test")
        async def handler() -> None:
            msg = "gone"
            raise RecordNotFoundError(msg)

        with TestClient(make_exception_handler_app(handler)) as client:
            resp = client.get("/test")
            assert resp.status_code == 404
            body = resp.json()
            assert "success" in body
            assert "error" in body
            assert "error_detail" in body

    def test_accept_json_returns_envelope(self) -> None:
        """Accept: application/json returns the envelope format."""

        @get("/test")
        async def handler() -> None:
            msg = "gone"
            raise RecordNotFoundError(msg)

        with TestClient(make_exception_handler_app(handler)) as client:
            resp = client.get(
                "/test",
                headers={"Accept": "application/json"},
            )
            assert resp.status_code == 404
            body = resp.json()
            assert "success" in body
            assert "error" in body
            assert "error_detail" in body

    def test_accept_wildcard_returns_envelope(self) -> None:
        """Accept: */* returns the envelope format."""

        @get("/test")
        async def handler() -> None:
            msg = "gone"
            raise RecordNotFoundError(msg)

        with TestClient(make_exception_handler_app(handler)) as client:
            resp = client.get(
                "/test",
                headers={"Accept": "*/*"},
            )
            assert resp.status_code == 404
            body = resp.json()
            assert "success" in body
            assert "error_detail" in body

    def test_problem_json_preferred_over_json(self) -> None:
        """problem+json preferred when listed with higher quality."""

        @get("/test")
        async def handler() -> None:
            msg = "gone"
            raise RecordNotFoundError(msg)

        with TestClient(make_exception_handler_app(handler)) as client:
            resp = client.get(
                "/test",
                headers={
                    "Accept": "application/problem+json, application/json;q=0.9",
                },
            )
            assert resp.status_code == 404
            assert _PROBLEM_JSON in resp.headers.get("content-type", "")
            body = resp.json()
            assert "status" in body
            assert "success" not in body


class TestWantsProblemJson:
    """Direct unit tests for _wants_problem_json helper."""

    def test_returns_true_for_problem_json(self) -> None:
        request = MagicMock()
        request.accept.best_match.return_value = _PROBLEM_JSON
        assert _wants_problem_json(request) is True

    def test_returns_false_for_json(self) -> None:
        request = MagicMock()
        request.accept.best_match.return_value = "application/json"
        assert _wants_problem_json(request) is False

    def test_returns_false_for_none(self) -> None:
        request = MagicMock()
        request.accept.best_match.return_value = None
        assert _wants_problem_json(request) is False

    def test_returns_false_on_exception(self) -> None:
        """Defensive: returns False if Accept parsing raises."""
        request = MagicMock()
        request.accept.best_match.side_effect = RuntimeError("broken")
        assert _wants_problem_json(request) is False
