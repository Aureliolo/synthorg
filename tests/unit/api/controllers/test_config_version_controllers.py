"""Tests for config version history API endpoints.

Covers BudgetConfigVersionController, CompanyVersionController,
EvaluationConfigVersionController, and RoleVersionController.
"""

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import BaseModel

from synthorg.budget.config import BudgetConfig
from synthorg.core.company import Company
from synthorg.core.enums import DepartmentName
from synthorg.core.role import Role
from synthorg.hr.evaluation.config import EvaluationConfig
from synthorg.versioning import VersionSnapshot, compute_content_hash
from tests._shared import LoopAsyncClient
from tests.unit.api.conftest import make_auth_headers

_NOW = datetime(2026, 4, 8, 12, 0, tzinfo=UTC)


def _snap[T: BaseModel](
    entity_id: str,
    model: T,
    version: int = 1,
) -> VersionSnapshot[T]:
    return VersionSnapshot(
        entity_id=entity_id,
        version=version,
        content_hash=compute_content_hash(model),
        snapshot=model,
        saved_by="test-user",
        saved_at=_NOW,
    )


# ── BudgetConfigVersionController ──────────────────────────────


class TestBudgetConfigVersions:
    """GET /budget/config/versions endpoints."""

    @pytest.mark.unit
    async def test_list_versions_empty(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        resp = await async_test_client.get(
            "/api/v1/budget/config/versions",
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 200
        assert resp.json()["data"] == []

    @pytest.mark.unit
    async def test_list_versions_with_data(
        self,
        async_test_client: LoopAsyncClient,
        fake_persistence: Any,
    ) -> None:
        repo = fake_persistence.budget_config_versions
        c1 = BudgetConfig(total_monthly=100.0)
        c2 = BudgetConfig(total_monthly=200.0)
        await repo.save_version(_snap("default", c1, version=1))
        await repo.save_version(_snap("default", c2, version=2))

        resp = await async_test_client.get(
            "/api/v1/budget/config/versions",
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data) == 2
        assert data[0]["version"] == 2
        assert data[1]["version"] == 1

    @pytest.mark.unit
    async def test_get_version(
        self,
        async_test_client: LoopAsyncClient,
        fake_persistence: Any,
    ) -> None:
        repo = fake_persistence.budget_config_versions
        config = BudgetConfig(total_monthly=150.0)
        await repo.save_version(_snap("default", config))

        resp = await async_test_client.get(
            "/api/v1/budget/config/versions/1",
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 200
        snap = resp.json()["data"]
        assert snap["version"] == 1
        assert snap["snapshot"]["total_monthly"] == 150.0

    @pytest.mark.unit
    async def test_get_version_not_found(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        resp = await async_test_client.get(
            "/api/v1/budget/config/versions/99",
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 404

    @pytest.mark.unit
    async def test_list_versions_paginated(
        self,
        async_test_client: LoopAsyncClient,
        fake_persistence: Any,
    ) -> None:
        repo = fake_persistence.budget_config_versions
        for v in range(1, 4):
            c = BudgetConfig(total_monthly=float(v * 100))
            await repo.save_version(_snap("default", c, version=v))

        resp = await async_test_client.get(
            "/api/v1/budget/config/versions?limit=2&offset=0",
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["data"]) == 2


# ── CompanyVersionController ───────────────────────────────────


class TestCompanyVersions:
    """GET /company/versions endpoints."""

    @pytest.mark.unit
    async def test_list_versions_empty(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        resp = await async_test_client.get(
            "/api/v1/company/versions",
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 200
        assert resp.json()["data"] == []

    @pytest.mark.unit
    async def test_list_versions_with_data(
        self,
        async_test_client: LoopAsyncClient,
        fake_persistence: Any,
    ) -> None:
        repo = fake_persistence.company_versions
        c1 = Company(name="Corp v1")
        c2 = Company(name="Corp v2")
        await repo.save_version(_snap("default", c1, version=1))
        await repo.save_version(_snap("default", c2, version=2))

        resp = await async_test_client.get(
            "/api/v1/company/versions",
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data) == 2
        assert data[0]["version"] == 2
        assert data[1]["version"] == 1

    @pytest.mark.unit
    async def test_list_versions_paginated(
        self,
        async_test_client: LoopAsyncClient,
        fake_persistence: Any,
    ) -> None:
        repo = fake_persistence.company_versions
        for v in range(1, 4):
            c = Company(name=f"Corp v{v}")
            await repo.save_version(_snap("default", c, version=v))

        resp = await async_test_client.get(
            "/api/v1/company/versions?limit=2&offset=0",
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["data"]) == 2

    @pytest.mark.unit
    async def test_get_version(
        self,
        async_test_client: LoopAsyncClient,
        fake_persistence: Any,
    ) -> None:
        repo = fake_persistence.company_versions
        company = Company(name="Test Corp")
        await repo.save_version(_snap("default", company))

        resp = await async_test_client.get(
            "/api/v1/company/versions/1",
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 200
        snap = resp.json()["data"]
        assert snap["version"] == 1
        assert snap["snapshot"]["name"] == "Test Corp"

    @pytest.mark.unit
    async def test_get_version_not_found(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        resp = await async_test_client.get(
            "/api/v1/company/versions/99",
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 404


# ── EvaluationConfigVersionController ──────────────────────────


class TestEvaluationConfigVersions:
    """GET /evaluation/config/versions endpoints."""

    @pytest.mark.unit
    async def test_list_versions_empty(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        resp = await async_test_client.get(
            "/api/v1/evaluation/config/versions",
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 200
        assert resp.json()["data"] == []

    @pytest.mark.unit
    async def test_list_versions_with_data(
        self,
        async_test_client: LoopAsyncClient,
        fake_persistence: Any,
    ) -> None:
        repo = fake_persistence.evaluation_config_versions
        c1 = EvaluationConfig()
        c2 = EvaluationConfig(calibration_drift_threshold=0.5)
        await repo.save_version(_snap("default", c1, version=1))
        await repo.save_version(_snap("default", c2, version=2))

        resp = await async_test_client.get(
            "/api/v1/evaluation/config/versions",
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data) == 2
        assert data[0]["version"] == 2
        assert data[1]["version"] == 1

    @pytest.mark.unit
    async def test_list_versions_paginated(
        self,
        async_test_client: LoopAsyncClient,
        fake_persistence: Any,
    ) -> None:
        repo = fake_persistence.evaluation_config_versions
        for v in range(1, 4):
            c = EvaluationConfig(calibration_drift_threshold=0.1 * v)
            await repo.save_version(_snap("default", c, version=v))

        resp = await async_test_client.get(
            "/api/v1/evaluation/config/versions?limit=2&offset=0",
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["data"]) == 2

    @pytest.mark.unit
    async def test_get_version(
        self,
        async_test_client: LoopAsyncClient,
        fake_persistence: Any,
    ) -> None:
        repo = fake_persistence.evaluation_config_versions
        config = EvaluationConfig()
        await repo.save_version(_snap("default", config))

        resp = await async_test_client.get(
            "/api/v1/evaluation/config/versions/1",
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 200
        snap = resp.json()["data"]
        assert snap["version"] == 1

    @pytest.mark.unit
    async def test_get_version_not_found(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        resp = await async_test_client.get(
            "/api/v1/evaluation/config/versions/99",
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 404


# ── RoleVersionController ────────────────────────────────────


class TestRoleVersions:
    """GET /roles/{role_name}/versions endpoints."""

    @pytest.mark.unit
    async def test_list_versions_empty(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        resp = await async_test_client.get(
            "/api/v1/roles/backend-dev/versions",
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 200
        assert resp.json()["data"] == []

    @pytest.mark.unit
    async def test_list_versions_with_data(
        self,
        async_test_client: LoopAsyncClient,
        fake_persistence: Any,
    ) -> None:
        repo = fake_persistence.role_versions
        r1 = Role(
            name="backend-dev",
            department=DepartmentName.ENGINEERING,
            required_skills=(),
            system_prompt_template="v1",
        )
        r2 = Role(
            name="backend-dev",
            department=DepartmentName.ENGINEERING,
            required_skills=("python",),
            system_prompt_template="v2",
        )
        await repo.save_version(_snap("backend-dev", r1, version=1))
        await repo.save_version(_snap("backend-dev", r2, version=2))

        resp = await async_test_client.get(
            "/api/v1/roles/backend-dev/versions",
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data) == 2
        assert data[0]["version"] == 2
        assert data[1]["version"] == 1

    @pytest.mark.unit
    async def test_list_versions_paginated(
        self,
        async_test_client: LoopAsyncClient,
        fake_persistence: Any,
    ) -> None:
        repo = fake_persistence.role_versions
        for v in range(1, 4):
            r = Role(
                name="backend-dev",
                department=DepartmentName.ENGINEERING,
                required_skills=(),
                system_prompt_template=f"v{v}",
            )
            await repo.save_version(_snap("backend-dev", r, version=v))

        # Walk one page to advance past v3, then request the next.
        resp1 = await async_test_client.get(
            "/api/v1/roles/backend-dev/versions?limit=1",
            headers=make_auth_headers("ceo"),
        )
        assert resp1.status_code == 200
        cursor = resp1.json()["pagination"]["next_cursor"]
        assert cursor is not None
        resp = await async_test_client.get(
            f"/api/v1/roles/backend-dev/versions?limit=1&cursor={cursor}",
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["data"]) == 1
        # Descending order: v3, v2, v1 -- cursor advances past v3.
        assert body["data"][0]["version"] == 2
        assert body["pagination"]["limit"] == 1

    @pytest.mark.unit
    async def test_get_version(
        self,
        async_test_client: LoopAsyncClient,
        fake_persistence: Any,
    ) -> None:
        repo = fake_persistence.role_versions
        role = Role(
            name="backend-dev",
            department=DepartmentName.ENGINEERING,
            required_skills=(),
            system_prompt_template="You are a developer.",
        )
        await repo.save_version(_snap("backend-dev", role))

        resp = await async_test_client.get(
            "/api/v1/roles/backend-dev/versions/1",
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 200
        snap = resp.json()["data"]
        assert snap["version"] == 1
        assert snap["snapshot"]["name"] == "backend-dev"

    @pytest.mark.unit
    async def test_get_version_not_found(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        resp = await async_test_client.get(
            "/api/v1/roles/backend-dev/versions/99",
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 404
