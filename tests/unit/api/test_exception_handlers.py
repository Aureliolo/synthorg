"""Tests for exception handlers with RFC 9457 structured error responses."""

import re
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import structlog
from litestar import get, post
from litestar.exceptions import (
    HTTPException,
    NotAuthorizedException,
    PermissionDeniedException,
    ValidationException,
)
from litestar.testing import TestClient

from synthorg.api.errors import (
    ApiError,
    ApiValidationError,
    ConflictError,
    ErrorCategory,
    ErrorCode,
    ForbiddenError,
    NotFoundError,
    ServiceUnavailableError,
    UnauthorizedError,
    category_title,
    category_type_uri,
)
from synthorg.api.exception_handlers import (
    _build_error_response,
    _build_response,
    _category_for_status,
    _get_instance_id,
    handle_http_exception,
    handle_unexpected,
)
from synthorg.backup.errors import (
    BackupError,
    BackupInProgressError,
    BackupNotFoundError,
    ManifestError,
)
from synthorg.persistence.errors import (
    DuplicateRecordError,
    PersistenceError,
    RecordNotFoundError,
)
from tests.unit.api.conftest import make_exception_handler_app

pytestmark = pytest.mark.unit

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
)


def _assert_error_detail(
    body: dict[str, Any],
    *,
    error_code: int,
    error_category: str,
    retryable: bool,
    retry_after: int | None = None,
) -> None:
    """Assert common error_detail structure."""
    detail = body["error_detail"]
    assert detail is not None
    assert detail["error_code"] == error_code
    assert detail["error_category"] == error_category
    assert detail["retryable"] is retryable
    assert detail["retry_after"] == retry_after
    assert detail["detail"] == body["error"]
    # RFC 9457 title/type fields
    assert isinstance(detail["title"], str)
    assert len(detail["title"]) > 0
    assert isinstance(detail["type"], str)
    assert detail["type"].startswith("https://")
    # instance should be a non-empty string (UUID format when middleware runs)
    assert isinstance(detail["instance"], str)
    assert len(detail["instance"]) > 0


