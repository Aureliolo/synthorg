"""Unit tests for organization MCP handlers.

Covers 19 tools: company (6), departments (6), teams (5), role
versions (2).
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import structlog.testing

from synthorg.api.state import AppState
from synthorg.communication.mcp_errors import CapabilityNotSupportedError
from synthorg.meta.mcp.handlers.organization import ORGANIZATION_HANDLERS
from synthorg.observability.events.mcp import MCP_ADMIN_OP_EXECUTED
from synthorg.organization._team_service import TeamService
from synthorg.organization.services import (
    DepartmentService,
)
from synthorg.organization.state import OrganizationStateSlice
from tests._shared import make_app_state
from tests.unit.meta.mcp.conftest import make_test_actor

pytestmark = pytest.mark.unit


@pytest.fixture
def fake_company() -> AsyncMock:
    service = AsyncMock()
    service.get_company = AsyncMock(return_value={"name": "Acme"})
    service.update_company = AsyncMock(return_value={"name": "Acme2"})
    service.list_departments = AsyncMock(return_value=())
    service.reorder_departments = AsyncMock(return_value=None)
    service.list_versions = AsyncMock(return_value=())
    service.get_version = AsyncMock(return_value=None)
    return service


@pytest.fixture
def fake_role_version() -> AsyncMock:
    service = AsyncMock()
    service.list_versions = AsyncMock(return_value=((), 0))
    service.get_version = AsyncMock(return_value=None)
    return service


@pytest.fixture
def real_department() -> DepartmentService:
    return DepartmentService()


@pytest.fixture
def real_team() -> TeamService:
    return TeamService()


@pytest.fixture
def fake_app_state(
    fake_company: AsyncMock,
    real_department: DepartmentService,
    real_team: TeamService,
    fake_role_version: AsyncMock,
) -> AppState:
    return make_app_state(
        slices={
            OrganizationStateSlice: {
                "company_read_service": fake_company,
                "department_service": real_department,
                "team_service": real_team,
                "role_version_service": fake_role_version,
            },
        },
    )


class TestCompany:
    async def test_get(self, fake_app_state: AppState) -> None:
        handler = ORGANIZATION_HANDLERS["synthorg_company_get"]
        response = await handler(app_state=fake_app_state, arguments={})
        assert json.loads(response)["status"] == "ok"

    async def test_update_requires_payload(
        self,
        fake_app_state: AppState,
    ) -> None:
        handler = ORGANIZATION_HANDLERS["synthorg_company_update"]
        response = await handler(
            app_state=fake_app_state,
            arguments={},
            actor=make_test_actor(),
        )
        assert json.loads(response)["status"] == "error"

    async def test_update_ok(self, fake_app_state: AppState) -> None:
        handler = ORGANIZATION_HANDLERS["synthorg_company_update"]
        response = await handler(
            app_state=fake_app_state,
            arguments={"payload": {"name": "Acme2"}},
            actor=make_test_actor(),
        )
        assert json.loads(response)["status"] == "ok"

    async def test_list_departments(self, fake_app_state: AppState) -> None:
        handler = ORGANIZATION_HANDLERS["synthorg_company_list_departments"]
        response = await handler(app_state=fake_app_state, arguments={})
        assert json.loads(response)["status"] == "ok"

    async def test_reorder_requires_list(
        self,
        fake_app_state: AppState,
    ) -> None:
        handler = ORGANIZATION_HANDLERS["synthorg_company_reorder_departments"]
        response = await handler(
            app_state=fake_app_state,
            arguments={},
            actor=make_test_actor(),
        )
        assert json.loads(response)["status"] == "error"

    async def test_reorder_ok(self, fake_app_state: AppState) -> None:
        handler = ORGANIZATION_HANDLERS["synthorg_company_reorder_departments"]
        response = await handler(
            app_state=fake_app_state,
            arguments={
                "department_ids": [
                    "11111111-1111-1111-1111-111111111111",
                    "22222222-2222-2222-2222-222222222222",
                ],
            },
            actor=make_test_actor(),
        )
        assert json.loads(response)["status"] == "ok"

    async def test_reorder_rejects_empty(
        self,
        fake_app_state: AppState,
    ) -> None:
        handler = ORGANIZATION_HANDLERS["synthorg_company_reorder_departments"]
        response = await handler(
            app_state=fake_app_state,
            arguments={"department_ids": []},
            actor=make_test_actor(),
        )
        payload = json.loads(response)
        assert payload["status"] == "error"
        assert payload["domain_code"] == "invalid_argument"

    async def test_versions_list(self, fake_app_state: AppState) -> None:
        handler = ORGANIZATION_HANDLERS["synthorg_company_versions_list"]
        response = await handler(app_state=fake_app_state, arguments={})
        payload = json.loads(response)
        assert payload["status"] == "ok"
        assert payload["data"] == []

    async def test_versions_get_not_found(
        self,
        fake_app_state: AppState,
    ) -> None:
        handler = ORGANIZATION_HANDLERS["synthorg_company_versions_get"]
        response = await handler(
            app_state=fake_app_state,
            arguments={"version_id": "v1"},
        )
        assert json.loads(response)["domain_code"] == "not_found"

    async def test_get_capability_gap(
        self,
        fake_app_state: AppState,
        fake_company: AsyncMock,
    ) -> None:
        fake_company.get_company = AsyncMock(
            side_effect=CapabilityNotSupportedError("company_get", "x"),
        )
        handler = ORGANIZATION_HANDLERS["synthorg_company_get"]
        response = await handler(app_state=fake_app_state, arguments={})
        assert json.loads(response)["domain_code"] == "not_supported"

    async def test_list_departments_capability_gap(
        self,
        fake_app_state: AppState,
        fake_company: AsyncMock,
    ) -> None:
        fake_company.list_departments = AsyncMock(
            side_effect=CapabilityNotSupportedError("company_list_departments", "x"),
        )
        handler = ORGANIZATION_HANDLERS["synthorg_company_list_departments"]
        response = await handler(app_state=fake_app_state, arguments={})
        assert json.loads(response)["domain_code"] == "not_supported"

    async def test_versions_list_capability_gap(
        self,
        fake_app_state: AppState,
        fake_company: AsyncMock,
    ) -> None:
        fake_company.list_versions = AsyncMock(
            side_effect=CapabilityNotSupportedError("company_list_versions", "x"),
        )
        handler = ORGANIZATION_HANDLERS["synthorg_company_versions_list"]
        response = await handler(app_state=fake_app_state, arguments={})
        assert json.loads(response)["domain_code"] == "not_supported"

    async def test_versions_get_capability_gap(
        self,
        fake_app_state: AppState,
        fake_company: AsyncMock,
    ) -> None:
        fake_company.get_version = AsyncMock(
            side_effect=CapabilityNotSupportedError("company_get_version", "x"),
        )
        handler = ORGANIZATION_HANDLERS["synthorg_company_versions_get"]
        response = await handler(
            app_state=fake_app_state,
            arguments={"version_id": "v-1"},
        )
        assert json.loads(response)["domain_code"] == "not_supported"


class TestDepartments:
    async def test_create_and_get(self, fake_app_state: AppState) -> None:
        handler = ORGANIZATION_HANDLERS["synthorg_departments_create"]
        response = await handler(
            app_state=fake_app_state,
            arguments={"name": "Eng", "description": "team"},
            actor=make_test_actor(),
        )
        created = json.loads(response)
        assert created["status"] == "ok"
        dept_id = created["data"]["id"]

        handler_get = ORGANIZATION_HANDLERS["synthorg_departments_get"]
        response_get = await handler_get(
            app_state=fake_app_state,
            arguments={"department_id": dept_id},
        )
        assert json.loads(response_get)["status"] == "ok"

    async def test_list(self, fake_app_state: AppState) -> None:
        handler = ORGANIZATION_HANDLERS["synthorg_departments_list"]
        response = await handler(app_state=fake_app_state, arguments={})
        payload = json.loads(response)
        assert payload["status"] == "ok"
        assert payload["data"] == []

    async def test_delete_requires_guardrails(
        self,
        fake_app_state: AppState,
    ) -> None:
        handler = ORGANIZATION_HANDLERS["synthorg_departments_delete"]
        response = await handler(
            app_state=fake_app_state,
            arguments={"department_id": str(uuid4())},
            actor=make_test_actor(),
        )
        assert json.loads(response)["domain_code"] == "guardrail_violated"

    async def test_delete_emits_admin_op_executed(
        self,
        fake_app_state: AppState,
    ) -> None:
        create = ORGANIZATION_HANDLERS["synthorg_departments_create"]
        created = json.loads(
            await create(
                app_state=fake_app_state,
                arguments={"name": "doomed", "description": "v1"},
                actor=make_test_actor(),
            ),
        )
        dept_id = created["data"]["id"]
        handler = ORGANIZATION_HANDLERS["synthorg_departments_delete"]
        with structlog.testing.capture_logs() as events:
            response = await handler(
                app_state=fake_app_state,
                arguments={
                    "department_id": dept_id,
                    "confirm": True,
                    "reason": "cleanup",
                },
                actor=make_test_actor(),
            )
        assert json.loads(response)["status"] == "ok"
        assert any(
            e.get("event") == MCP_ADMIN_OP_EXECUTED
            and e.get("tool_name") == "synthorg_departments_delete"
            for e in events
        )

    async def test_health(self, fake_app_state: AppState) -> None:
        handler = ORGANIZATION_HANDLERS["synthorg_departments_get_health"]
        response = await handler(
            app_state=fake_app_state,
            arguments={"department_id": str(uuid4())},
        )
        assert json.loads(response)["status"] == "ok"

    async def test_update_patches_existing(
        self,
        fake_app_state: AppState,
    ) -> None:
        create = ORGANIZATION_HANDLERS["synthorg_departments_create"]
        created = json.loads(
            await create(
                app_state=fake_app_state,
                arguments={"name": "original", "description": "v1"},
                actor=make_test_actor(),
            ),
        )
        dept_id = created["data"]["id"]
        update = ORGANIZATION_HANDLERS["synthorg_departments_update"]
        response = await update(
            app_state=fake_app_state,
            arguments={"department_id": dept_id, "name": "renamed"},
            actor=make_test_actor(),
        )
        body = json.loads(response)
        assert body["status"] == "ok"
        assert body["data"]["name"] == "renamed"

    async def test_get_not_found(self, fake_app_state: AppState) -> None:
        handler = ORGANIZATION_HANDLERS["synthorg_departments_get"]
        response = await handler(
            app_state=fake_app_state,
            arguments={"department_id": str(uuid4())},
        )
        assert json.loads(response)["domain_code"] == "not_found"

    async def test_update_not_found(
        self,
        fake_app_state: AppState,
    ) -> None:
        handler = ORGANIZATION_HANDLERS["synthorg_departments_update"]
        response = await handler(
            app_state=fake_app_state,
            arguments={"department_id": str(uuid4()), "name": "ghost"},
            actor=make_test_actor(),
        )
        assert json.loads(response)["domain_code"] == "not_found"


class TestTeams:
    async def test_create_and_get(self, fake_app_state: AppState) -> None:
        handler = ORGANIZATION_HANDLERS["synthorg_teams_create"]
        response = await handler(
            app_state=fake_app_state,
            arguments={"name": "Core"},
            actor=make_test_actor(),
        )
        created = json.loads(response)
        assert created["status"] == "ok"
        team_id = created["data"]["id"]
        get_handler = ORGANIZATION_HANDLERS["synthorg_teams_get"]
        response_get = await get_handler(
            app_state=fake_app_state,
            arguments={"team_id": team_id},
        )
        assert json.loads(response_get)["status"] == "ok"

    async def test_list(self, fake_app_state: AppState) -> None:
        handler = ORGANIZATION_HANDLERS["synthorg_teams_list"]
        response = await handler(app_state=fake_app_state, arguments={})
        payload = json.loads(response)
        assert payload["status"] == "ok"
        assert payload["data"] == []

    async def test_delete_guardrails(self, fake_app_state: AppState) -> None:
        handler = ORGANIZATION_HANDLERS["synthorg_teams_delete"]
        response = await handler(
            app_state=fake_app_state,
            arguments={"team_id": str(uuid4())},
            actor=make_test_actor(),
        )
        assert json.loads(response)["domain_code"] == "guardrail_violated"

    async def test_delete_emits_admin_op_executed(
        self,
        fake_app_state: AppState,
    ) -> None:
        create = ORGANIZATION_HANDLERS["synthorg_teams_create"]
        created = json.loads(
            await create(
                app_state=fake_app_state,
                arguments={"name": "doomed-team"},
                actor=make_test_actor(),
            ),
        )
        team_id = created["data"]["id"]
        handler = ORGANIZATION_HANDLERS["synthorg_teams_delete"]
        with structlog.testing.capture_logs() as events:
            response = await handler(
                app_state=fake_app_state,
                arguments={
                    "team_id": team_id,
                    "confirm": True,
                    "reason": "cleanup",
                },
                actor=make_test_actor(),
            )
        assert json.loads(response)["status"] == "ok"
        assert any(
            e.get("event") == MCP_ADMIN_OP_EXECUTED
            and e.get("tool_name") == "synthorg_teams_delete"
            for e in events
        )

    async def test_update_patches_existing(
        self,
        fake_app_state: AppState,
    ) -> None:
        create = ORGANIZATION_HANDLERS["synthorg_teams_create"]
        created = json.loads(
            await create(
                app_state=fake_app_state,
                arguments={"name": "old-name"},
                actor=make_test_actor(),
            ),
        )
        team_id = created["data"]["id"]
        update = ORGANIZATION_HANDLERS["synthorg_teams_update"]
        response = await update(
            app_state=fake_app_state,
            arguments={"team_id": team_id, "name": "new-name"},
            actor=make_test_actor(),
        )
        body = json.loads(response)
        assert body["status"] == "ok"
        assert body["data"]["name"] == "new-name"

    async def test_get_not_found(self, fake_app_state: AppState) -> None:
        handler = ORGANIZATION_HANDLERS["synthorg_teams_get"]
        response = await handler(
            app_state=fake_app_state,
            arguments={"team_id": str(uuid4())},
        )
        assert json.loads(response)["domain_code"] == "not_found"

    async def test_update_not_found(
        self,
        fake_app_state: AppState,
    ) -> None:
        handler = ORGANIZATION_HANDLERS["synthorg_teams_update"]
        response = await handler(
            app_state=fake_app_state,
            arguments={"team_id": str(uuid4()), "name": "ghost"},
            actor=make_test_actor(),
        )
        assert json.loads(response)["domain_code"] == "not_found"


class TestRoleVersions:
    async def test_list(self, fake_app_state: AppState) -> None:
        handler = ORGANIZATION_HANDLERS["synthorg_role_versions_list"]
        response = await handler(app_state=fake_app_state, arguments={})
        payload = json.loads(response)
        assert payload["status"] == "ok"
        assert payload["data"] == []

    async def test_list_paginates_and_reports_total(
        self,
        fake_role_version: AsyncMock,
        fake_app_state: AppState,
    ) -> None:
        version = SimpleNamespace(model_dump=lambda mode="json": {"id": "rv-1"})
        fake_role_version.list_versions = AsyncMock(return_value=((version,), 7))
        handler = ORGANIZATION_HANDLERS["synthorg_role_versions_list"]
        response = await handler(
            app_state=fake_app_state,
            arguments={"offset": 3, "limit": 2},
        )
        payload = json.loads(response)
        assert payload["status"] == "ok"
        assert payload["pagination"] == {"total": 7, "offset": 3, "limit": 2}
        call = fake_role_version.list_versions.await_args
        assert call.kwargs["offset"] == 3
        assert call.kwargs["limit"] == 2

    async def test_get_not_found(self, fake_app_state: AppState) -> None:
        handler = ORGANIZATION_HANDLERS["synthorg_role_versions_get"]
        response = await handler(
            app_state=fake_app_state,
            arguments={"version_id": "v1"},
        )
        assert json.loads(response)["domain_code"] == "not_found"

    async def test_list_capability_gap(
        self,
        fake_app_state: AppState,
        fake_role_version: AsyncMock,
    ) -> None:
        fake_role_version.list_versions = AsyncMock(
            side_effect=CapabilityNotSupportedError("role_versions_list", "x"),
        )
        handler = ORGANIZATION_HANDLERS["synthorg_role_versions_list"]
        response = await handler(app_state=fake_app_state, arguments={})
        assert json.loads(response)["domain_code"] == "not_supported"

    async def test_get_capability_gap(
        self,
        fake_app_state: AppState,
        fake_role_version: AsyncMock,
    ) -> None:
        fake_role_version.get_version = AsyncMock(
            side_effect=CapabilityNotSupportedError("role_versions_get", "x"),
        )
        handler = ORGANIZATION_HANDLERS["synthorg_role_versions_get"]
        response = await handler(
            app_state=fake_app_state,
            arguments={"version_id": "v1"},
        )
        assert json.loads(response)["domain_code"] == "not_supported"
