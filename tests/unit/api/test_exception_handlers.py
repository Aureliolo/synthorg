"""Tests for exception handlers with RFC 9457 structured error responses."""

import re
from collections.abc import Callable
from typing import Annotated
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
from litestar.params import PathParameter

from synthorg.api.dto import ProblemDetail
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
    ComponentBackupError,
    ManifestError,
    RestoreError,
    RetentionError,
)
from synthorg.core.domain_errors import (
    ConflictError,
    DomainError,
    ForbiddenError,
    NotFoundError,
    ServiceUnavailableError,
    UnauthorizedError,
    ValidationError,
)
from synthorg.core.error_taxonomy import (
    ErrorCategory,
    ErrorCode,
    category_title,
    category_type_uri,
)
from synthorg.core.persistence_errors import (
    DuplicateRecordError,
    PersistenceError,
    RecordNotFoundError,
)
from tests._shared import JsonDict, LoopAsyncClient
from tests.unit.api.conftest import make_exception_handler_app

pytestmark = pytest.mark.unit

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
)


def _assert_error_detail(
    body: JsonDict,
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
    async def test_record_not_found_maps_to_404(self) -> None:
        @get("/test")
        async def handler() -> None:
            msg = "gone"
            raise RecordNotFoundError(msg)

        async with LoopAsyncClient(make_exception_handler_app(handler)) as client:
            resp = await client.get("/test")
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

    async def test_duplicate_record_maps_to_409(self) -> None:
        @get("/test")
        async def handler() -> None:
            msg = "exists"
            raise DuplicateRecordError(msg)

        async with LoopAsyncClient(make_exception_handler_app(handler)) as client:
            resp = await client.get("/test")
            assert resp.status_code == 409
            body = resp.json()
            assert body["error"] == "Resource already exists"
            _assert_error_detail(
                body,
                error_code=ErrorCode.DUPLICATE_RECORD,
                error_category=ErrorCategory.CONFLICT,
                retryable=False,
            )

    async def test_persistence_error_maps_to_500(self) -> None:
        @get("/test")
        async def handler() -> None:
            msg = "db fail"
            raise PersistenceError(msg)

        async with LoopAsyncClient(make_exception_handler_app(handler)) as client:
            resp = await client.get("/test")
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

    async def test_constraint_violation_error_maps_to_400(self) -> None:
        """Backstop: persistence integrity violations -> 400.

        Repository modules translate driver integrity errors (psycopg
        ForeignKeyViolation / UniqueViolation / NotNullViolation,
        SQLite IntegrityError) into ``ConstraintViolationError`` at
        the persistence boundary. The api layer registers the domain
        class so callers see "you sent a bad reference" instead of
        "we exploded internally". Domain code is expected to
        validate-first, so this handler exists as a defence against
        race conditions and any path that misses pre-validation.
        """
        from synthorg.core.persistence_errors import ConstraintViolationError

        @get("/test")
        async def handler() -> None:
            msg = "fk constraint x violated"
            raise ConstraintViolationError(msg, constraint="connections_fk")

        async with LoopAsyncClient(make_exception_handler_app(handler)) as client:
            resp = await client.get("/test")
            assert resp.status_code == 400
            body = resp.json()
            assert body["success"] is False
            assert body["error"] == "persistence integrity violation"
            _assert_error_detail(
                body,
                error_code=ErrorCode.VALIDATION_ERROR,
                error_category=ErrorCategory.VALIDATION,
                retryable=False,
            )

    # The five backup tests below use the explicit form (rather than
    # collapsing into one parametrize) because each exercises a distinct
    # dispatch branch (404 / 409 / 500-with-specific-subtype /
    # 500-via-base-class) and the assertions diverge on more than the
    # exception input.  An assertion failure should name which branch
    # diverged without the reader having to decode parameter ids.

    async def test_backup_not_found_error_maps_to_404(self) -> None:
        @get("/test")
        async def handler() -> None:
            msg = "Backup not found: abc123"
            raise BackupNotFoundError(msg)

        with structlog.testing.capture_logs() as logs:
            async with LoopAsyncClient(make_exception_handler_app(handler)) as client:
                resp = await client.get("/test")
                assert resp.status_code == 404
                body = resp.json()
                assert body["success"] is False
                # 4xx responses surface the raw exception message.  The
                # backup_id in that message is a public identifier (returned
                # by ``GET /admin/backups`` as resource metadata), not PII or
                # internal state, so the pass-through is intentional.
                assert body["error"] == "Backup not found: abc123"
                _assert_error_detail(
                    body,
                    error_code=ErrorCode.BACKUP_NOT_FOUND,
                    error_category=ErrorCategory.NOT_FOUND,
                    retryable=False,
                )
        request_errors = [
            log for log in logs if log.get("event") == "api.request.error"
        ]
        assert request_errors, "_log_error must emit api.request.error"
        assert request_errors[0]["log_level"] == "warning"

    async def test_backup_in_progress_error_maps_to_409(self) -> None:
        @get("/test")
        async def handler() -> None:
            msg = "A backup is already in progress"
            raise BackupInProgressError(msg)

        with structlog.testing.capture_logs() as logs:
            async with LoopAsyncClient(make_exception_handler_app(handler)) as client:
                resp = await client.get("/test")
                assert resp.status_code == 409
                body = resp.json()
                assert body["success"] is False
                assert body["error"] == "A backup is already in progress"
                _assert_error_detail(
                    body,
                    error_code=ErrorCode.BACKUP_IN_PROGRESS,
                    error_category=ErrorCategory.CONFLICT,
                    retryable=False,
                )
        request_errors = [
            log for log in logs if log.get("event") == "api.request.error"
        ]
        assert request_errors[0]["log_level"] == "warning"

    async def test_manifest_error_maps_to_structured_500(self) -> None:
        @get("/test")
        async def handler() -> None:
            msg = "Manifest checksum mismatch"
            raise ManifestError(msg)

        with structlog.testing.capture_logs() as logs:
            async with LoopAsyncClient(make_exception_handler_app(handler)) as client:
                resp = await client.get("/test")
                assert resp.status_code == 500
                body = resp.json()
                assert body["success"] is False
                # 5xx scrubs the upstream message to the class default; the
                # distinct ``BACKUP_MANIFEST_ERROR`` code lets clients tell
                # a corrupt-manifest failure apart from a generic 500.
                assert body["error"] == "Backup manifest is invalid or corrupt"
                _assert_error_detail(
                    body,
                    error_code=ErrorCode.BACKUP_MANIFEST_ERROR,
                    error_category=ErrorCategory.INTERNAL,
                    retryable=False,
                )
        request_errors = [
            log for log in logs if log.get("event") == "api.request.error"
        ]
        assert request_errors[0]["log_level"] == "error"

    async def test_generic_backup_error_does_not_fall_through_to_500_unstructured(
        self,
    ) -> None:
        """Catch-all ``BackupError`` must reach ``handle_backup_error``.

        Without the ``BackupError`` entry in ``EXCEPTION_HANDLERS``, any
        subtype not explicitly caught in a controller would fall through
        to ``handle_unexpected``, producing a generic 500 with no
        backup-specific ``error_code``.  This test pins the contract:
        the response must carry a structured envelope with
        ``ErrorCode.INTERNAL_ERROR`` and the scrubbed
        ``"Backup operation failed"`` detail.
        """

        @get("/test")
        async def handler() -> None:
            msg = "generic backup failure"
            raise BackupError(msg)

        async with LoopAsyncClient(make_exception_handler_app(handler)) as client:
            resp = await client.get("/test")
            assert resp.status_code == 500
            body = resp.json()
            assert body["error"] == "Backup operation failed"
            assert body["error_detail"]["error_code"] == ErrorCode.INTERNAL_ERROR

    @pytest.mark.parametrize(
        "exc_cls",
        [RetentionError, ComponentBackupError],
        ids=["retention_error", "component_backup_error"],
    )
    async def test_other_backup_subtypes_map_to_structured_500(
        self,
        exc_cls: type[BackupError],
    ) -> None:
        """``RetentionError``, ``ComponentBackupError``.

        Pin the contract that every non-special-cased ``BackupError``
        subtype routes through ``handle_backup_error``'s catch-all
        branch and produces a structured 5xx with ``INTERNAL_ERROR``.
        Adding an explicit branch for any of these in a future refactor
        must update this test. ``RestoreError`` and ``ManifestError``
        now carry distinct codes and have their own tests.
        """

        @get("/test")
        async def handler() -> None:
            msg = "subtype failure"
            raise exc_cls(msg)

        async with LoopAsyncClient(make_exception_handler_app(handler)) as client:
            resp = await client.get("/test")
            assert resp.status_code == 500
            body = resp.json()
            assert body["error"] == "Backup operation failed"
            _assert_error_detail(
                body,
                error_code=ErrorCode.INTERNAL_ERROR,
                error_category=ErrorCategory.INTERNAL,
                retryable=False,
            )

    async def test_restore_error_maps_to_structured_500_with_distinct_code(
        self,
    ) -> None:
        """``RestoreError`` carries the distinct ``BACKUP_RESTORE_FAILED``.

        5xx still scrubs the upstream message to the class default, but
        the distinct code lets clients/operators alert on restore
        failures specifically rather than a generic internal error.
        """

        @get("/test")
        async def handler() -> None:
            msg = "restore subtype failure"
            raise RestoreError(msg)

        async with LoopAsyncClient(make_exception_handler_app(handler)) as client:
            resp = await client.get("/test")
            assert resp.status_code == 500
            body = resp.json()
            assert body["error"] == "Restore operation failed"
            _assert_error_detail(
                body,
                error_code=ErrorCode.BACKUP_RESTORE_FAILED,
                error_category=ErrorCategory.INTERNAL,
                retryable=False,
            )

    @pytest.mark.parametrize(
        ("exc_cls", "status_code", "expected_detail"),
        [
            (BackupNotFoundError, 404, "Backup not found"),
            (BackupInProgressError, 409, "Backup operation already in progress"),
        ],
        ids=["not_found_empty_msg", "in_progress_empty_msg"],
    )
    async def test_backup_error_4xx_uses_fallback_when_message_empty(
        self,
        exc_cls: type[BackupError],
        status_code: int,
        expected_detail: str,
    ) -> None:
        """Empty exception messages must trigger the 4xx fallback strings.

        ``handle_backup_error`` uses ``str(exc) or "<fallback>"`` so a
        ``BackupNotFoundError("")`` does not propagate an empty
        ``detail`` field to the client.  Without coverage, a refactor
        could drop the ``or "<fallback>"`` guard silently.
        """

        @get("/test")
        async def handler() -> None:
            msg = ""
            raise exc_cls(msg)

        async with LoopAsyncClient(make_exception_handler_app(handler)) as client:
            resp = await client.get("/test")
            assert resp.status_code == status_code
            body = resp.json()
            assert body["error"] == expected_detail

    async def test_api_not_found_error_maps_to_404(self) -> None:
        @get("/test")
        async def handler() -> None:
            msg = "nope"
            raise NotFoundError(msg)

        async with LoopAsyncClient(make_exception_handler_app(handler)) as client:
            resp = await client.get("/test")
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

    async def test_api_conflict_error_maps_to_409(self) -> None:
        @get("/test")
        async def handler() -> None:
            msg = "conflict"
            raise ConflictError(msg)

        async with LoopAsyncClient(make_exception_handler_app(handler)) as client:
            resp = await client.get("/test")
            assert resp.status_code == 409
            body = resp.json()
            assert body["error"] == "conflict"
            _assert_error_detail(
                body,
                error_code=ErrorCode.RESOURCE_CONFLICT,
                error_category=ErrorCategory.CONFLICT,
                retryable=False,
            )

    async def test_api_forbidden_error_maps_to_403(self) -> None:
        @get("/test")
        async def handler() -> None:
            msg = "denied"
            raise ForbiddenError(msg)

        async with LoopAsyncClient(make_exception_handler_app(handler)) as client:
            resp = await client.get("/test")
            assert resp.status_code == 403
            body = resp.json()
            assert body["error"] == "denied"
            _assert_error_detail(
                body,
                error_code=ErrorCode.FORBIDDEN,
                error_category=ErrorCategory.AUTH,
                retryable=False,
            )

    async def test_value_error_falls_through_to_catch_all(self) -> None:
        @get("/test")
        async def handler() -> None:
            msg = "bad input"
            raise ValueError(msg)

        async with LoopAsyncClient(make_exception_handler_app(handler)) as client:
            resp = await client.get("/test")
            assert resp.status_code == 500
            body = resp.json()
            _assert_error_detail(
                body,
                error_code=ErrorCode.INTERNAL_ERROR,
                error_category=ErrorCategory.INTERNAL,
                retryable=False,
            )

    async def test_unexpected_error_maps_to_500(self) -> None:
        @get("/test")
        async def handler() -> None:
            msg = "boom"
            raise RuntimeError(msg)

        async with LoopAsyncClient(make_exception_handler_app(handler)) as client:
            resp = await client.get("/test")
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

    async def test_unauthorized_error_maps_to_401(self) -> None:
        @get("/test")
        async def handler() -> None:
            msg = "Invalid credentials"
            raise UnauthorizedError(msg)

        async with LoopAsyncClient(make_exception_handler_app(handler)) as client:
            resp = await client.get("/test")
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

    async def test_validation_error_maps_to_422(self) -> None:
        @get("/test")
        async def handler() -> None:
            msg = "Bad field"
            raise ValidationError(msg)

        async with LoopAsyncClient(make_exception_handler_app(handler)) as client:
            resp = await client.get("/test")
            assert resp.status_code == 422
            body = resp.json()
            assert body["error"] == "Bad field"
            _assert_error_detail(
                body,
                error_code=ErrorCode.VALIDATION_ERROR,
                error_category=ErrorCategory.VALIDATION,
                retryable=False,
            )

    async def test_unmatched_route_returns_404(self) -> None:
        """NotFoundException for unknown routes returns 404, not 500."""

        @get("/test")
        async def handler() -> str:
            return "ok"

        async with LoopAsyncClient(make_exception_handler_app(handler)) as client:
            resp = await client.get("/nonexistent")
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

    async def test_litestar_permission_denied_maps_to_403(self) -> None:
        """PermissionDeniedException with no detail falls back to 'Forbidden'."""

        @get("/test")
        async def handler() -> None:
            raise PermissionDeniedException

        async with LoopAsyncClient(make_exception_handler_app(handler)) as client:
            resp = await client.get("/test")
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

    async def test_permission_denied_scrubs_custom_detail(self) -> None:
        """PermissionDeniedException always returns fixed 'Forbidden' message."""

        @get("/test")
        async def handler() -> None:
            raise PermissionDeniedException(detail="Write access denied")

        async with LoopAsyncClient(make_exception_handler_app(handler)) as client:
            resp = await client.get("/test")
            assert resp.status_code == 403
            body = resp.json()
            assert body["error"] == "Forbidden"
            _assert_error_detail(
                body,
                error_code=ErrorCode.FORBIDDEN,
                error_category=ErrorCategory.AUTH,
                retryable=False,
            )

    async def test_litestar_not_authorized_maps_to_401(self) -> None:
        """NotAuthorizedException with default detail returns 401."""

        @get("/test")
        async def handler() -> None:
            raise NotAuthorizedException

        async with LoopAsyncClient(make_exception_handler_app(handler)) as client:
            resp = await client.get("/test")
            assert resp.status_code == 401
            body = resp.json()
            assert body["success"] is False
            # Unrecognised detail (Litestar's default) falls through to
            # the generic UNAUTHORIZED code; the dashboard treats this
            # as "show login, no toast".
            assert body["error"] == "Authentication required"
            _assert_error_detail(
                body,
                error_code=ErrorCode.UNAUTHORIZED,
                error_category=ErrorCategory.AUTH,
                retryable=False,
            )

    async def test_missing_authentication_maps_to_session_no_token(self) -> None:
        """Missing-cookie path emits SESSION_NO_TOKEN.

        The dashboard treats this as a fresh page load with no
        session yet -- silent redirect to /login, no "you were
        signed out" toast (which would be a false positive).
        """

        @get("/test")
        async def handler() -> None:
            raise NotAuthorizedException(detail="Missing authentication")

        async with LoopAsyncClient(make_exception_handler_app(handler)) as client:
            resp = await client.get("/test")
            assert resp.status_code == 401
            body = resp.json()
            assert body["error"] == "Authentication required"
            _assert_error_detail(
                body,
                error_code=ErrorCode.SESSION_NO_TOKEN,
                error_category=ErrorCategory.AUTH,
                retryable=False,
            )

    async def test_invalid_session_cookie_maps_to_session_expired(self) -> None:
        """Cookie-present-but-invalid path emits SESSION_EXPIRED.

        The dashboard treats this as a session that lapsed --
        redirect plus "Session expired. Please log in again."
        """

        @get("/test")
        async def handler() -> None:
            raise NotAuthorizedException(detail="Invalid session cookie")

        async with LoopAsyncClient(make_exception_handler_app(handler)) as client:
            resp = await client.get("/test")
            assert resp.status_code == 401
            body = resp.json()
            assert body["error"] == "Session expired. Please log in again."
            _assert_error_detail(
                body,
                error_code=ErrorCode.SESSION_EXPIRED,
                error_category=ErrorCategory.AUTH,
                retryable=False,
            )

    async def test_invalid_jwt_token_also_maps_to_session_expired(self) -> None:
        """Bearer-token-invalid path is treated the same as expired cookie."""

        @get("/test")
        async def handler() -> None:
            raise NotAuthorizedException(detail="Invalid JWT token")

        async with LoopAsyncClient(make_exception_handler_app(handler)) as client:
            resp = await client.get("/test")
            assert resp.status_code == 401
            body = resp.json()
            assert "expired" in body["error"].lower()
            _assert_error_detail(
                body,
                error_code=ErrorCode.SESSION_EXPIRED,
                error_category=ErrorCategory.AUTH,
                retryable=False,
            )

    async def test_not_authorized_unknown_detail_falls_through_to_unauthorized(
        self,
    ) -> None:
        """Unrecognised detail strings get the generic UNAUTHORIZED code."""

        @get("/test")
        async def handler() -> None:
            raise NotAuthorizedException(detail="Custom: something else")

        async with LoopAsyncClient(make_exception_handler_app(handler)) as client:
            resp = await client.get("/test")
            assert resp.status_code == 401
            body = resp.json()
            assert body["error"] == "Authentication required"
            _assert_error_detail(
                body,
                error_code=ErrorCode.UNAUTHORIZED,
                error_category=ErrorCategory.AUTH,
                retryable=False,
            )

    async def test_litestar_validation_exception_maps_to_400(self) -> None:
        """Litestar ValidationException forwards its detail."""

        @get("/test")
        async def handler() -> None:
            raise ValidationException

        async with LoopAsyncClient(make_exception_handler_app(handler)) as client:
            resp = await client.get("/test")
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

    async def test_method_not_allowed_maps_to_405(self) -> None:
        """Router-level MethodNotAllowed returns 405 via HTTPException handler."""

        @post("/test")
        async def handler() -> str:
            return "ok"

        async with LoopAsyncClient(make_exception_handler_app(handler)) as client:
            resp = await client.get("/test")
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

    async def test_http_exception_5xx_returns_scrubbed_message(self) -> None:
        """5xx HTTPException scrubs detail to prevent info leakage."""

        @get("/test")
        async def handler() -> None:
            raise HTTPException(
                status_code=502,
                detail="upstream db connection refused",
            )

        async with LoopAsyncClient(make_exception_handler_app(handler)) as client:
            resp = await client.get("/test")
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

    async def test_http_exception_empty_detail_uses_phrase(self) -> None:
        """HTTPException with empty detail falls back to HTTP phrase."""

        @get("/test")
        async def handler() -> None:
            raise HTTPException(status_code=429)

        async with LoopAsyncClient(make_exception_handler_app(handler)) as client:
            resp = await client.get("/test")
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

    def test_http_exception_malformed_retry_after_logs_warning(self) -> None:
        """A non-integer Retry-After header logs a warning, falls back to None.

        ``contextlib.suppress`` previously swallowed this silently.  The
        new explicit try/except emits ``api.request.error`` with a
        ``retry_after_parse_error`` marker so a misbehaving upstream
        is observable in operator logs.
        """
        exc = MagicMock(spec=HTTPException)
        exc.status_code = 429
        exc.detail = "Slow down"
        exc.headers = {"Retry-After": "soon"}

        request = MagicMock()
        request.method = "GET"
        request.url.path = "/test"
        request.accept.best_match.return_value = "application/json"

        with structlog.testing.capture_logs() as logs:
            resp = handle_http_exception(request, exc)

        assert resp.status_code == 429
        assert resp.content.error_detail is not None  # type: ignore[union-attr]
        assert resp.content.error_detail.retry_after is None  # type: ignore[union-attr]

        parse_warnings = [
            log
            for log in logs
            if log.get("event") == "api.request.error"
            and log.get("error_type") == "retry_after_parse_error"
        ]
        assert len(parse_warnings) == 1
        assert parse_warnings[0]["raw_retry_after"] == "soon"

    def test_http_exception_non_string_detail_is_coerced(self) -> None:
        """``exc.detail`` set to bytes (or any non-string) is coerced to str.

        Litestar types ``detail`` as ``str | None`` but third-party
        ``HTTPException`` subclasses occasionally set it to bytes.  The
        slice ``[:_MAX_DETAIL_LEN]`` would otherwise yield a bytes
        result that the response builder cannot serialise as JSON.
        """
        exc = MagicMock(spec=HTTPException)
        exc.status_code = 400
        exc.detail = b"bytes detail"
        exc.headers = None

        request = MagicMock()
        request.method = "GET"
        request.url.path = "/test"
        request.accept.best_match.return_value = "application/json"

        resp = handle_http_exception(request, exc)
        assert resp.status_code == 400
        assert isinstance(resp.content.error, str)  # type: ignore[union-attr]
        assert "bytes detail" in resp.content.error  # type: ignore[union-attr]


class TestNormalizeStatusCode:
    """``_normalize_status_code`` warning path validation tests."""

    def test_invalid_type_logs_warning(self) -> None:
        """Non-numeric, non-string types (e.g. ``object``) emit a warning."""
        from synthorg.api.exception_handlers import _normalize_status_code

        with structlog.testing.capture_logs() as logs:
            assert _normalize_status_code(object()) == 500
        warnings = [
            log for log in logs if log.get("error_type") == "status_code_invalid_type"
        ]
        assert len(warnings) == 1

    def test_bool_logs_warning(self) -> None:
        """``bool`` is rejected to avoid ``True`` coercing to ``1``."""
        from synthorg.api.exception_handlers import _normalize_status_code

        with structlog.testing.capture_logs() as logs:
            assert _normalize_status_code(True) == 500
        assert any(log.get("error_type") == "status_code_invalid_type" for log in logs)

    def test_unparseable_string_logs_warning(self) -> None:
        """A string that ``int()`` cannot parse logs the coercion failure."""
        from synthorg.api.exception_handlers import _normalize_status_code

        with structlog.testing.capture_logs() as logs:
            assert _normalize_status_code("not-a-number") == 500
        warnings = [
            log
            for log in logs
            if log.get("error_type") == "status_code_coercion_failed"
        ]
        assert len(warnings) == 1

    def test_out_of_range_logs_warning(self) -> None:
        """A 200- or 600-range value is logged as out-of-range."""
        from synthorg.api.exception_handlers import _normalize_status_code

        with structlog.testing.capture_logs() as logs:
            assert _normalize_status_code(200) == 500
            assert _normalize_status_code(600) == 500
        out_of_range = [
            log for log in logs if log.get("error_type") == "status_code_out_of_range"
        ]
        assert len(out_of_range) == 2

    def test_valid_4xx_passes_through(self) -> None:
        """Valid 4xx/5xx values return unchanged with no warnings."""
        from synthorg.api.exception_handlers import _normalize_status_code

        with structlog.testing.capture_logs() as logs:
            assert _normalize_status_code(404) == 404
            assert _normalize_status_code("503") == 503
        assert not [log for log in logs if log.get("event") == "api.request.error"]


class TestStructuredErrorMetadata:
    """Tests specifically for RFC 9457 structured error features."""

    async def test_service_unavailable_is_retryable(self) -> None:
        @get("/test")
        async def handler() -> None:
            raise ServiceUnavailableError

        async with LoopAsyncClient(make_exception_handler_app(handler)) as client:
            resp = await client.get("/test")
            assert resp.status_code == 503
            body = resp.json()
            assert body["error"] == "Service unavailable"
            _assert_error_detail(
                body,
                error_code=ErrorCode.SERVICE_UNAVAILABLE,
                error_category=ErrorCategory.INTERNAL,
                retryable=True,
            )

    async def test_http_429_is_retryable(self) -> None:
        @get("/test")
        async def handler() -> None:
            raise HTTPException(status_code=429, detail="Slow down")

        async with LoopAsyncClient(make_exception_handler_app(handler)) as client:
            resp = await client.get("/test")
            assert resp.status_code == 429
            body = resp.json()
            _assert_error_detail(
                body,
                error_code=ErrorCode.RATE_LIMITED,
                error_category=ErrorCategory.RATE_LIMIT,
                retryable=True,
            )

    async def test_http_429_retry_after_header_propagated_to_body(self) -> None:
        """Retry-After header value is parsed into the body field."""

        @get("/test")
        async def handler() -> None:
            raise HTTPException(
                status_code=429,
                detail="Slow down",
                headers={"Retry-After": "60"},
            )

        async with LoopAsyncClient(make_exception_handler_app(handler)) as client:
            resp = await client.get("/test")
            assert resp.status_code == 429
            body = resp.json()
            assert resp.headers.get("retry-after") == "60"
            assert body["error_detail"]["retry_after"] == 60
            assert body["error_detail"]["retryable"] is True

    async def test_http_503_is_retryable(self) -> None:
        @get("/test")
        async def handler() -> None:
            raise HTTPException(status_code=503)

        async with LoopAsyncClient(make_exception_handler_app(handler)) as client:
            resp = await client.get("/test")
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

    async def test_error_detail_detail_matches_error_field(self) -> None:
        """error_detail.detail must match the top-level error field."""

        @get("/test")
        async def handler() -> None:
            msg = "custom not found"
            raise NotFoundError(msg)

        async with LoopAsyncClient(make_exception_handler_app(handler)) as client:
            resp = await client.get("/test")
            body = resp.json()
            assert body["error_detail"]["detail"] == body["error"]

    async def test_error_detail_has_title_and_type(self) -> None:
        """error_detail includes RFC 9457 title and type fields."""

        @get("/test")
        async def handler() -> None:
            msg = "gone"
            raise NotFoundError(msg)

        async with LoopAsyncClient(make_exception_handler_app(handler)) as client:
            resp = await client.get("/test")
            body = resp.json()
            ed = body["error_detail"]
            assert ed["title"] == category_title(ErrorCategory.NOT_FOUND)
            assert ed["type"] == category_type_uri(ErrorCategory.NOT_FOUND)

    async def test_retry_after_is_none_for_non_rate_limit(self) -> None:
        """retry_after should be None for non-rate-limit errors."""

        @get("/test")
        async def handler() -> None:
            msg = "dup"
            raise ConflictError(msg)

        async with LoopAsyncClient(make_exception_handler_app(handler)) as client:
            resp = await client.get("/test")
            body = resp.json()
            assert body["error_detail"]["retry_after"] is None

    async def test_5xx_scrubs_custom_message(self) -> None:
        """ServiceUnavailableError with custom message returns default."""

        @get("/test")
        async def handler() -> None:
            msg = "Connection pool exhausted: 10.0.0.5:5432"
            raise ServiceUnavailableError(msg)

        async with LoopAsyncClient(make_exception_handler_app(handler)) as client:
            resp = await client.get("/test")
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


class TestDomainErrorInstantiation:
    """Tests for DomainError subclass instantiation behavior."""

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
        cls: type[DomainError],
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

    def test_fallback_preserves_envelope_shape_for_application_json(
        self,
    ) -> None:
        """Default JSON clients keep the ``ApiResponse`` envelope shape.

        The fallback path must respect content negotiation: a client
        sending ``Accept: application/json`` (the dashboard, every SDK)
        expects ``error`` + ``error_detail`` keys.  Returning a bare
        ``ProblemDetail`` instead would silently drop the envelope
        contract on the very degraded path the fallback is designed to
        cover.
        """
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
        envelope = resp.content
        # JSON clients receive the standard envelope.
        assert hasattr(envelope, "error")
        assert hasattr(envelope, "error_detail")
        assert envelope.error == "Internal server error"
        assert envelope.error_detail is not None
        assert envelope.error_detail.error_code == ErrorCode.INTERNAL_ERROR
        assert envelope.error_detail.error_category == ErrorCategory.INTERNAL
        assert envelope.error_detail.retryable is False
        assert isinstance(envelope.error_detail.instance, str)
        assert envelope.error_detail.instance

    def test_fallback_returns_problem_detail_for_problem_json(self) -> None:
        """``Accept: application/problem+json`` keeps the bare RFC 9457 body."""
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
        assert isinstance(resp.content, ProblemDetail)
        assert resp.content.status == 500
        assert resp.content.detail == "Internal server error"
        assert resp.content.error_code == ErrorCode.INTERNAL_ERROR
        assert resp.content.error_category == ErrorCategory.INTERNAL
        assert resp.content.retryable is False

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


# ── Domain error base class mappings ─────────────────────────────


class TestDomainErrorMapping:
    """Every domain error base and its key subclasses produce RFC 9457.

    Every domain error base class must be registered in
    EXCEPTION_HANDLERS; an unregistered base that escapes a controller
    falls through to ``handle_unexpected`` (500, INTERNAL_ERROR).  Each base declares
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
    async def test_domain_error_base_maps_to_rfc_9457(
        self,
        exc_factory: Callable[[], Exception],
        expected_status: int,
        expected_code: int,
        expected_category: str,
        expected_retryable: bool,
    ) -> None:
        @get("/test")
        async def handler() -> None:
            raise exc_factory()

        async with LoopAsyncClient(make_exception_handler_app(handler)) as client:
            resp = await client.get("/test")
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

    async def test_provider_rate_limit_surfaces_retry_after(self) -> None:
        """``RateLimitError`` produces 429 with ``Retry-After`` header."""
        from synthorg.providers.errors import RateLimitError

        msg = "throttled"

        @get("/test")
        async def handler() -> None:
            raise RateLimitError(msg, retry_after=42.0)

        async with LoopAsyncClient(make_exception_handler_app(handler)) as client:
            resp = await client.get("/test")
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

    async def test_retryable_provider_timeout_flag_is_set(self) -> None:
        """Retryable provider errors surface ``retryable: True``."""
        from synthorg.providers.errors import ProviderTimeoutError

        msg = "timed out"

        @get("/test")
        async def handler() -> None:
            raise ProviderTimeoutError(msg)

        async with LoopAsyncClient(make_exception_handler_app(handler)) as client:
            resp = await client.get("/test")
            assert resp.status_code == 504
            body = resp.json()
            assert body["error_detail"]["retryable"] is True


# ── Bare-Response fix tests ──────────────────────────────────────


class TestBareResponseFixes:
    """Controllers no longer return bare ``Response`` for error paths.

    Error paths in ``artifacts.py``, ``subworkflows.py``, and
    ``projects.py`` must not return plain ``Response(content=ApiResponse(
    error="..."))`` objects that bypass the RFC 9457 handler
    registration.  Each site raises a typed ``ApiError`` subclass
    (or lets a domain error with ClassVar metadata propagate) so the
    central handlers produce a structured response with the correct
    ``error_detail`` envelope.
    """

    async def test_artifact_too_large_produces_rfc_9457_413(self) -> None:
        """Artifact upload over the size limit returns 413 + error_detail.

        The persistence-layer ``ArtifactTooLargeError`` is registered with
        ``handle_domain_error`` above the generic ``PersistenceError`` 500
        handler, so its 413 status + ``ARTIFACT_TOO_LARGE`` code reach the wire.
        """
        from synthorg.core.persistence_errors import ArtifactTooLargeError

        @post("/upload")
        async def handler() -> None:
            raise ArtifactTooLargeError

        async with LoopAsyncClient(make_exception_handler_app(handler)) as client:
            resp = await client.post("/upload")
            assert resp.status_code == 413
            body = resp.json()
            assert body["success"] is False
            _assert_error_detail(
                body,
                error_code=ErrorCode.ARTIFACT_TOO_LARGE,
                error_category=ErrorCategory.VALIDATION,
                retryable=False,
            )

    async def test_artifact_storage_full_produces_rfc_9457_507(self) -> None:
        """Storage-full rejection returns 507 with structured detail.

        Same registration pattern as the too-large case: the
        ``ArtifactStorageFullError`` 507 + ``ARTIFACT_STORAGE_FULL`` code
        win over the generic ``PersistenceError`` 500 mapping.
        """
        from synthorg.core.persistence_errors import ArtifactStorageFullError

        @post("/upload")
        async def handler() -> None:
            raise ArtifactStorageFullError

        async with LoopAsyncClient(make_exception_handler_app(handler)) as client:
            resp = await client.post("/upload")
            assert resp.status_code == 507
            body = resp.json()
            assert body["success"] is False
            _assert_error_detail(
                body,
                error_code=ErrorCode.ARTIFACT_STORAGE_FULL,
                error_category=ErrorCategory.INTERNAL,
                retryable=False,
            )

    async def test_subworkflow_not_found_produces_rfc_9457_404(self) -> None:
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

        async with LoopAsyncClient(make_exception_handler_app(handler)) as client:
            resp = await client.get("/sub")
            assert resp.status_code == 404
            body = resp.json()
            assert body["success"] is False
            _assert_error_detail(
                body,
                error_code=ErrorCode.SUBWORKFLOW_NOT_FOUND,
                error_category=ErrorCategory.NOT_FOUND,
                retryable=False,
            )

    async def test_invalid_project_status_produces_rfc_9457_422(self) -> None:
        """Invalid project status filter raises ValidationError (422)."""

        @get("/projects")
        async def handler() -> None:
            msg = "Invalid project status: 'bogus'. Valid values: active"
            raise ValidationError(msg)

        async with LoopAsyncClient(make_exception_handler_app(handler)) as client:
            resp = await client.get("/projects")
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

    async def test_project_not_found_produces_rfc_9457_404(self) -> None:
        """Missing project raises NotFoundError (404)."""

        @get("/projects/{project_id:str}")
        async def handler(project_id: Annotated[str, PathParameter()]) -> None:
            msg = f"Project {project_id!r} not found"
            raise NotFoundError(msg)

        async with LoopAsyncClient(make_exception_handler_app(handler)) as client:
            resp = await client.get("/projects/missing")
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


class TestDomainErrorFamilyClassVarHttpMapping:
    """End-to-end ClassVar -> HTTP mapping for DomainError-rooted families.

    Catches typos in ``status_code`` / ``error_code`` / ``error_category``
    on any class in the domain-error tree. Without these, a refactor
    that flips ``status_code = 404`` to ``500`` on (for example)
    ``AgentNotFoundError`` would silently ship: the AST gate only
    checks that the class roots in ``DomainError``, not that its HTTP
    metadata is sensible.
    """

    @pytest.mark.parametrize(
        ("import_path", "exc_name", "status_code", "error_code", "error_category"),
        [
            (
                "synthorg.hr.errors",
                "AgentNotFoundError",
                404,
                ErrorCode.AGENT_NOT_FOUND,
                ErrorCategory.NOT_FOUND,
            ),
            (
                "synthorg.hr.errors",
                "AgentAlreadyRegisteredError",
                409,
                ErrorCode.AGENT_ALREADY_REGISTERED,
                ErrorCategory.CONFLICT,
            ),
            (
                "synthorg.hr.errors",
                "InvalidCandidateError",
                422,
                ErrorCode.VALIDATION_ERROR,
                ErrorCategory.VALIDATION,
            ),
            (
                "synthorg.hr.errors",
                "PromotionCooldownError",
                409,
                ErrorCode.PROMOTION_COOLDOWN_ACTIVE,
                ErrorCategory.CONFLICT,
            ),
            (
                "synthorg.hr.errors",
                "HRError",
                500,
                ErrorCode.INTERNAL_ERROR,
                ErrorCategory.INTERNAL,
            ),
            (
                "synthorg.hr.scaling.errors",
                "ScalingCooldownActiveError",
                409,
                ErrorCode.SCALING_COOLDOWN_ACTIVE,
                ErrorCategory.CONFLICT,
            ),
            (
                "synthorg.hr.scaling.errors",
                "ScalingError",
                500,
                ErrorCode.INTERNAL_ERROR,
                ErrorCategory.INTERNAL,
            ),
            (
                "synthorg.security.trust.errors",
                "TrustError",
                500,
                ErrorCode.INTERNAL_ERROR,
                ErrorCategory.INTERNAL,
            ),
            (
                "synthorg.settings.errors",
                "SettingNotFoundError",
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                ErrorCategory.NOT_FOUND,
            ),
            (
                "synthorg.settings.errors",
                "SettingValidationError",
                422,
                ErrorCode.VALIDATION_ERROR,
                ErrorCategory.VALIDATION,
            ),
            (
                "synthorg.settings.errors",
                "SettingsError",
                500,
                ErrorCode.INTERNAL_ERROR,
                ErrorCategory.INTERNAL,
            ),
            (
                "synthorg.config.errors",
                "ConfigError",
                500,
                ErrorCode.INTERNAL_ERROR,
                ErrorCategory.INTERNAL,
            ),
            (
                "synthorg.core.registry.errors",
                "StrategyFactoryNotFoundError",
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                ErrorCategory.NOT_FOUND,
            ),
            (
                "synthorg.core.registry.errors",
                "StrategyFactoryError",
                500,
                ErrorCode.INTERNAL_ERROR,
                ErrorCategory.INTERNAL,
            ),
        ],
    )
    async def test_migrated_class_classvars_drive_response(
        self,
        import_path: str,
        exc_name: str,
        status_code: int,
        error_code: int,
        error_category: ErrorCategory,
    ) -> None:
        """Each class's ClassVars produce the right HTTP envelope."""
        import importlib

        module = importlib.import_module(import_path)
        exc_cls: type[Exception] = getattr(module, exc_name)

        # Most DomainError subclasses accept ``()`` because the base
        # ``__init__`` defaults ``message=None``; the fallback path
        # below keeps the metadata-routing assertion alive even if a
        # class ever requires a positional message argument.
        @get("/test")
        async def handler() -> None:
            try:
                raise exc_cls
            except TypeError:
                # Class needs an arg; pass an empty marker so the test
                # still verifies metadata routing.
                msg = ""
                raise exc_cls(msg)  # noqa: B904 -- intentional re-raise

        async with LoopAsyncClient(make_exception_handler_app(handler)) as client:
            resp = await client.get("/test")
            assert resp.status_code == status_code, (
                f"{exc_name}: expected {status_code}, got {resp.status_code}"
            )
            body = resp.json()
            assert body["success"] is False
            _assert_error_detail(
                body,
                error_code=error_code,
                error_category=error_category,
                retryable=False,
            )