class TestExceptionHandlers:
    def test_record_not_found_maps_to_404(self) -> None:
        @get("/test")
        async def handler() -> None:
            msg = "gone"
            raise RecordNotFoundError(msg)

        with TestClient(make_exception_handler_app(handler)) as client:
            resp = client.get("/test")
            assert resp.status_code == 404
            body = resp.json()
            assert body["success"] is False
            # Error message is scrubbed -- internal details not exposed.
            assert body["error"] == "Resource not found"
            _assert_error_detail(
                body,
                error_code=ErrorCode.RECORD_NOT_FOUND,
                error_category=ErrorCategory.NOT_FOUND,
                retryable=False,
            )

    def test_duplicate_record_maps_to_409(self) -> None:
        @get("/test")
        async def handler() -> None:
            msg = "exists"
            raise DuplicateRecordError(msg)

        with TestClient(make_exception_handler_app(handler)) as client:
            resp = client.get("/test")
            assert resp.status_code == 409
            body = resp.json()
            assert body["error"] == "Resource already exists"
            _assert_error_detail(
                body,
                error_code=ErrorCode.DUPLICATE_RECORD,
                error_category=ErrorCategory.CONFLICT,
                retryable=False,
            )

    def test_persistence_error_maps_to_500(self) -> None:
        @get("/test")
        async def handler() -> None:
            msg = "db fail"
            raise PersistenceError(msg)

        with TestClient(make_exception_handler_app(handler)) as client:
            resp = client.get("/test")
            assert resp.status_code == 500
            body = resp.json()
            assert body["success"] is False
            assert body["error"] == "Internal server error"
            _assert_error_detail(
                body,
                error_code=ErrorCode.PERSISTENCE_ERROR,
                error_category=ErrorCategory.INTERNAL,
                retryable=False,
            )

    def test_backup_not_found_error_maps_to_404(self) -> None:
        @get("/test")
        async def handler() -> None:
            msg = "Backup not found: abc123"
            raise BackupNotFoundError(msg)

        with TestClient(make_exception_handler_app(handler)) as client:
            resp = client.get("/test")
            assert resp.status_code == 404
            body = resp.json()
            assert body["success"] is False
            assert body["error"] == "Backup not found: abc123"
            _assert_error_detail(
                body,
                error_code=ErrorCode.RECORD_NOT_FOUND,
                error_category=ErrorCategory.NOT_FOUND,
                retryable=False,
            )

    def test_backup_in_progress_error_maps_to_409(self) -> None:
        @get("/test")
        async def handler() -> None:
            msg = "A backup is already in progress"
            raise BackupInProgressError(msg)

        with TestClient(make_exception_handler_app(handler)) as client:
            resp = client.get("/test")
            assert resp.status_code == 409
            body = resp.json()
            assert body["success"] is False
            assert body["error"] == "A backup is already in progress"
            _assert_error_detail(
                body,
                error_code=ErrorCode.RESOURCE_CONFLICT,
                error_category=ErrorCategory.CONFLICT,
                retryable=False,
            )

    def test_manifest_error_maps_to_structured_500(self) -> None:
        @get("/test")
        async def handler() -> None:
            msg = "Manifest checksum mismatch"
            raise ManifestError(msg)

        with TestClient(make_exception_handler_app(handler)) as client:
            resp = client.get("/test")
            assert resp.status_code == 500
            body = resp.json()
            assert body["success"] is False
            # 5xx scrubs the upstream message; the structured envelope
            # surfaces the category title, not the raw exception text.
            assert body["error"] == "Backup operation failed"
            _assert_error_detail(
                body,
                error_code=ErrorCode.INTERNAL_ERROR,
                error_category=ErrorCategory.INTERNAL,
                retryable=False,
            )

    def test_generic_backup_error_does_not_fall_through_to_500_unstructured(
        self,
    ) -> None:
        """Catch-all BackupError must hit handle_backup_error, not handle_unexpected.

        Regression test for ZAP DAST 90022/10023: prior to the BackupError
        handler being added to EXCEPTION_HANDLERS, any BackupError subtype
        not explicitly handled in a controller fell through to
        handle_unexpected, returning a generic 500 with no Backup-specific
        error_code.
        """

        @get("/test")
        async def handler() -> None:
            msg = "generic backup failure"
            raise BackupError(msg)

        with TestClient(make_exception_handler_app(handler)) as client:
            resp = client.get("/test")
            assert resp.status_code == 500
            body = resp.json()
            assert body["error"] == "Backup operation failed"
            # Goes through handle_backup_error, not handle_unexpected.
            assert body["error_detail"]["error_code"] == ErrorCode.INTERNAL_ERROR

    def test_api_not_found_error_maps_to_404(self) -> None:
        @get("/test")
        async def handler() -> None:
            msg = "nope"
            raise NotFoundError(msg)

        with TestClient(make_exception_handler_app(handler)) as client:
            resp = client.get("/test")
            assert resp.status_code == 404
            body = resp.json()
            # 4xx errors return the actual exception message
            assert body["error"] == "nope"
            _assert_error_detail(
                body,
                error_code=ErrorCode.RESOURCE_NOT_FOUND,
                error_category=ErrorCategory.NOT_FOUND,
                retryable=False,
            )

    def test_api_conflict_error_maps_to_409(self) -> None:
        @get("/test")
        async def handler() -> None:
            msg = "conflict"
            raise ConflictError(msg)

        with TestClient(make_exception_handler_app(handler)) as client:
            resp = client.get("/test")
            assert resp.status_code == 409
            body = resp.json()
            assert body["error"] == "conflict"
            _assert_error_detail(
                body,
                error_code=ErrorCode.RESOURCE_CONFLICT,
                error_category=ErrorCategory.CONFLICT,
                retryable=False,
            )

    def test_api_forbidden_error_maps_to_403(self) -> None:
        @get("/test")
        async def handler() -> None:
            msg = "denied"
            raise ForbiddenError(msg)

        with TestClient(make_exception_handler_app(handler)) as client:
            resp = client.get("/test")
            assert resp.status_code == 403
            body = resp.json()
            assert body["error"] == "denied"
            _assert_error_detail(
                body,
                error_code=ErrorCode.FORBIDDEN,
                error_category=ErrorCategory.AUTH,
                retryable=False,
            )

    def test_value_error_falls_through_to_catch_all(self) -> None:
        @get("/test")
        async def handler() -> None:
            msg = "bad input"
            raise ValueError(msg)

        with TestClient(make_exception_handler_app(handler)) as client:
            resp = client.get("/test")
            assert resp.status_code == 500
            body = resp.json()
            _assert_error_detail(
                body,
                error_code=ErrorCode.INTERNAL_ERROR,
                error_category=ErrorCategory.INTERNAL,
                retryable=False,
            )

    def test_unexpected_error_maps_to_500(self) -> None:
        @get("/test")
        async def handler() -> None:
            msg = "boom"
            raise RuntimeError(msg)

        with TestClient(make_exception_handler_app(handler)) as client:
            resp = client.get("/test")
            assert resp.status_code == 500
            body = resp.json()
            assert body["success"] is False
            assert body["error"] == "Internal server error"
            _assert_error_detail(
                body,
                error_code=ErrorCode.INTERNAL_ERROR,
                error_category=ErrorCategory.INTERNAL,
                retryable=False,
            )

    def test_unauthorized_error_maps_to_401(self) -> None:
        @get("/test")
        async def handler() -> None:
            msg = "Invalid credentials"
            raise UnauthorizedError(msg)

        with TestClient(make_exception_handler_app(handler)) as client:
            resp = client.get("/test")
            assert resp.status_code == 401
            body = resp.json()
            # 4xx returns the actual message, not the generic default
            assert body["error"] == "Invalid credentials"
            _assert_error_detail(
                body,
                error_code=ErrorCode.UNAUTHORIZED,
                error_category=ErrorCategory.AUTH,
                retryable=False,
            )

    def test_validation_error_maps_to_422(self) -> None:
        @get("/test")
        async def handler() -> None:
            msg = "Bad field"
            raise ApiValidationError(msg)

        with TestClient(make_exception_handler_app(handler)) as client:
            resp = client.get("/test")
            assert resp.status_code == 422
            body = resp.json()
            assert body["error"] == "Bad field"
            _assert_error_detail(
                body,
                error_code=ErrorCode.VALIDATION_ERROR,
                error_category=ErrorCategory.VALIDATION,
                retryable=False,
            )

    def test_unmatched_route_returns_404(self) -> None:
        """NotFoundException for unknown routes returns 404, not 500."""

        @get("/test")
        async def handler() -> str:
            return "ok"

        with TestClient(make_exception_handler_app(handler)) as client:
            resp = client.get("/nonexistent")
            assert resp.status_code == 404
            body = resp.json()
            assert body["success"] is False
            assert body["error"] == "Not found"
            _assert_error_detail(
                body,
                error_code=ErrorCode.ROUTE_NOT_FOUND,
                error_category=ErrorCategory.NOT_FOUND,
                retryable=False,
            )

    def test_litestar_permission_denied_maps_to_403(self) -> None:
        """PermissionDeniedException with no detail falls back to 'Forbidden'."""

        @get("/test")
        async def handler() -> None:
            raise PermissionDeniedException

        with TestClient(make_exception_handler_app(handler)) as client:
            resp = client.get("/test")
            assert resp.status_code == 403
            body = resp.json()
            assert body["success"] is False
            assert body["error"] == "Forbidden"
            _assert_error_detail(
                body,
                error_code=ErrorCode.FORBIDDEN,
                error_category=ErrorCategory.AUTH,
                retryable=False,
            )

    def test_permission_denied_scrubs_custom_detail(self) -> None:
        """PermissionDeniedException always returns fixed 'Forbidden' message."""

        @get("/test")
        async def handler() -> None:
            raise PermissionDeniedException(detail="Write access denied")

        with TestClient(make_exception_handler_app(handler)) as client:
            resp = client.get("/test")
            assert resp.status_code == 403
            body = resp.json()
            assert body["error"] == "Forbidden"
            _assert_error_detail(
                body,
                error_code=ErrorCode.FORBIDDEN,
                error_category=ErrorCategory.AUTH,
                retryable=False,
            )

    def test_litestar_not_authorized_maps_to_401(self) -> None:
        """NotAuthorizedException with default detail returns 401."""

        @get("/test")
        async def handler() -> None:
            raise NotAuthorizedException

        with TestClient(make_exception_handler_app(handler)) as client:
            resp = client.get("/test")
            assert resp.status_code == 401
            body = resp.json()
            assert body["success"] is False
            assert body["error"] == "Authentication required"
            _assert_error_detail(
                body,
                error_code=ErrorCode.UNAUTHORIZED,
                error_category=ErrorCategory.AUTH,
                retryable=False,
            )

    def test_not_authorized_scrubs_custom_detail(self) -> None:
        """NotAuthorizedException always returns fixed message."""

        @get("/test")
        async def handler() -> None:
            raise NotAuthorizedException(detail="Invalid JWT token")

        with TestClient(make_exception_handler_app(handler)) as client:
            resp = client.get("/test")
            assert resp.status_code == 401
            body = resp.json()
            assert body["error"] == "Authentication required"
            _assert_error_detail(
                body,
                error_code=ErrorCode.UNAUTHORIZED,
                error_category=ErrorCategory.AUTH,
                retryable=False,
            )

    def test_litestar_validation_exception_maps_to_400(self) -> None:
        """Litestar ValidationException forwards its detail."""

        @get("/test")
        async def handler() -> None:
            raise ValidationException

        with TestClient(make_exception_handler_app(handler)) as client:
            resp = client.get("/test")
            assert resp.status_code == 400
            body = resp.json()
            assert body["success"] is False
            # Default ValidationException detail is "Bad Request"
            assert isinstance(body["error"], str)
            assert len(body["error"]) > 0
            _assert_error_detail(
                body,
                error_code=ErrorCode.REQUEST_VALIDATION_ERROR,
                error_category=ErrorCategory.VALIDATION,
                retryable=False,
            )

    def test_method_not_allowed_maps_to_405(self) -> None:
        """Router-level MethodNotAllowed returns 405 via HTTPException handler."""

        @post("/test")
        async def handler() -> str:
            return "ok"

        with TestClient(make_exception_handler_app(handler)) as client:
            resp = client.get("/test")
            assert resp.status_code == 405
            body = resp.json()
            assert body["success"] is False
            assert body["error"] == "Method Not Allowed"
            assert "POST" in resp.headers.get("allow", "")
            _assert_error_detail(
                body,
                error_code=ErrorCode.REQUEST_VALIDATION_ERROR,
                error_category=ErrorCategory.VALIDATION,
                retryable=False,
            )

    def test_http_exception_5xx_returns_scrubbed_message(self) -> None:
        """5xx HTTPException scrubs detail to prevent info leakage."""

        @get("/test")
        async def handler() -> None:
            raise HTTPException(
                status_code=502,
                detail="upstream db connection refused",
            )

        with TestClient(make_exception_handler_app(handler)) as client:
            resp = client.get("/test")
            assert resp.status_code == 502
            body = resp.json()
            assert body["success"] is False
            assert body["error"] == "Internal server error"
            _assert_error_detail(
                body,
                error_code=ErrorCode.INTERNAL_ERROR,
                error_category=ErrorCategory.INTERNAL,
                retryable=False,
            )

    def test_http_exception_empty_detail_uses_phrase(self) -> None:
        """HTTPException with empty detail falls back to HTTP phrase."""

        @get("/test")
        async def handler() -> None:
            raise HTTPException(status_code=429)

        with TestClient(make_exception_handler_app(handler)) as client:
            resp = client.get("/test")
            assert resp.status_code == 429
            body = resp.json()
            assert body["error"] == "Too Many Requests"
            _assert_error_detail(
                body,
                error_code=ErrorCode.RATE_LIMITED,
                error_category=ErrorCategory.RATE_LIMIT,
                retryable=True,
            )

    def test_http_exception_nonstandard_status_uses_fallback(self) -> None:
        """Non-standard status code falls back to generic message."""
        exc = MagicMock(spec=HTTPException)
        exc.status_code = 499
        exc.detail = ""
        exc.headers = None

        request = MagicMock()
        request.method = "GET"
        request.url.path = "/test"
        request.accept.best_match.return_value = "application/json"

        resp = handle_http_exception(request, exc)
        assert resp.status_code == 499
        assert resp.content.error == "Request error"  # type: ignore[union-attr]
        assert resp.content.error_detail is not None  # type: ignore[union-attr]
        assert resp.content.error_detail.error_category == ErrorCategory.VALIDATION  # type: ignore[union-attr]


