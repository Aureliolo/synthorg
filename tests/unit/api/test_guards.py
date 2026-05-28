"""Tests for route guards with JWT-based authentication."""

import pytest

from tests._shared import LoopAsyncClient
from tests.unit.api.conftest import make_auth_headers

# To test "no auth" we need a fresh client without default headers.
# The async_test_client fixture sets CEO headers. Passing headers={} to
# a request merges with session defaults -- it does NOT clear them.
# Instead we create a bare_client fixture.


@pytest.fixture
async def bare_client(async_test_client: LoopAsyncClient) -> LoopAsyncClient:
    """Test client with no default Authorization header."""
    async_test_client.headers.pop("authorization", None)
    return async_test_client


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
    async def test_allows_ceo(self, async_test_client: LoopAsyncClient) -> None:
        response = await async_test_client.post(
            "/api/v1/tasks",
            json=_TASK_PAYLOAD,
            headers=make_auth_headers("ceo"),
        )
        assert response.status_code == 202

    async def test_allows_manager(self, async_test_client: LoopAsyncClient) -> None:
        response = await async_test_client.post(
            "/api/v1/tasks",
            json=_TASK_PAYLOAD,
            headers=make_auth_headers("manager"),
        )
        assert response.status_code == 202

    async def test_blocks_board_member(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        response = await async_test_client.post(
            "/api/v1/tasks",
            json=_TASK_PAYLOAD,
            headers=make_auth_headers("board_member"),
        )
        assert response.status_code == 403

    async def test_allows_pair_programmer(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        response = await async_test_client.post(
            "/api/v1/tasks",
            json=_TASK_PAYLOAD,
            headers=make_auth_headers("pair_programmer"),
        )
        assert response.status_code == 202

    async def test_blocks_observer(self, async_test_client: LoopAsyncClient) -> None:
        response = await async_test_client.post(
            "/api/v1/tasks",
            json=_TASK_PAYLOAD,
            headers=make_auth_headers("observer"),
        )
        assert response.status_code == 403

    async def test_missing_auth_returns_401(self, bare_client: LoopAsyncClient) -> None:
        response = await bare_client.post(
            "/api/v1/tasks",
            json=_TASK_PAYLOAD,
        )
        assert response.status_code == 401

    async def test_invalid_token_returns_401(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        response = await async_test_client.post(
            "/api/v1/tasks",
            json=_TASK_PAYLOAD,
            headers={"Authorization": "Bearer invalid-token"},
        )
        assert response.status_code == 401


@pytest.mark.unit
class TestReadGuard:
    async def test_allows_observer(self, async_test_client: LoopAsyncClient) -> None:
        response = await async_test_client.get(
            "/api/v1/tasks",
            headers=make_auth_headers("observer"),
        )
        assert response.status_code == 200

    async def test_allows_board_member(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        response = await async_test_client.get(
            "/api/v1/tasks",
            headers=make_auth_headers("board_member"),
        )
        assert response.status_code == 200

    async def test_allows_ceo(self, async_test_client: LoopAsyncClient) -> None:
        response = await async_test_client.get(
            "/api/v1/tasks",
            headers=make_auth_headers("ceo"),
        )
        assert response.status_code == 200

    async def test_missing_auth_returns_401(self, bare_client: LoopAsyncClient) -> None:
        response = await bare_client.get("/api/v1/tasks")
        assert response.status_code == 401

    async def test_invalid_token_returns_401(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        response = await async_test_client.get(
            "/api/v1/tasks",
            headers={"Authorization": "Bearer invalid-token"},
        )
        assert response.status_code == 401


@pytest.mark.unit
class TestRequireRoles:
    """Tests for the require_roles() guard factory via live endpoints."""

    async def test_setup_blocks_manager(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        # Setup write endpoints now require CEO only.
        response = await async_test_client.post(
            "/api/v1/setup/company",
            json={"company_name": "test", "template": "startup"},
            headers=make_auth_headers("manager"),
        )
        assert response.status_code == 403

    async def test_board_member_can_read_but_not_write(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        # Read access works
        response = await async_test_client.get(
            "/api/v1/tasks",
            headers=make_auth_headers("board_member"),
        )
        assert response.status_code == 200

        # Write access is denied
        response = await async_test_client.post(
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
    async def test_ceo_or_manager_guard(
        self,
        async_test_client: LoopAsyncClient,
        role: str,
        allowed: bool,
    ) -> None:
        # The autonomy update endpoint is guarded by
        # require_ceo_or_manager. This exercises the guard, not the
        # handler: a permitted role passes the guard (the handler then
        # 404s on the unknown ``test-agent`` -- still proof the guard
        # did not block); a denied role is rejected with 403.
        response = await async_test_client.post(
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
