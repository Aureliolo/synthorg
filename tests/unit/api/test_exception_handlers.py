"""Tests for exception handlers."""

from typing import Any

import pytest
from litestar import Litestar, get
from litestar.testing import TestClient

from ai_company.api.errors import (
    ApiValidationError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    UnauthorizedError,
)
from ai_company.api.exception_handlers import EXCEPTION_HANDLERS
from ai_company.persistence.errors import (
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