class TestStructuredErrorMetadata:
    """Tests specifically for RFC 9457 structured error features."""

    def test_service_unavailable_is_retryable(self) -> None:
        @get("/test")
        async def handler() -> None:
            raise ServiceUnavailableError

        with TestClient(make_exception_handler_app(handler)) as client:
            resp = client.get("/test")
            assert resp.status_code == 503
            body = resp.json()
            assert body["error"] == "Service unavailable"
            _assert_error_detail(
                body,
                error_code=ErrorCode.SERVICE_UNAVAILABLE,
                error_category=ErrorCategory.INTERNAL,
                retryable=True,
            )

    def test_http_429_is_retryable(self) -> None:
        @get("/test")
        async def handler() -> None:
            raise HTTPException(status_code=429, detail="Slow down")

        with TestClient(make_exception_handler_app(handler)) as client:
            resp = client.get("/test")
            assert resp.status_code == 429
            body = resp.json()
            _assert_error_detail(
                body,
                error_code=ErrorCode.RATE_LIMITED,
                error_category=ErrorCategory.RATE_LIMIT,
                retryable=True,
            )

    def test_http_429_retry_after_header_propagated_to_body(self) -> None:
        """Retry-After header value is parsed into the body field."""

        @get("/test")
        async def handler() -> None:
            raise HTTPException(
                status_code=429,
                detail="Slow down",
                headers={"Retry-After": "60"},
            )

        with TestClient(make_exception_handler_app(handler)) as client:
            resp = client.get("/test")
            assert resp.status_code == 429
            body = resp.json()
            assert resp.headers.get("retry-after") == "60"
            assert body["error_detail"]["retry_after"] == 60
            assert body["error_detail"]["retryable"] is True

    def test_http_503_is_retryable(self) -> None:
        @get("/test")
        async def handler() -> None:
            raise HTTPException(status_code=503)

        with TestClient(make_exception_handler_app(handler)) as client:
            resp = client.get("/test")
            assert resp.status_code == 503
            body = resp.json()
            _assert_error_detail(
                body,
                error_code=ErrorCode.SERVICE_UNAVAILABLE,
                error_category=ErrorCategory.INTERNAL,
                retryable=True,
            )

    def test_instance_is_valid_uuid_format(self) -> None:
        """instance field should be a UUID when middleware is not active."""

        exc = RuntimeError("boom")
        request = MagicMock()
        request.method = "GET"
        request.url.path = "/test"
        request.accept.best_match.return_value = "application/json"

        resp = handle_unexpected(request, exc)
        instance = resp.content.error_detail.instance  # type: ignore[union-attr]
        assert _UUID_RE.match(instance), f"Expected UUID, got {instance!r}"

    def test_error_detail_detail_matches_error_field(self) -> None:
        """error_detail.detail must match the top-level error field."""

        @get("/test")
        async def handler() -> None:
            msg = "custom not found"
            raise NotFoundError(msg)

        with TestClient(make_exception_handler_app(handler)) as client:
            resp = client.get("/test")
            body = resp.json()
            assert body["error_detail"]["detail"] == body["error"]

    def test_error_detail_has_title_and_type(self) -> None:
        """error_detail includes RFC 9457 title and type fields."""

        @get("/test")
        async def handler() -> None:
            msg = "gone"
            raise NotFoundError(msg)

        with TestClient(make_exception_handler_app(handler)) as client:
            resp = client.get("/test")
            body = resp.json()
            ed = body["error_detail"]
            assert ed["title"] == category_title(ErrorCategory.NOT_FOUND)
            assert ed["type"] == category_type_uri(ErrorCategory.NOT_FOUND)

    def test_retry_after_is_none_for_non_rate_limit(self) -> None:
        """retry_after should be None for non-rate-limit errors."""

        @get("/test")
        async def handler() -> None:
            msg = "dup"
            raise ConflictError(msg)

        with TestClient(make_exception_handler_app(handler)) as client:
            resp = client.get("/test")
            body = resp.json()
            assert body["error_detail"]["retry_after"] is None

    def test_5xx_scrubs_custom_message(self) -> None:
        """ServiceUnavailableError with custom message returns default."""

        @get("/test")
        async def handler() -> None:
            msg = "Connection pool exhausted: 10.0.0.5:5432"
            raise ServiceUnavailableError(msg)

        with TestClient(make_exception_handler_app(handler)) as client:
            resp = client.get("/test")
            assert resp.status_code == 503
            body = resp.json()
            # 5xx must scrub to class-level default, not leak internals
            assert body["error"] == "Service unavailable"
            assert "10.0.0.5" not in body["error"]


