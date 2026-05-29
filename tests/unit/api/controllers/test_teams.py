"""Tests for team CRUD controller."""

import json
from typing import Any

import pytest

from synthorg.api.controllers.teams import (
    _check_team_name_unique,
    _find_department,
    _find_team,
    _persisted_name,
)
from synthorg.core.domain_errors import ConflictError, NotFoundError, ValidationError
from tests._shared import LoopAsyncClient
from tests.unit.api.conftest import make_auth_headers

# ── Helpers ────────────────────────────────────────────────


async def _seed_departments(
    async_test_client: LoopAsyncClient,
    depts: list[dict[str, Any]],
) -> None:
    """Seed departments into settings via the settings endpoint."""
    resp = await async_test_client.put(
        "/api/v1/settings/company/departments",
        json={"value": json.dumps(depts)},
        headers=make_auth_headers("ceo"),
    )
    assert resp.status_code < 400, f"seed failed: {resp.text}"


def _dept_with_teams(
    name: str = "engineering",
    budget: float = 60.0,
    teams: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "budget_percent": budget,
        "teams": teams or [],
    }


def _team(
    name: str = "backend",
    lead: str = "alice",
    members: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "lead": lead,
        "members": members or [],
    }


# ── Create Team ──────────────────────────────────────────


@pytest.mark.unit
class TestCreateTeam:
    async def test_create_team_success(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        await _seed_departments(async_test_client, [_dept_with_teams()])
        resp = await async_test_client.post(
            "/api/v1/departments/engineering/teams",
            json={"name": "backend", "lead": "alice", "members": ["bob"]},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["data"]["name"] == "backend"
        assert body["data"]["lead"] == "alice"
        assert body["data"]["members"] == ["bob"]

    async def test_create_team_department_not_found(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        resp = await async_test_client.post(
            "/api/v1/departments/nonexistent/teams",
            json={"name": "backend", "lead": "alice"},
        )
        assert resp.status_code == 404

    async def test_create_team_duplicate_name_conflict(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        await _seed_departments(
            async_test_client,
            [_dept_with_teams(teams=[_team("backend")])],
        )
        resp = await async_test_client.post(
            "/api/v1/departments/engineering/teams",
            json={"name": "backend", "lead": "bob"},
        )
        assert resp.status_code == 409

    async def test_create_team_duplicate_name_case_insensitive(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        await _seed_departments(
            async_test_client,
            [_dept_with_teams(teams=[_team("Backend")])],
        )
        resp = await async_test_client.post(
            "/api/v1/departments/engineering/teams",
            json={"name": "backend", "lead": "bob"},
        )
        assert resp.status_code == 409

    async def test_create_team_duplicate_members_rejected(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        await _seed_departments(async_test_client, [_dept_with_teams()])
        resp = await async_test_client.post(
            "/api/v1/departments/engineering/teams",
            json={"name": "t1", "lead": "a", "members": ["bob", "bob"]},
        )
        assert resp.status_code == 422

    async def test_create_team_blank_name_rejected(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        await _seed_departments(async_test_client, [_dept_with_teams()])
        resp = await async_test_client.post(
            "/api/v1/departments/engineering/teams",
            json={"name": "  ", "lead": "alice"},
        )
        assert resp.status_code in {400, 422}

    async def test_create_team_no_members_defaults_empty(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        await _seed_departments(async_test_client, [_dept_with_teams()])
        resp = await async_test_client.post(
            "/api/v1/departments/engineering/teams",
            json={"name": "backend", "lead": "alice"},
        )
        assert resp.status_code == 201
        assert resp.json()["data"]["members"] == []

    async def test_create_team_requires_write_access(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        await _seed_departments(async_test_client, [_dept_with_teams()])
        resp = await async_test_client.post(
            "/api/v1/departments/engineering/teams",
            json={"name": "backend", "lead": "alice"},
            headers=make_auth_headers("observer"),
        )
        assert resp.status_code == 403


# ── Update Team ──────────────────────────────────────────


@pytest.mark.unit
class TestUpdateTeam:
    async def test_update_team_rename(self, async_test_client: LoopAsyncClient) -> None:
        await _seed_departments(
            async_test_client,
            [_dept_with_teams(teams=[_team("backend", lead="alice")])],
        )
        resp = await async_test_client.patch(
            "/api/v1/departments/engineering/teams/backend",
            json={"name": "platform"},
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["name"] == "platform"
        assert resp.json()["data"]["lead"] == "alice"

    async def test_update_team_change_lead(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        await _seed_departments(
            async_test_client,
            [_dept_with_teams(teams=[_team("backend", lead="alice")])],
        )
        resp = await async_test_client.patch(
            "/api/v1/departments/engineering/teams/backend",
            json={"lead": "bob"},
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["lead"] == "bob"

    async def test_update_team_replace_members(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        await _seed_departments(
            async_test_client,
            [_dept_with_teams(teams=[_team("backend", members=["a"])])],
        )
        resp = await async_test_client.patch(
            "/api/v1/departments/engineering/teams/backend",
            json={"members": ["x", "y"]},
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["members"] == ["x", "y"]

    async def test_update_team_not_found(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        await _seed_departments(async_test_client, [_dept_with_teams()])
        resp = await async_test_client.patch(
            "/api/v1/departments/engineering/teams/nonexistent",
            json={"name": "new-name"},
        )
        assert resp.status_code == 404

    async def test_update_team_rename_conflict(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        await _seed_departments(
            async_test_client,
            [
                _dept_with_teams(
                    teams=[_team("backend"), _team("frontend", lead="bob")],
                ),
            ],
        )
        resp = await async_test_client.patch(
            "/api/v1/departments/engineering/teams/backend",
            json={"name": "frontend"},
        )
        assert resp.status_code == 409

    async def test_update_team_rename_same_name_ok(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        """Renaming a team to its own name should succeed."""
        await _seed_departments(
            async_test_client,
            [_dept_with_teams(teams=[_team("backend")])],
        )
        resp = await async_test_client.patch(
            "/api/v1/departments/engineering/teams/backend",
            json={"name": "backend"},
        )
        assert resp.status_code == 200

    async def test_update_team_department_not_found(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        resp = await async_test_client.patch(
            "/api/v1/departments/nonexistent/teams/backend",
            json={"name": "new"},
        )
        assert resp.status_code == 404


# ── Delete Team ──────────────────────────────────────────


@pytest.mark.unit
class TestDeleteTeam:
    async def test_delete_team_success(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        await _seed_departments(
            async_test_client,
            [_dept_with_teams(teams=[_team("backend")])],
        )
        resp = await async_test_client.delete(
            "/api/v1/departments/engineering/teams/backend",
        )
        assert resp.status_code == 204

        # Verify team is gone.
        resp2 = await async_test_client.patch(
            "/api/v1/departments/engineering/teams/backend",
            json={"name": "backend"},
        )
        assert resp2.status_code == 404

    async def test_delete_team_not_found(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        await _seed_departments(async_test_client, [_dept_with_teams()])
        resp = await async_test_client.delete(
            "/api/v1/departments/engineering/teams/nonexistent",
        )
        assert resp.status_code == 404

    async def test_delete_team_self_reassign_rejected(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        await _seed_departments(
            async_test_client,
            [_dept_with_teams(teams=[_team("backend", members=["alice"])])],
        )
        resp = await async_test_client.delete(
            "/api/v1/departments/engineering/teams/backend?reassign_to=backend",
        )
        assert resp.status_code == 422

    async def test_delete_team_with_reassign(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        await _seed_departments(
            async_test_client,
            [
                _dept_with_teams(
                    teams=[
                        _team("backend", lead="alice", members=["bob"]),
                        _team("frontend", lead="carol", members=["dave"]),
                    ],
                ),
            ],
        )
        resp = await async_test_client.delete(
            "/api/v1/departments/engineering/teams/backend?reassign_to=frontend",
        )
        assert resp.status_code == 204

        # Verify backend members merged into frontend.
        resp2 = await async_test_client.patch(
            "/api/v1/departments/engineering/teams/frontend",
            json={},
        )
        assert resp2.status_code == 200
        members = resp2.json()["data"]["members"]
        assert "bob" in members
        assert "dave" in members

    async def test_delete_team_reassign_target_not_found(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        await _seed_departments(
            async_test_client,
            [_dept_with_teams(teams=[_team("backend")])],
        )
        resp = await async_test_client.delete(
            "/api/v1/departments/engineering/teams/backend?reassign_to=nonexistent",
        )
        assert resp.status_code == 404

    async def test_delete_team_reassign_deduplicates_members(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        await _seed_departments(
            async_test_client,
            [
                _dept_with_teams(
                    teams=[
                        _team("a", lead="lead-a", members=["shared"]),
                        _team("b", lead="lead-b", members=["shared"]),
                    ],
                ),
            ],
        )
        resp = await async_test_client.delete(
            "/api/v1/departments/engineering/teams/a?reassign_to=b",
        )
        assert resp.status_code == 204

        resp2 = await async_test_client.patch(
            "/api/v1/departments/engineering/teams/b",
            json={},
        )
        members = resp2.json()["data"]["members"]
        # "shared" should appear only once.
        assert members.count("shared") == 1


# ── Reorder Teams ────────────────────────────────────────


@pytest.mark.unit
class TestReorderTeams:
    async def test_reorder_teams_success(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        await _seed_departments(
            async_test_client,
            [
                _dept_with_teams(
                    teams=[
                        _team("alpha", lead="a"),
                        _team("beta", lead="b"),
                        _team("gamma", lead="c"),
                    ],
                ),
            ],
        )
        resp = await async_test_client.patch(
            "/api/v1/departments/engineering/teams/reorder",
            json={"team_names": ["gamma", "alpha", "beta"]},
        )
        assert resp.status_code == 200
        names = [t["name"] for t in resp.json()["data"]]
        assert names == ["gamma", "alpha", "beta"]

    async def test_reorder_teams_missing_name_rejected(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        await _seed_departments(
            async_test_client,
            [
                _dept_with_teams(
                    teams=[_team("alpha"), _team("beta")],
                ),
            ],
        )
        resp = await async_test_client.patch(
            "/api/v1/departments/engineering/teams/reorder",
            json={"team_names": ["alpha"]},
        )
        assert resp.status_code == 422

    async def test_reorder_teams_extra_name_rejected(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        await _seed_departments(
            async_test_client,
            [_dept_with_teams(teams=[_team("alpha")])],
        )
        resp = await async_test_client.patch(
            "/api/v1/departments/engineering/teams/reorder",
            json={"team_names": ["alpha", "nonexistent"]},
        )
        assert resp.status_code == 422

    async def test_reorder_teams_duplicate_names_rejected(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        await _seed_departments(
            async_test_client,
            [_dept_with_teams(teams=[_team("alpha"), _team("beta")])],
        )
        resp = await async_test_client.patch(
            "/api/v1/departments/engineering/teams/reorder",
            json={"team_names": ["alpha", "alpha"]},
        )
        assert resp.status_code == 422

    async def test_reorder_teams_case_insensitive_duplicates_rejected(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        await _seed_departments(
            async_test_client,
            [_dept_with_teams(teams=[_team("alpha"), _team("beta")])],
        )
        resp = await async_test_client.patch(
            "/api/v1/departments/engineering/teams/reorder",
            json={"team_names": ["Alpha", "ALPHA"]},
        )
        assert resp.status_code == 422

    async def test_reorder_zero_teams(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        await _seed_departments(async_test_client, [_dept_with_teams(teams=[])])
        resp = await async_test_client.patch(
            "/api/v1/departments/engineering/teams/reorder",
            json={"team_names": []},
        )
        assert resp.status_code == 200
        assert resp.json()["data"] == []

    async def test_reorder_teams_department_not_found(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        resp = await async_test_client.patch(
            "/api/v1/departments/nonexistent/teams/reorder",
            json={"team_names": []},
        )
        assert resp.status_code == 404


# ── Private helpers (direct unit tests) ──────────────────────


@pytest.mark.unit
class TestPrivateHelpers:
    """Direct tests for the module-level lookup helpers.

    The controller endpoints exercise these indirectly, but a direct
    unit suite gives independent insurance: a regression that drops
    one of the ``normalize_identifier`` calls would show up here even
    if the controller's request flow happens to mask it.
    """

    async def test_find_department_matches_case_insensitively(self) -> None:
        depts = [
            {"name": "Engineering", "budget_percent": 60.0, "teams": []},
            {"name": "Design", "budget_percent": 40.0, "teams": []},
        ]
        idx, dept = _find_department(depts, "engineering")
        assert idx == 0
        assert dept["name"] == "Engineering"

    async def test_find_department_strips_whitespace(self) -> None:
        depts = [{"name": "Engineering", "budget_percent": 60.0, "teams": []}]
        idx, dept = _find_department(depts, "  ENGINEERING  ")
        assert idx == 0
        assert dept["name"] == "Engineering"

    async def test_find_department_raises_not_found(self) -> None:
        depts = [{"name": "Engineering", "budget_percent": 60.0, "teams": []}]
        with pytest.raises(NotFoundError, match="Department"):
            _find_department(depts, "marketing")

    async def test_find_team_matches_case_insensitively(self) -> None:
        teams = [_team(name="Backend"), _team(name="Frontend")]
        idx, team = _find_team(teams, "backend")
        assert idx == 0
        assert team["name"] == "Backend"

    async def test_find_team_strips_whitespace(self) -> None:
        teams = [_team(name="Backend")]
        idx, team = _find_team(teams, "  BACKEND  ")
        assert idx == 0
        assert team["name"] == "Backend"

    async def test_find_team_raises_not_found(self) -> None:
        teams = [_team(name="Backend")]
        with pytest.raises(NotFoundError, match="Team"):
            _find_team(teams, "platform")

    async def test_check_team_name_unique_passes_for_new_name(self) -> None:
        teams = [_team(name="Backend")]
        _check_team_name_unique(teams, "Frontend")

    async def test_check_team_name_unique_rejects_case_collision(self) -> None:
        teams = [_team(name="Backend")]
        with pytest.raises(ConflictError, match="already exists"):
            _check_team_name_unique(teams, "BACKEND")

    async def test_check_team_name_unique_rejects_whitespace_collision(self) -> None:
        teams = [_team(name="Backend")]
        with pytest.raises(ConflictError, match="already exists"):
            _check_team_name_unique(teams, "  Backend  ")

    async def test_check_team_name_unique_skips_excluded_index(self) -> None:
        teams = [_team(name="Backend"), _team(name="Frontend")]
        _check_team_name_unique(teams, "Backend", exclude_index=0)

    async def test_persisted_name_returns_string_value(self) -> None:
        assert _persisted_name({"name": "Engineering"}, "Department") == "Engineering"

    async def test_persisted_name_rejects_missing_name(self) -> None:
        with pytest.raises(ValidationError, match="non-string"):
            _persisted_name({}, "Department")

    async def test_persisted_name_rejects_none_name(self) -> None:
        with pytest.raises(ValidationError, match="non-string"):
            _persisted_name({"name": None}, "Team")

    async def test_persisted_name_rejects_int_name(self) -> None:
        with pytest.raises(ValidationError, match="non-string"):
            _persisted_name({"name": 42}, "Team")

    async def test_find_department_surfaces_corrupted_record(self) -> None:
        depts: list[dict[str, Any]] = [
            {"name": None, "budget_percent": 50.0, "teams": []},
        ]
        with pytest.raises(ValidationError, match="non-string"):
            _find_department(depts, "anything")

    async def test_find_team_surfaces_corrupted_record(self) -> None:
        teams: list[dict[str, Any]] = [
            {"name": 7, "lead": "alice", "members": []},
        ]
        with pytest.raises(ValidationError, match="non-string"):
            _find_team(teams, "anything")
