"""Tests for user management controller (CEO-only CRUD)."""

import uuid

import pytest

from tests._shared import JsonDict, LoopAsyncClient
from tests.unit.api.conftest import make_auth_headers

# Must match the ID pattern in conftest._seed_test_users
_SYSTEM_USER_ID = str(uuid.uuid5(uuid.NAMESPACE_DNS, "test-system"))

_BASE = "/api/v1/users"
_CEO_HEADERS = make_auth_headers("ceo")


def _create_payload(
    **overrides: object,
) -> JsonDict:
    defaults: JsonDict = {
        "username": "new-user",
        "password": "secure-password-12chars",
        "role": "manager",
    }
    return {**defaults, **overrides}


@pytest.mark.unit
class TestCreateUser:
    """CEO-only user creation with role, password, and uniqueness validation."""

    async def test_create_manager(self, async_test_client: LoopAsyncClient) -> None:
        resp = await async_test_client.post(
            _BASE,
            json=_create_payload(),
            headers=_CEO_HEADERS,
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["success"] is True
        data = body["data"]
        assert data["username"] == "new-user"
        assert data["role"] == "manager"
        assert data["must_change_password"] is True
        assert "password_hash" not in data

    @pytest.mark.parametrize(
        ("username", "role"),
        [
            ("board-user", "board_member"),
            ("pp-user", "pair_programmer"),
            ("obs-user", "observer"),
        ],
    )
    async def test_create_valid_roles(
        self,
        async_test_client: LoopAsyncClient,
        username: str,
        role: str,
    ) -> None:
        resp = await async_test_client.post(
            _BASE,
            json=_create_payload(username=username, role=role),
            headers=_CEO_HEADERS,
        )
        assert resp.status_code == 201
        assert resp.json()["data"]["role"] == role

    async def test_create_second_ceo_rejected(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        resp = await async_test_client.post(
            _BASE,
            json=_create_payload(username="ceo2", role="ceo"),
            headers=_CEO_HEADERS,
        )
        assert resp.status_code == 409

    async def test_create_system_role_rejected(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        resp = await async_test_client.post(
            _BASE,
            json=_create_payload(username="sys", role="system"),
            headers=_CEO_HEADERS,
        )
        assert resp.status_code == 422

    async def test_create_duplicate_username_rejected(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        first = await async_test_client.post(
            _BASE,
            json=_create_payload(username="dup-user"),
            headers=_CEO_HEADERS,
        )
        assert first.status_code == 201

        resp = await async_test_client.post(
            _BASE,
            json=_create_payload(username="dup-user"),
            headers=_CEO_HEADERS,
        )
        assert resp.status_code == 409

    async def test_create_short_password_rejected(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        resp = await async_test_client.post(
            _BASE,
            json=_create_payload(password="short"),
            headers=_CEO_HEADERS,
        )
        assert resp.status_code == 422

    @pytest.mark.parametrize(
        "role",
        ["manager", "board_member", "pair_programmer", "observer"],
    )
    async def test_non_ceo_blocked(
        self,
        async_test_client: LoopAsyncClient,
        role: str,
    ) -> None:
        resp = await async_test_client.post(
            _BASE,
            json=_create_payload(),
            headers=make_auth_headers(role),
        )
        assert resp.status_code == 403


@pytest.mark.unit
class TestListUsers:
    """List users endpoint returns seeded data and enforces CEO guard."""

    async def test_list_returns_seeded_users(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        resp = await async_test_client.get(_BASE, headers=_CEO_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        # Seeded users: one per HumanRole (6), minus system = 5
        assert len(body["data"]) == 5
        assert all("password_hash" not in u for u in body["data"])

    async def test_list_blocked_for_observer(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        resp = await async_test_client.get(
            _BASE,
            headers=make_auth_headers("observer"),
        )
        assert resp.status_code == 403

    async def test_list_pagination_metadata_present(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        resp = await async_test_client.get(_BASE, headers=_CEO_HEADERS)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "pagination" in body
        assert body["pagination"]["limit"] == 50
        # ``total`` is ``null`` under keyset pagination because the
        # endpoint skips the COUNT(*) round-trip on every request;
        # clients derive display counts from ``data.length`` per the
        # frontend contract in ``web/CLAUDE.md``.
        assert body["pagination"]["has_more"] is False
        assert body["pagination"]["next_cursor"] is None

    async def test_list_limit_page_chain(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        # 5 seeded users, limit=2: walk all three pages so a backend
        # that drops the fifth user or clears ``has_more`` one page
        # early cannot pass on just the first two pages.
        first_resp = await async_test_client.get(
            _BASE,
            params={"limit": 2},
            headers=_CEO_HEADERS,
        )
        assert first_resp.status_code == 200, first_resp.text
        first = first_resp.json()
        assert len(first["data"]) == 2
        assert first["pagination"]["has_more"] is True
        cursor = first["pagination"]["next_cursor"]
        assert cursor is not None

        second_resp = await async_test_client.get(
            _BASE,
            params={"limit": 2, "cursor": cursor},
            headers=_CEO_HEADERS,
        )
        assert second_resp.status_code == 200, second_resp.text
        second = second_resp.json()
        assert len(second["data"]) == 2
        assert second["pagination"]["has_more"] is True
        third_cursor = second["pagination"]["next_cursor"]
        assert third_cursor is not None
        first_ids = {u["id"] for u in first["data"]}
        second_ids = {u["id"] for u in second["data"]}
        assert first_ids.isdisjoint(second_ids)

        third_resp = await async_test_client.get(
            _BASE,
            params={"limit": 2, "cursor": third_cursor},
            headers=_CEO_HEADERS,
        )
        assert third_resp.status_code == 200, third_resp.text
        third = third_resp.json()
        assert len(third["data"]) == 1
        assert third["pagination"]["has_more"] is False
        assert third["pagination"]["next_cursor"] is None
        third_ids = {u["id"] for u in third["data"]}
        assert first_ids.isdisjoint(third_ids)
        assert second_ids.isdisjoint(third_ids)

    async def test_list_invalid_cursor_returns_400(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        resp = await async_test_client.get(
            f"{_BASE}?cursor=not-a-real-cursor",
            headers=_CEO_HEADERS,
        )
        assert resp.status_code == 400

    async def test_list_stable_ordering(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        first_resp = await async_test_client.get(
            _BASE,
            params={"limit": 5},
            headers=_CEO_HEADERS,
        )
        assert first_resp.status_code == 200, first_resp.text
        first = first_resp.json()["data"]
        second_resp = await async_test_client.get(
            _BASE,
            params={"limit": 5},
            headers=_CEO_HEADERS,
        )
        assert second_resp.status_code == 200, second_resp.text
        second = second_resp.json()["data"]
        assert first == second
        # Lock the ordering contract to ascending ``id`` -- repeatability
        # alone would also pass for a backend that returns rows in
        # ``created_at`` (or any other) order.  ``id`` is the sort key
        # the keyset cursor advances on, so any drift here would
        # break cursor pagination semantics.
        ids = [u["id"] for u in first]
        assert ids == sorted(ids)


@pytest.mark.unit
class TestGetUser:
    """Get user by ID with not-found handling."""

    async def test_get_existing_user(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        # Create a user first
        create_resp = await async_test_client.post(
            _BASE,
            json=_create_payload(username="get-test"),
            headers=_CEO_HEADERS,
        )
        user_id = create_resp.json()["data"]["id"]

        resp = await async_test_client.get(
            f"{_BASE}/{user_id}",
            headers=_CEO_HEADERS,
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["username"] == "get-test"
        assert "password_hash" not in data

    async def test_get_nonexistent_returns_404(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        resp = await async_test_client.get(
            f"{_BASE}/nonexistent-id",
            headers=_CEO_HEADERS,
        )
        assert resp.status_code == 404


@pytest.mark.unit
class TestUpdateUserRole:
    """Role update with CEO demotion, promotion, and system-user guards."""

    async def test_update_role(self, async_test_client: LoopAsyncClient) -> None:
        create_resp = await async_test_client.post(
            _BASE,
            json=_create_payload(username="update-test"),
            headers=_CEO_HEADERS,
        )
        user_id = create_resp.json()["data"]["id"]

        resp = await async_test_client.patch(
            f"{_BASE}/{user_id}",
            json={"role": "observer"},
            headers=_CEO_HEADERS,
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["role"] == "observer"
        assert "password_hash" not in data

    async def test_update_to_system_rejected(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        create_resp = await async_test_client.post(
            _BASE,
            json=_create_payload(username="sys-update"),
            headers=_CEO_HEADERS,
        )
        user_id = create_resp.json()["data"]["id"]

        resp = await async_test_client.patch(
            f"{_BASE}/{user_id}",
            json={"role": "system"},
            headers=_CEO_HEADERS,
        )
        assert resp.status_code == 422

    async def test_update_system_user_rejected(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        resp = await async_test_client.patch(
            f"{_BASE}/{_SYSTEM_USER_ID}",
            json={"role": "manager"},
            headers=_CEO_HEADERS,
        )
        assert resp.status_code == 409

    async def test_update_nonexistent_returns_404(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        resp = await async_test_client.patch(
            f"{_BASE}/nonexistent-id",
            json={"role": "observer"},
            headers=_CEO_HEADERS,
        )
        assert resp.status_code == 404

    async def test_demote_only_ceo_rejected(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        # The seeded CEO is the only one -- changing role must fail.
        list_resp = await async_test_client.get(_BASE, headers=_CEO_HEADERS)
        ceo_users = [u for u in list_resp.json()["data"] if u["role"] == "ceo"]
        assert len(ceo_users) == 1
        ceo_id = ceo_users[0]["id"]

        resp = await async_test_client.patch(
            f"{_BASE}/{ceo_id}",
            json={"role": "manager"},
            headers=_CEO_HEADERS,
        )
        assert resp.status_code == 409

    async def test_promote_to_second_ceo_rejected(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        create_resp = await async_test_client.post(
            _BASE,
            json=_create_payload(username="promote-test"),
            headers=_CEO_HEADERS,
        )
        user_id = create_resp.json()["data"]["id"]

        resp = await async_test_client.patch(
            f"{_BASE}/{user_id}",
            json={"role": "ceo"},
            headers=_CEO_HEADERS,
        )
        assert resp.status_code == 409


@pytest.mark.unit
class TestDeleteUser:
    """User deletion with self-delete, system-user, and CEO guards."""

    async def test_delete_user(self, async_test_client: LoopAsyncClient) -> None:
        create_resp = await async_test_client.post(
            _BASE,
            json=_create_payload(username="delete-test"),
            headers=_CEO_HEADERS,
        )
        user_id = create_resp.json()["data"]["id"]

        resp = await async_test_client.delete(
            f"{_BASE}/{user_id}",
            headers=_CEO_HEADERS,
        )
        assert resp.status_code == 204

        # Verify deleted
        get_resp = await async_test_client.get(
            f"{_BASE}/{user_id}",
            headers=_CEO_HEADERS,
        )
        assert get_resp.status_code == 404

    async def test_delete_nonexistent_returns_404(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        resp = await async_test_client.delete(
            f"{_BASE}/nonexistent-id",
            headers=_CEO_HEADERS,
        )
        assert resp.status_code == 404

    async def test_delete_system_user_rejected(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        resp = await async_test_client.delete(
            f"{_BASE}/{_SYSTEM_USER_ID}",
            headers=_CEO_HEADERS,
        )
        assert resp.status_code == 409

    async def test_delete_ceo_self_rejected(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        # The authenticated CEO attempts to delete themselves;
        # self-deletion check fires before the CEO role check.
        list_resp = await async_test_client.get(_BASE, headers=_CEO_HEADERS)
        ceo_users = [u for u in list_resp.json()["data"] if u["role"] == "ceo"]
        assert len(ceo_users) > 0
        ceo_id = ceo_users[0]["id"]

        resp = await async_test_client.delete(
            f"{_BASE}/{ceo_id}",
            headers=_CEO_HEADERS,
        )
        assert resp.status_code == 409