class TestGetInstanceId:
    """Direct unit tests for _get_instance_id helper."""

    def test_returns_request_id_from_context(self) -> None:
        structlog.contextvars.bind_contextvars(request_id="req-known-123")
        try:
            result = _get_instance_id()
            assert result == "req-known-123"
        finally:
            structlog.contextvars.unbind_contextvars("request_id")

    def test_falls_back_to_uuid_when_no_context(self) -> None:
        structlog.contextvars.unbind_contextvars("request_id")

        result = _get_instance_id()
        assert _UUID_RE.match(result)

    def test_falls_back_for_non_string_request_id(self) -> None:
        structlog.contextvars.bind_contextvars(request_id=12345)
        try:
            result = _get_instance_id()
            assert _UUID_RE.match(result)
        finally:
            structlog.contextvars.unbind_contextvars("request_id")

    def test_falls_back_for_empty_string_request_id(self) -> None:
        structlog.contextvars.bind_contextvars(request_id="")
        try:
            result = _get_instance_id()
            assert _UUID_RE.match(result)
        finally:
            structlog.contextvars.unbind_contextvars("request_id")


class TestCategoryForStatus:
    """Direct unit tests for _category_for_status helper."""

    @pytest.mark.parametrize(
        ("status", "expected_code", "expected_category", "expected_retryable"),
        [
            (401, ErrorCode.UNAUTHORIZED, ErrorCategory.AUTH, False),
            (403, ErrorCode.FORBIDDEN, ErrorCategory.AUTH, False),
            (404, ErrorCode.ROUTE_NOT_FOUND, ErrorCategory.NOT_FOUND, False),
            (409, ErrorCode.RESOURCE_CONFLICT, ErrorCategory.CONFLICT, False),
            (429, ErrorCode.RATE_LIMITED, ErrorCategory.RATE_LIMIT, True),
            (503, ErrorCode.SERVICE_UNAVAILABLE, ErrorCategory.INTERNAL, True),
        ],
    )
    def test_mapped_status_codes(
        self,
        status: int,
        expected_code: ErrorCode,
        expected_category: ErrorCategory,
        expected_retryable: bool,
    ) -> None:
        code, category, retryable = _category_for_status(status)
        assert code == expected_code
        assert category == expected_category
        assert retryable is expected_retryable

    def test_unmapped_server_error(self) -> None:
        code, category, retryable = _category_for_status(507)
        assert code == ErrorCode.INTERNAL_ERROR
        assert category == ErrorCategory.INTERNAL
        assert retryable is False

    def test_unmapped_client_error(self) -> None:
        code, category, retryable = _category_for_status(418)
        assert code == ErrorCode.REQUEST_VALIDATION_ERROR
        assert category == ErrorCategory.VALIDATION
        assert retryable is False


