"""Tests for route guards with JWT-based authentication."""

from typing import Any

import pytest
from litestar.testing import TestClient

from tests.unit.api.conftest import make_auth_headers

# To test "no auth" we need a fresh client without default headers.
# The test_client fixture sets CEO headers. Passing headers={} to
# a request merges with session defaults -- it does NOT clear them.
# Instead we create a bare_client fixture.


@pytest.fixture
def bare_client(test_client: TestClient[Any]) -> TestClient[Any]:
    """Test client with no default Authorization header."""
    test_client.headers.pop("authorization", None)
    return test_client


# -- Task payload used across write-guard tests -------------------------

_TASK_PAYLOAD: dict[str, str] = {
    "title": "Test",
    "description": "Test desc",
    "type": "development",
    "project": "proj",
    "created_by": "alice",
}


@pytest.mark.unit
class TestWriteGuard:
    def test_allows_ceo(self, test_client: TestClient[Any]) -> None:
        response = test_client.post(
            "/api/v1/tasks",
            json=_TASK_PAYLOAD,
            headers=make_auth_headers("ceo"),
        )
        assert response.status_code == 201

    def test_allows_manager(self, test_client: TestClient[Any]) -> None:
        response = test_client.post(
            "/api/v1/tasks",
            json=_TASK_PAYLOAD,
            headers=make_auth_headers("manager"),
        )
        assert response.status_code == 201

    def test_blocks_board_member(self, test_client: TestClient[Any]) -> None:
        response = test_client.post(
            "/api/v1/tasks",
            json=_TASK_PAYLOAD,
            headers=make_auth_headers("board_member"),
        )
        assert response.status_code == 403

    def test_allows_pair_programmer(self, test_client: TestClient[Any]) -> None:
        response = test_client.post(
            "/api/v1/tasks",
            json=_TASK_PAYLOAD,
            headers=make_auth_headers("pair_programmer"),
        )
        assert response.status_code == 201

    def test_blocks_observer(self, test_client: TestClient[Any]) -> None:
        response = test_client.post(
            "/api/v1/tasks",
            json=_TASK_PAYLOAD,
            headers=make_auth_headers("observer"),
        )
        assert response.status_code == 403

    def test_missing_auth_returns_401(self, bare_client: TestClient[Any]) -> None:
        response = bare_client.post(
            "/api/v1/tasks",
            json=_TASK_PAYLOAD,
        )
        assert response.status_code == 401

    def test_invalid_token_returns_401(self, test_client: TestClient[Any]) -> None:
        response = test_client.post(
            "/api/v1/tasks",
            json=_TASK_PAYLOAD,
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

    def test_allows_board_member(self, test_client: TestClient[Any]) -> None:
        response = test_client.get(
            "/api/v1/tasks",
            headers=make_auth_headers("board_member"),
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


@pytest.mark.unit
class TestRequireRoles:
    """Tests for the require_roles() guard factory via live endpoints."""

    def test_setup_blocks_manager(
        self,
        test_client: TestClient[Any],
    ) -> None:
        # Setup write endpoints now require CEO only.
        response = test_client.post(
            "/api/v1/setup/company",
            json={"company_name": "test", "template": "startup"},
            headers=make_auth_headers("manager"),
        )
        assert response.status_code == 403

    def test_board_member_can_read_but_not_write(
        self,
        test_client: TestClient[Any],
    ) -> None:
        # Read access works
        response = test_client.get(
            "/api/v1/tasks",
            headers=make_auth_headers("board_member"),
        )
        assert response.status_code == 200

        # Write access is denied
        response = test_client.post(
            "/api/v1/tasks",
            json=_TASK_PAYLOAD,
            headers=make_auth_headers("board_member"),
        )
        assert response.status_code == 403

    @pytest.mark.parametrize(
        ("role", "allowed"),
        [
            ("ceo", True),
            ("manager", True),
            ("pair_programmer", False),
            ("board_member", False),
            ("observer", False),
        ],
    )
    def test_ceo_or_manager_guard(
        self,
        test_client: TestClient[Any],
        role: str,
        allowed: bool,
    ) -> None:
        # The autonomy update endpoint is guarded by
        # require_ceo_or_manager. This exercises the guard, not the
        # handler: a permitted role passes the guard (the handler then
        # 404s on the unknown ``test-agent`` -- still proof the guard
        # did not block); a denied role is rejected with 403.
        response = test_client.post(
            "/api/v1/agents/test-agent/autonomy",
            json={"level": "semi", "reason": "guard exercise"},
            headers=make_auth_headers(role),
        )
        if allowed:
            # Guard passed; the handler then 404s on the unknown
            # ``test-agent`` (proof the guard did not block).
            assert response.status_code == 404
        else:
            assert response.status_code == 403
