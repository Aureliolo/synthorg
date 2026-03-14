"""Tests for exception handlers."""

from typing import Any

import pytest
from litestar import Litestar, get, post
from litestar.exceptions import (
    HTTPException,
    NotAuthorizedException,
    PermissionDeniedException,
    ValidationException,
)
from litestar.testing import TestClient

from synthorg.api.errors import (
    ApiValidationError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    UnauthorizedError,
)
from synthorg.api.exception_handlers import EXCEPTION_HANDLERS
from synthorg.persistence.errors import (
    DuplicateRecordError,
    PersistenceError,
    RecordNotFoundError,
)


def _make_app(handler: Any) -> Litestar:
    return Litestar(
        route_handlers=[handler],
        exception_handlers=EXCEPTION_HANDLERS,  # type: ignore[arg-type]
    )


@pytest.mark.unit
class TestExceptionHandlers:
    def test_record_not_found_maps_to_404(self) -> None:
        @get("/test")
        async def handler() -> None:
            msg = "gone"
            raise RecordNotFoundError(msg)

        with TestClient(_make_app(handler)) as client:
            resp = client.get("/test")
            assert resp.status_code == 404
            body = resp.json()
            assert body["success"] is False
            # Error message is scrubbed — internal details not exposed.
            assert body["error"] == "Resource not found"

    def test_duplicate_record_maps_to_409(self) -> None:
        @get("/test")
        async def handler() -> None:
            msg = "exists"
            raise DuplicateRecordError(msg)

        with TestClient(_make_app(handler)) as client:
            resp = client.get("/test")
            assert resp.status_code == 409
            body = resp.json()
            assert body["error"] == "Resource already exists"

    def test_persistence_error_maps_to_500(self) -> None:
        @get("/test")
        async def handler() -> None:
            msg = "db fail"
            raise PersistenceError(msg)

        with TestClient(_make_app(handler)) as client:
            resp = client.get("/test")
            assert resp.status_code == 500
            body = resp.json()
            assert body["success"] is False
            assert body["error"] == "Internal persistence error"

    def test_api_not_found_error_maps_to_404(self) -> None:
        @get("/test")
        async def handler() -> None:
            msg = "nope"
            raise NotFoundError(msg)

        with TestClient(_make_app(handler)) as client:
            resp = client.get("/test")
            assert resp.status_code == 404
            body = resp.json()
            # 4xx errors return the actual exception message
            assert body["error"] == "nope"

    def test_api_conflict_error_maps_to_409(self) -> None:
        @get("/test")
        async def handler() -> None:
            msg = "conflict"
            raise ConflictError(msg)

        with TestClient(_make_app(handler)) as client:
            resp = client.get("/test")
            assert resp.status_code == 409
            body = resp.json()
            assert body["error"] == "conflict"

    def test_api_forbidden_error_maps_to_403(self) -> None:
        @get("/test")
        async def handler() -> None:
            msg = "denied"
            raise ForbiddenError(msg)

        with TestClient(_make_app(handler)) as client:
            resp = client.get("/test")
            assert resp.status_code == 403
            body = resp.json()
            assert body["error"] == "denied"

    def test_value_error_falls_through_to_catch_all(self) -> None:
        @get("/test")
        async def handler() -> None:
            msg = "bad input"
            raise ValueError(msg)

        with TestClient(_make_app(handler)) as client:
            resp = client.get("/test")
            assert resp.status_code == 500

    def test_unexpected_error_maps_to_500(self) -> None:
        @get("/test")
        async def handler() -> None:
            msg = "boom"
            raise RuntimeError(msg)

        with TestClient(_make_app(handler)) as client:
            resp = client.get("/test")
            assert resp.status_code == 500
            body = resp.json()
            assert body["success"] is False
            assert body["error"] == "Internal server error"

    def test_unauthorized_error_maps_to_401(self) -> None:
        @get("/test")
        async def handler() -> None:
            msg = "Invalid credentials"
            raise UnauthorizedError(msg)

        with TestClient(_make_app(handler)) as client:
            resp = client.get("/test")
            assert resp.status_code == 401
            body = resp.json()
            # 4xx returns the actual message, not the generic default
            assert body["error"] == "Invalid credentials"

    def test_validation_error_maps_to_422(self) -> None:
        @get("/test")
        async def handler() -> None:
            msg = "Bad field"
            raise ApiValidationError(msg)

        with TestClient(_make_app(handler)) as client:
            resp = client.get("/test")
            assert resp.status_code == 422
            body = resp.json()
            assert body["error"] == "Bad field"

    def test_unmatched_route_returns_404(self) -> None:
        """NotFoundException for unknown routes returns 404, not 500."""

        @get("/test")
        async def handler() -> str:
            return "ok"

        with TestClient(_make_app(handler)) as client:
            resp = client.get("/nonexistent")
            assert resp.status_code == 404
            body = resp.json()
            assert body["success"] is False
            assert body["error"] == "Not found"

    def test_litestar_permission_denied_maps_to_403(self) -> None:
        """PermissionDeniedException with no detail falls back to 'Forbidden'."""

        @get("/test")
        async def handler() -> None:
            raise PermissionDeniedException

        with TestClient(_make_app(handler)) as client:
            resp = client.get("/test")
            assert resp.status_code == 403
            body = resp.json()
            assert body["success"] is False
            assert body["error"] == "Forbidden"

    def test_permission_denied_preserves_detail(self) -> None:
        """PermissionDeniedException with custom detail passes it through."""

        @get("/test")
        async def handler() -> None:
            raise PermissionDeniedException(detail="Write access denied")

        with TestClient(_make_app(handler)) as client:
            resp = client.get("/test")
            assert resp.status_code == 403
            body = resp.json()
            assert body["error"] == "Write access denied"

    def test_litestar_not_authorized_maps_to_401(self) -> None:
        """NotAuthorizedException with default detail returns 401."""

        @get("/test")
        async def handler() -> None:
            raise NotAuthorizedException

        with TestClient(_make_app(handler)) as client:
            resp = client.get("/test")
            assert resp.status_code == 401
            body = resp.json()
            assert body["success"] is False
            assert body["error"] == "Unauthorized"

    def test_not_authorized_preserves_detail(self) -> None:
        """NotAuthorizedException with custom detail passes it through."""

        @get("/test")
        async def handler() -> None:
            raise NotAuthorizedException(detail="Invalid JWT token")

        with TestClient(_make_app(handler)) as client:
            resp = client.get("/test")
            assert resp.status_code == 401
            body = resp.json()
            assert body["error"] == "Invalid JWT token"

    def test_litestar_validation_exception_maps_to_400(self) -> None:
        """Litestar ValidationException returns static 400."""

        @get("/test")
        async def handler() -> None:
            raise ValidationException

        with TestClient(_make_app(handler)) as client:
            resp = client.get("/test")
            assert resp.status_code == 400
            body = resp.json()
            assert body["success"] is False
            assert body["error"] == "Validation error"

    def test_method_not_allowed_maps_to_405(self) -> None:
        """Router-level MethodNotAllowed returns 405 via HTTPException handler."""

        @post("/test")
        async def handler() -> str:
            return "ok"

        with TestClient(_make_app(handler)) as client:
            resp = client.get("/test")
            assert resp.status_code == 405
            body = resp.json()
            assert body["success"] is False
            assert body["error"] == "Method Not Allowed"
            assert "POST" in resp.headers.get("allow", "")

    def test_http_exception_5xx_returns_scrubbed_message(self) -> None:
        """5xx HTTPException scrubs detail to prevent info leakage."""

        @get("/test")
        async def handler() -> None:
            raise HTTPException(
                status_code=502,
                detail="upstream db connection refused",
            )

        with TestClient(_make_app(handler)) as client:
            resp = client.get("/test")
            assert resp.status_code == 502
            body = resp.json()
            assert body["success"] is False
            assert body["error"] == "Internal server error"

    def test_http_exception_empty_detail_uses_phrase(self) -> None:
        """HTTPException with empty detail falls back to HTTP phrase."""

        @get("/test")
        async def handler() -> None:
            raise HTTPException(status_code=429)

        with TestClient(_make_app(handler)) as client:
            resp = client.get("/test")
            assert resp.status_code == 429
            body = resp.json()
            assert body["error"] == "Too Many Requests"

    def test_http_exception_nonstandard_status_uses_fallback(self) -> None:
        """Non-standard status code falls back to generic message."""
        from unittest.mock import MagicMock

        from synthorg.api.exception_handlers import handle_http_exception

        exc = MagicMock(spec=HTTPException)
        exc.status_code = 499
        exc.detail = ""
        exc.headers = None

        request = MagicMock()
        request.method = "GET"
        request.url.path = "/test"

        resp = handle_http_exception(request, exc)
        assert resp.status_code == 499
        assert resp.content.error == "Request error"