class TestApiErrorInstantiation:
    """Tests for ApiError and subclass instantiation behavior."""

    @pytest.mark.parametrize(
        ("cls", "expected_status", "expected_default"),
        [
            (NotFoundError, 404, "Resource not found"),
            (ConflictError, 409, "Resource conflict"),
            (ForbiddenError, 403, "Forbidden"),
            (UnauthorizedError, 401, "Authentication required"),
            (ServiceUnavailableError, 503, "Service unavailable"),
        ],
    )
    def test_default_message_and_status(
        self,
        cls: type[ApiError],
        expected_status: int,
        expected_default: str,
    ) -> None:
        exc = cls()
        assert str(exc) == expected_default
        assert exc.status_code == expected_status

    def test_custom_message_takes_precedence(self) -> None:
        exc = NotFoundError("Custom not found")
        assert str(exc) == "Custom not found"
        assert exc.status_code == 404


class TestGetInstanceIdExceptionFallback:
    """Test that _get_instance_id falls back when get_contextvars raises."""

    def test_falls_back_when_get_contextvars_raises(self) -> None:
        with patch(
            "structlog.contextvars.get_contextvars",
            side_effect=RuntimeError("broken"),
        ):
            result = _get_instance_id()
            assert _UUID_RE.match(result)


class TestBuildErrorResponseRetryAfter:
    """Test _build_error_response with non-None retry_after."""

    def test_retry_after_propagated(self) -> None:
        resp = _build_error_response(
            detail="Slow down",
            error_code=ErrorCode.RATE_LIMITED,
            error_category=ErrorCategory.RATE_LIMIT,
            retryable=True,
            retry_after=120,
        )
        assert resp.error_detail is not None
        assert resp.error_detail.retry_after == 120
        assert resp.error_detail.retryable is True
        assert resp.error_detail.title == category_title(ErrorCategory.RATE_LIMIT)
        assert resp.error_detail.type == category_type_uri(ErrorCategory.RATE_LIMIT)


