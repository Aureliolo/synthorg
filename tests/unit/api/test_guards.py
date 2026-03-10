"""Tests for route guards with JWT-based authentication."""

from typing import Any

import pytest
from litestar.testing import TestClient  # noqa: TC002

from tests.unit.api.conftest import make_auth_headers

# To test "no auth" we need a fresh client without default headers.
# The test_client fixture sets CEO headers. Passing headers={} to
# a request merges with session defaults — it does NOT clear them.
# Instead we create a bare_client fixture.


@pytest.fixture
def bare_client(test_client: TestClient[Any]) -> TestClient[Any]:
    """Test client with no default Authorization header."""
    test_client.headers.pop("authorization", None)
    return test_client


@pytest.mark.unit
class TestWriteGuard:
    def test_allows_ceo(self, test_client: TestClient[Any]) -> None:
        response = test_client.post(
            "/api/v1/tasks",
            json={
                "title": "Test",
                "description": "Test desc",
                "type": "development",
                "project": "proj",
                "created_by": "alice",
            },
            headers=make_auth_headers("ceo"),
        )
        assert response.status_code == 201

    def test_allows_manager(self, test_client: TestClient[Any]) -> None:
        response = test_client.post(
            "/api/v1/tasks",
            json={
                "title": "Test",
                "description": "Test desc",
                "type": "development",
                "project": "proj",
                "created_by": "alice",
            },
            headers=make_auth_headers("manager"),
        )
        assert response.status_code == 201

    def test_allows_board_member(self, test_client: TestClient[Any]) -> None:
        response = test_client.post(
            "/api/v1/tasks",
            json={
                "title": "Test",
                "description": "Test desc",
                "type": "development",
                "project": "proj",
                "created_by": "alice",
            },
            headers=make_auth_headers("board_member"),
        )
        assert response.status_code == 201

    def test_allows_pair_programmer(self, test_client: TestClient[Any]) -> None:
        response = test_client.post(
            "/api/v1/tasks",
            json={
                "title": "Test",
                "description": "Test desc",
                "type": "development",
                "project": "proj",
                "created_by": "alice",
            },
            headers=make_auth_headers("pair_programmer"),
        )
        assert response.status_code == 201

    def test_blocks_observer(self, test_client: TestClient[Any]) -> None:
        response = test_client.post(
            "/api/v1/tasks",
            json={
                "title": "Test",
                "description": "Test desc",
                "type": "development",
                "project": "proj",
                "created_by": "alice",
            },
            headers=make_auth_headers("observer"),
        )
        assert response.status_code == 403

    def test_missing_auth_returns_401(self, bare_client: TestClient[Any]) -> None:
        response = bare_client.post(
            "/api/v1/tasks",
            json={
                "title": "Test",
                "description": "Test desc",
                "type": "development",
                "project": "proj",
                "created_by": "alice",
            },
        )
        assert response.status_code == 401

    def test_invalid_token_returns_401(self, test_client: TestClient[Any]) -> None:
        response = test_client.post(
            "/api/v1/tasks",
            json={
                "title": "Test",
                "description": "Test desc",
                "type": "development",
                "project": "proj",
                "created_by": "alice",
            },
            headers={"Authorization": "Bearer invalid-token"},
        )
        assert response.status_code == 401


@pytest.mark.unit
class TestReadGuard:
    def test_allows_observer(self, test_client: TestClient[Any]) -> None:
        response = test_client.get(
            "/api/v1/tasks",
            headers=make_auth_headers("observer"),
        )
        assert response.status_code == 200

    def test_allows_ceo(self, test_client: TestClient[Any]) -> None:
        response = test_client.get(
            "/api/v1/tasks",
            headers=make_auth_headers("ceo"),
        )
        assert response.status_code == 200

    def test_missing_auth_returns_401(self, bare_client: TestClient[Any]) -> None:
        response = bare_client.get("/api/v1/tasks")
        assert response.status_code == 401

    def test_invalid_token_returns_401(self, test_client: TestClient[Any]) -> None:
        response = test_client.get(
            "/api/v1/tasks",
            headers={"Authorization": "Bearer invalid-token"},
        )
        assert response.status_code == 401
