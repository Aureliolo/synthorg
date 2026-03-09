"""Tests for exception handlers."""

from typing import Any

import pytest
from litestar import Litestar, get
from litestar.testing import TestClient

from ai_company.api.errors import ConflictError, ForbiddenError, NotFoundError
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
            assert "gone" in body["error"]

    def test_duplicate_record_maps_to_409(self) -> None:
        @get("/test")
        async def handler() -> None:
            msg = "exists"
            raise DuplicateRecordError(msg)

        with TestClient(_make_app(handler)) as client:
            resp = client.get("/test")
            assert resp.status_code == 409

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

    def test_api_not_found_error_maps_to_404(self) -> None:
        @get("/test")
        async def handler() -> None:
            msg = "nope"
            raise NotFoundError(msg)

        with TestClient(_make_app(handler)) as client:
            resp = client.get("/test")
            assert resp.status_code == 404

    def test_api_conflict_error_maps_to_409(self) -> None:
        @get("/test")
        async def handler() -> None:
            msg = "conflict"
            raise ConflictError(msg)

        with TestClient(_make_app(handler)) as client:
            resp = client.get("/test")
            assert resp.status_code == 409

    def test_api_forbidden_error_maps_to_403(self) -> None:
        @get("/test")
        async def handler() -> None:
            msg = "denied"
            raise ForbiddenError(msg)

        with TestClient(_make_app(handler)) as client:
            resp = client.get("/test")
            assert resp.status_code == 403

    def test_value_error_maps_to_422(self) -> None:
        @get("/test")
        async def handler() -> None:
            msg = "bad input"
            raise ValueError(msg)

        with TestClient(_make_app(handler)) as client:
            resp = client.get("/test")
            assert resp.status_code == 422

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