class TestBuildResponseFallback:
    """Test _build_response defensive fallback when construction fails."""

    def test_fallback_returns_500_on_build_failure(self) -> None:
        """If ProblemDetail/ErrorDetail construction fails, return 500."""
        request = MagicMock()
        request.accept.best_match.return_value = "application/json"

        with patch(
            "synthorg.api.exception_handlers._build_error_response",
            side_effect=RuntimeError("construction failed"),
        ):
            resp = _build_response(
                request,
                detail="Resource not found",
                error_code=ErrorCode.RECORD_NOT_FOUND,
                error_category=ErrorCategory.NOT_FOUND,
                status_code=404,
            )
        assert resp.status_code == 500
        assert resp.content == {"error": "Internal server error"}  # type: ignore[comparison-overlap]

    def test_fallback_returns_500_on_problem_json_build_failure(self) -> None:
        """Fallback fires for problem+json path too."""
        request = MagicMock()
        request.accept.best_match.return_value = "application/problem+json"

        with patch(
            "synthorg.api.exception_handlers._build_problem_detail_response",
            side_effect=RuntimeError("construction failed"),
        ):
            resp = _build_response(
                request,
                detail="Resource not found",
                error_code=ErrorCode.RECORD_NOT_FOUND,
                error_category=ErrorCategory.NOT_FOUND,
                status_code=404,
            )
        assert resp.status_code == 500


# ── Domain error base class mappings (#1405) ────────────────────


class TestDomainErrorMapping:
    """Every domain error base and its key subclasses produce RFC 9457.

    Before #1405, seven domain error base classes were not registered in
    EXCEPTION_HANDLERS -- when they escaped a controller, they fell through
    to ``handle_unexpected`` (500, INTERNAL_ERROR).  Now each base declares
    HTTP metadata ClassVars (``status_code``, ``error_code``,
    ``error_category``, ``retryable``, ``default_message``) and
    ``handle_domain_error`` maps them through ``_build_response``,
    giving every domain exception a correct structured response.
    """

    @pytest.mark.parametrize(
        (
            "exc_factory",
            "expected_status",
            "expected_code",
            "expected_category",
            "expected_retryable",
        ),
        [
            pytest.param(
                lambda: __import__(
                    "synthorg.engine.errors",
                    fromlist=["EngineError"],
                ).EngineError("oops"),
                500,
                ErrorCode.ENGINE_ERROR,
                ErrorCategory.INTERNAL,
                False,
                id="engine_base",
            ),
            pytest.param(
                lambda: __import__(
                    "synthorg.engine.errors",
                    fromlist=["TaskNotFoundError"],
                ).TaskNotFoundError("missing"),
                404,
                ErrorCode.TASK_NOT_FOUND,
                ErrorCategory.NOT_FOUND,
                False,
                id="engine_task_not_found",
            ),
            pytest.param(
                lambda: __import__(
                    "synthorg.engine.errors",
                    fromlist=["TaskVersionConflictError"],
                ).TaskVersionConflictError("stale"),
                409,
                ErrorCode.TASK_VERSION_CONFLICT,
                ErrorCategory.CONFLICT,
                False,
                id="engine_version_conflict",
            ),
            pytest.param(
                lambda: __import__(
                    "synthorg.budget.errors",
                    fromlist=["BudgetExhaustedError"],
                ).BudgetExhaustedError("over budget"),
                402,
                ErrorCode.BUDGET_EXHAUSTED,
                ErrorCategory.BUDGET_EXHAUSTED,
                False,
                id="budget_base",
            ),
            pytest.param(
                lambda: __import__(
                    "synthorg.budget.errors",
                    fromlist=["DailyLimitExceededError"],
                ).DailyLimitExceededError("daily cap hit"),
                402,
                ErrorCode.DAILY_LIMIT_EXCEEDED,
                ErrorCategory.BUDGET_EXHAUSTED,
                False,
                id="budget_daily",
            ),
            pytest.param(
                lambda: __import__(
                    "synthorg.budget.errors",
                    fromlist=["MixedCurrencyAggregationError"],
                ).MixedCurrencyAggregationError(
                    "cannot aggregate",
                    currencies=frozenset({"EUR", "JPY"}),
                ),
                409,
                ErrorCode.MIXED_CURRENCY_AGGREGATION,
                ErrorCategory.CONFLICT,
                False,
                id="budget_mixed_currency",
            ),
            pytest.param(
                lambda: __import__(
                    "synthorg.providers.errors",
                    fromlist=["ProviderError"],
                ).ProviderError("upstream fail"),
                502,
                ErrorCode.PROVIDER_ERROR,
                ErrorCategory.PROVIDER_ERROR,
                False,
                id="provider_base",
            ),
            pytest.param(
                lambda: __import__(
                    "synthorg.providers.errors",
                    fromlist=["ProviderTimeoutError"],
                ).ProviderTimeoutError("timed out"),
                504,
                ErrorCode.PROVIDER_TIMEOUT,
                ErrorCategory.PROVIDER_ERROR,
                True,
                id="provider_timeout",
            ),
            pytest.param(
                lambda: __import__(
                    "synthorg.providers.errors",
                    fromlist=["ProviderInternalError"],
                ).ProviderInternalError("upstream 500"),
                502,
                ErrorCode.PROVIDER_INTERNAL,
                ErrorCategory.PROVIDER_ERROR,
                True,
                id="provider_internal",
            ),
            pytest.param(
                lambda: __import__(
                    "synthorg.ontology.errors",
                    fromlist=["OntologyError"],
                ).OntologyError("bad schema"),
                500,
                ErrorCode.ONTOLOGY_ERROR,
                ErrorCategory.INTERNAL,
                False,
                id="ontology_base",
            ),
            pytest.param(
                lambda: __import__(
                    "synthorg.ontology.errors",
                    fromlist=["OntologyNotFoundError"],
                ).OntologyNotFoundError("unknown entity"),
                404,
                ErrorCode.ONTOLOGY_NOT_FOUND,
                ErrorCategory.NOT_FOUND,
                False,
                id="ontology_not_found",
            ),
            pytest.param(
                lambda: __import__(
                    "synthorg.communication.errors",
                    fromlist=["CommunicationError"],
                ).CommunicationError("bus fail"),
                500,
                ErrorCode.COMMUNICATION_ERROR,
                ErrorCategory.INTERNAL,
                False,
                id="communication_base",
            ),
            pytest.param(
                lambda: __import__(
                    "synthorg.communication.errors",
                    fromlist=["ChannelNotFoundError"],
                ).ChannelNotFoundError("no channel"),
                404,
                ErrorCode.CHANNEL_NOT_FOUND,
                ErrorCategory.NOT_FOUND,
                False,
                id="communication_channel_not_found",
            ),
            pytest.param(
                lambda: __import__(
                    "synthorg.integrations.errors",
                    fromlist=["IntegrationError"],
                ).IntegrationError("integration fail"),
                502,
                ErrorCode.INTEGRATION_ERROR,
                ErrorCategory.PROVIDER_ERROR,
                False,
                id="integration_base",
            ),
            pytest.param(
                lambda: __import__(
                    "synthorg.integrations.errors",
                    fromlist=["ConnectionNotFoundError"],
                ).ConnectionNotFoundError("no conn"),
                404,
                ErrorCode.CONNECTION_NOT_FOUND,
                ErrorCategory.NOT_FOUND,
                False,
                id="integration_connection_not_found",
            ),
            pytest.param(
                lambda: __import__(
                    "synthorg.tools.errors",
                    fromlist=["ToolError"],
                ).ToolError("tool fail"),
                500,
                ErrorCode.TOOL_ERROR,
                ErrorCategory.INTERNAL,
                False,
                id="tool_base",
            ),
            pytest.param(
                lambda: __import__(
                    "synthorg.tools.errors",
                    fromlist=["ToolNotFoundError"],
                ).ToolNotFoundError("missing tool"),
                404,
                ErrorCode.TOOL_NOT_FOUND,
                ErrorCategory.NOT_FOUND,
                False,
                id="tool_not_found",
            ),
            pytest.param(
                lambda: __import__(
                    "synthorg.tools.errors",
                    fromlist=["ToolPermissionDeniedError"],
                ).ToolPermissionDeniedError("forbidden tool"),
                403,
                ErrorCode.TOOL_PERMISSION_DENIED,
                ErrorCategory.AUTH,
                False,
                id="tool_permission_denied",
            ),
        ],
    )
    def test_domain_error_base_maps_to_rfc_9457(
        self,
        exc_factory: Any,
        expected_status: int,
        expected_code: int,
        expected_category: str,
        expected_retryable: bool,
    ) -> None:
        @get("/test")
        async def handler() -> None:
            raise exc_factory()

        with TestClient(make_exception_handler_app(handler)) as client:
            resp = client.get("/test")
            assert resp.status_code == expected_status
            body = resp.json()
            assert body["success"] is False
            # Server errors scrub the message; client errors pass through
            if expected_status >= 500:
                assert body["error"] not in ("oops", "upstream fail", "bus fail")
            _assert_error_detail(
                body,
                error_code=expected_code,
                error_category=expected_category,
                retryable=expected_retryable,
            )

    def test_provider_rate_limit_surfaces_retry_after(self) -> None:
        """``RateLimitError`` produces 429 with ``Retry-After`` header."""
        from synthorg.providers.errors import RateLimitError

        msg = "throttled"

        @get("/test")
        async def handler() -> None:
            raise RateLimitError(msg, retry_after=42.0)

        with TestClient(make_exception_handler_app(handler)) as client:
            resp = client.get("/test")
            assert resp.status_code == 429
            assert resp.headers.get("Retry-After") == "42"
            body = resp.json()
            _assert_error_detail(
                body,
                error_code=ErrorCode.RATE_LIMITED,
                error_category=ErrorCategory.RATE_LIMIT,
                retryable=True,
                retry_after=42,
            )

    def test_retryable_provider_timeout_flag_is_set(self) -> None:
        """Retryable provider errors surface ``retryable: True``."""
        from synthorg.providers.errors import ProviderTimeoutError

        msg = "timed out"

        @get("/test")
        async def handler() -> None:
            raise ProviderTimeoutError(msg)

        with TestClient(make_exception_handler_app(handler)) as client:
            resp = client.get("/test")
            assert resp.status_code == 504
            body = resp.json()
            assert body["error_detail"]["retryable"] is True


# ── Bare-Response fix tests (#1405) ─────────────────────────────


class TestBareResponseFixes:
    """Controllers no longer return bare ``Response`` for error paths.

    Before #1405, ``artifacts.py:354``, ``subworkflows.py:176``, and
    ``projects.py:74,113`` returned plain ``Response(content=ApiResponse(
    error="..."))`` objects that bypassed the RFC 9457 handler
    registration.  Now each site raises a typed ``ApiError`` subclass
    (or lets a domain error with ClassVar metadata propagate) so the
    central handlers produce a structured response with the correct
    ``error_detail`` envelope.
    """

    def test_artifact_too_large_produces_rfc_9457_413(self) -> None:
        """Artifact upload over the size limit returns 413 + error_detail."""
        from synthorg.api.errors import ArtifactTooLargeApiError

        @post("/upload")
        async def handler() -> None:
            raise ArtifactTooLargeApiError

        with TestClient(make_exception_handler_app(handler)) as client:
            resp = client.post("/upload")
            assert resp.status_code == 413
            body = resp.json()
            assert body["success"] is False
            _assert_error_detail(
                body,
                error_code=ErrorCode.ARTIFACT_TOO_LARGE,
                error_category=ErrorCategory.VALIDATION,
                retryable=False,
            )

    def test_subworkflow_not_found_produces_rfc_9457_404(self) -> None:
        """SubworkflowNotFoundError escapes as 404 with structured detail."""
        from synthorg.engine.errors import SubworkflowNotFoundError

        msg = "Subworkflow 'foo' version '1.0' not found"

        @get("/sub")
        async def handler() -> None:
            raise SubworkflowNotFoundError(
                msg,
                subworkflow_id="foo",
                version="1.0",
            )

        with TestClient(make_exception_handler_app(handler)) as client:
            resp = client.get("/sub")
            assert resp.status_code == 404
            body = resp.json()
            assert body["success"] is False
            _assert_error_detail(
                body,
                error_code=ErrorCode.SUBWORKFLOW_NOT_FOUND,
                error_category=ErrorCategory.NOT_FOUND,
                retryable=False,
            )

    def test_invalid_project_status_produces_rfc_9457_422(self) -> None:
        """Invalid project status filter raises ApiValidationError (422)."""

        @get("/projects")
        async def handler() -> None:
            msg = "Invalid project status: 'bogus'. Valid values: active"
            raise ApiValidationError(msg)

        with TestClient(make_exception_handler_app(handler)) as client:
            resp = client.get("/projects")
            assert resp.status_code == 422
            body = resp.json()
            assert body["success"] is False
            assert "bogus" in body["error"]
            _assert_error_detail(
                body,
                error_code=ErrorCode.VALIDATION_ERROR,
                error_category=ErrorCategory.VALIDATION,
                retryable=False,
            )

    def test_project_not_found_produces_rfc_9457_404(self) -> None:
        """Missing project raises NotFoundError (404)."""

        @get("/projects/{project_id:str}")
        async def handler(project_id: str) -> None:
            msg = f"Project {project_id!r} not found"
            raise NotFoundError(msg)

        with TestClient(make_exception_handler_app(handler)) as client:
            resp = client.get("/projects/missing")
            assert resp.status_code == 404
            body = resp.json()
            assert body["success"] is False
            assert "missing" in body["error"]
            _assert_error_detail(
                body,
                error_code=ErrorCode.RESOURCE_NOT_FOUND,
                error_category=ErrorCategory.NOT_FOUND,
                retryable=False,
            )
