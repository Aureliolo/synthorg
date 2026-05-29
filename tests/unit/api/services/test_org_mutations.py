"""Tests for the OrgMutationService."""

from collections.abc import AsyncGenerator

import pytest

import synthorg.settings.definitions  # noqa: F401 -- trigger registration
from synthorg.api.dto_org import (
    CreateAgentOrgRequest,
    CreateDepartmentRequest,
    ReorderAgentsRequest,
    ReorderDepartmentsRequest,
    UpdateAgentOrgRequest,
    UpdateCompanyRequest,
    UpdateDepartmentRequest,
)
from synthorg.api.services.org_mutations import OrgMutationService
from synthorg.config.schema import RootConfig
from synthorg.core.domain_errors import ConflictError, NotFoundError, ValidationError
from synthorg.core.enums import AutonomyLevel, SeniorityLevel
from synthorg.settings.registry import get_registry
from synthorg.settings.service import SettingsService
from tests.unit.api.fakes import FakePersistenceBackend

# Hardcoded valid Fernet key for settings encryption.
_TEST_SETTINGS_KEY = "lKzZcMznksIF8A_2HFFUnKxhxhz9_bxTvVJoZ6mvZrk="


@pytest.fixture(autouse=True)
def _set_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SYNTHORG_SETTINGS_KEY", _TEST_SETTINGS_KEY)


@pytest.fixture
async def persistence() -> AsyncGenerator[FakePersistenceBackend]:
    backend = FakePersistenceBackend()
    await backend.connect()
    yield backend
    await backend.disconnect()


@pytest.fixture
def config() -> RootConfig:
    return RootConfig(company_name="test-company")


@pytest.fixture
def settings_service(
    persistence: FakePersistenceBackend,
    config: RootConfig,
) -> SettingsService:
    return SettingsService(
        repository=persistence.settings,
        registry=get_registry(),
    )


@pytest.fixture
def service(
    settings_service: SettingsService,
    config: RootConfig,
) -> OrgMutationService:
    from synthorg.settings.resolver import ConfigResolver

    resolver = ConfigResolver(
        settings_service=settings_service,
        config=config,
    )
    return OrgMutationService(
        settings_service=settings_service,
        config_resolver=resolver,
    )


# ── Department CRUD ──────────────────────────────────────────


@pytest.mark.unit
class TestCreateDepartment:
    async def test_create_department_happy_path(
        self,
        service: OrgMutationService,
    ) -> None:
        req = CreateDepartmentRequest(
            name="engineering",
            budget_percent=30.0,
        )
        dept = await service.create_department(req)
        assert dept.name == "engineering"
        assert dept.budget_percent == 30.0

    async def test_create_department_duplicate_name_409(
        self,
        service: OrgMutationService,
    ) -> None:
        req = CreateDepartmentRequest(
            name="engineering",
        )
        await service.create_department(req)
        with pytest.raises(ConflictError, match="already exists"):
            await service.create_department(req)

    async def test_create_department_duplicate_case_insensitive(
        self,
        service: OrgMutationService,
    ) -> None:
        req1 = CreateDepartmentRequest(
            name="engineering",
        )
        await service.create_department(req1)
        req2 = CreateDepartmentRequest(
            name="Engineering",
        )
        with pytest.raises(ConflictError, match="already exists"):
            await service.create_department(req2)


@pytest.mark.unit
class TestUpdateDepartment:
    async def test_update_department_happy_path(
        self,
        service: OrgMutationService,
    ) -> None:
        create_req = CreateDepartmentRequest(
            name="engineering",
            budget_percent=20.0,
        )
        await service.create_department(create_req)

        update_req = UpdateDepartmentRequest(budget_percent=35.0)
        updated = await service.update_department("engineering", update_req)
        assert updated.budget_percent == 35.0

    async def test_update_department_not_found_404(
        self,
        service: OrgMutationService,
    ) -> None:
        update_req = UpdateDepartmentRequest(budget_percent=10.0)
        with pytest.raises(NotFoundError):
            await service.update_department("nonexistent", update_req)


@pytest.mark.unit
class TestDeleteDepartment:
    async def test_delete_department_happy_path(
        self,
        service: OrgMutationService,
        settings_service: SettingsService,
    ) -> None:
        create_req = CreateDepartmentRequest(
            name="engineering",
        )
        await service.create_department(create_req)
        await service.delete_department("engineering")

        # Verify it's gone
        with pytest.raises(NotFoundError):
            await service.update_department(
                "engineering",
                UpdateDepartmentRequest(budget_percent=10.0),
            )

    async def test_delete_department_not_found_404(
        self,
        service: OrgMutationService,
    ) -> None:
        with pytest.raises(NotFoundError):
            await service.delete_department("nonexistent")

    async def test_delete_department_with_agents_409(
        self,
        service: OrgMutationService,
    ) -> None:
        # Create department, then create an agent in it
        await service.create_department(
            CreateDepartmentRequest(
                name="engineering",
            ),
        )
        await service.create_agent(
            CreateAgentOrgRequest(
                name="alice",
                role="developer",
                department="engineering",
                level=SeniorityLevel.MID,
            ),
        )
        with pytest.raises(ConflictError, match="agents attached"):
            await service.delete_department("engineering")


@pytest.mark.unit
class TestReorderDepartments:
    async def test_reorder_departments_happy_path(
        self,
        service: OrgMutationService,
    ) -> None:
        await service.create_department(
            CreateDepartmentRequest(name="alpha"),
        )
        await service.create_department(
            CreateDepartmentRequest(name="beta"),
        )
        reordered = await service.reorder_departments(
            ReorderDepartmentsRequest(department_names=("beta", "alpha")),
        )
        assert tuple(d.name for d in reordered) == ("beta", "alpha")

    async def test_reorder_departments_incomplete_permutation_422(
        self,
        service: OrgMutationService,
    ) -> None:
        await service.create_department(
            CreateDepartmentRequest(name="alpha"),
        )
        await service.create_department(
            CreateDepartmentRequest(name="beta"),
        )
        with pytest.raises(ValidationError, match="exact permutation"):
            await service.reorder_departments(
                ReorderDepartmentsRequest(department_names=("alpha",)),
            )

    async def test_reorder_departments_extra_name_422(
        self,
        service: OrgMutationService,
    ) -> None:
        await service.create_department(
            CreateDepartmentRequest(name="alpha"),
        )
        with pytest.raises(ValidationError, match="exact permutation"):
            await service.reorder_departments(
                ReorderDepartmentsRequest(
                    department_names=("alpha", "gamma"),
                ),
            )


# ── Agent CRUD ────────────────────────────────────────────────


@pytest.mark.unit
class TestCreateAgent:
    async def test_create_agent_happy_path(
        self,
        service: OrgMutationService,
    ) -> None:
        await service.create_department(
            CreateDepartmentRequest(name="eng"),
        )
        req = CreateAgentOrgRequest(
            name="alice",
            role="developer",
            department="eng",
            level=SeniorityLevel.SENIOR,
        )
        agent = await service.create_agent(req)
        assert agent.name == "alice"
        assert agent.role == "developer"
        assert agent.department == "eng"
        assert agent.level == SeniorityLevel.SENIOR

    async def test_create_agent_nonexistent_department_422(
        self,
        service: OrgMutationService,
    ) -> None:
        req = CreateAgentOrgRequest(
            name="alice",
            role="developer",
            department="nonexistent",
            level=SeniorityLevel.MID,
        )
        with pytest.raises(ValidationError, match="does not exist"):
            await service.create_agent(req)

    async def test_create_agent_duplicate_name_409(
        self,
        service: OrgMutationService,
    ) -> None:
        await service.create_department(
            CreateDepartmentRequest(name="eng"),
        )
        req = CreateAgentOrgRequest(
            name="alice",
            role="developer",
            department="eng",
            level=SeniorityLevel.MID,
        )
        await service.create_agent(req)
        with pytest.raises(ConflictError, match="already exists"):
            await service.create_agent(req)


@pytest.mark.unit
class TestUpdateAgent:
    async def test_update_agent_happy_path(
        self,
        service: OrgMutationService,
    ) -> None:
        await service.create_department(
            CreateDepartmentRequest(name="eng"),
        )
        await service.create_agent(
            CreateAgentOrgRequest(
                name="alice",
                role="developer",
                department="eng",
                level=SeniorityLevel.MID,
            ),
        )
        updated = await service.update_agent(
            "alice",
            UpdateAgentOrgRequest(level=SeniorityLevel.SENIOR),
        )
        assert updated.level == SeniorityLevel.SENIOR

    async def test_update_agent_not_found_404(
        self,
        service: OrgMutationService,
    ) -> None:
        with pytest.raises(NotFoundError):
            await service.update_agent(
                "nonexistent",
                UpdateAgentOrgRequest(level=SeniorityLevel.SENIOR),
            )

    async def test_update_agent_move_to_nonexistent_dept_422(
        self,
        service: OrgMutationService,
    ) -> None:
        await service.create_department(
            CreateDepartmentRequest(name="eng"),
        )
        await service.create_agent(
            CreateAgentOrgRequest(
                name="alice",
                role="developer",
                department="eng",
                level=SeniorityLevel.MID,
            ),
        )
        with pytest.raises(ValidationError, match="does not exist"):
            await service.update_agent(
                "alice",
                UpdateAgentOrgRequest(department="nonexistent"),
            )


@pytest.mark.unit
class TestDeleteAgent:
    async def test_delete_agent_happy_path(
        self,
        service: OrgMutationService,
    ) -> None:
        await service.create_department(
            CreateDepartmentRequest(name="eng"),
        )
        await service.create_agent(
            CreateAgentOrgRequest(
                name="alice",
                role="developer",
                department="eng",
                level=SeniorityLevel.MID,
            ),
        )
        await service.delete_agent("alice")
        with pytest.raises(NotFoundError):
            await service.update_agent(
                "alice",
                UpdateAgentOrgRequest(level=SeniorityLevel.SENIOR),
            )

    async def test_delete_agent_not_found_404(
        self,
        service: OrgMutationService,
    ) -> None:
        with pytest.raises(NotFoundError):
            await service.delete_agent("nonexistent")

    async def test_delete_c_suite_agent_409(
        self,
        service: OrgMutationService,
    ) -> None:
        await service.create_department(
            CreateDepartmentRequest(name="exec"),
        )
        await service.create_agent(
            CreateAgentOrgRequest(
                name="chief",
                role="ceo",
                department="exec",
                level=SeniorityLevel.C_SUITE,
            ),
        )
        with pytest.raises(ConflictError, match="CEO"):
            await service.delete_agent("chief")


@pytest.mark.unit
class TestReorderAgents:
    async def test_reorder_agents_happy_path(
        self,
        service: OrgMutationService,
    ) -> None:
        await service.create_department(
            CreateDepartmentRequest(name="eng"),
        )
        await service.create_agent(
            CreateAgentOrgRequest(
                name="alice",
                role="dev",
                department="eng",
                level=SeniorityLevel.MID,
            ),
        )
        await service.create_agent(
            CreateAgentOrgRequest(
                name="bob",
                role="dev",
                department="eng",
                level=SeniorityLevel.MID,
            ),
        )
        reordered = await service.reorder_agents(
            "eng",
            ReorderAgentsRequest(agent_names=("bob", "alice")),
        )
        assert tuple(a.name for a in reordered) == ("bob", "alice")

    async def test_reorder_agents_wrong_department_422(
        self,
        service: OrgMutationService,
    ) -> None:
        with pytest.raises(NotFoundError):
            await service.reorder_agents(
                "nonexistent",
                ReorderAgentsRequest(agent_names=("alice",)),
            )

    async def test_reorder_agents_incomplete_permutation_422(
        self,
        service: OrgMutationService,
    ) -> None:
        await service.create_department(
            CreateDepartmentRequest(name="eng"),
        )
        await service.create_agent(
            CreateAgentOrgRequest(
                name="alice",
                role="dev",
                department="eng",
                level=SeniorityLevel.MID,
            ),
        )
        await service.create_agent(
            CreateAgentOrgRequest(
                name="bob",
                role="dev",
                department="eng",
                level=SeniorityLevel.MID,
            ),
        )
        with pytest.raises(ValidationError, match="exact permutation"):
            await service.reorder_agents(
                "eng",
                ReorderAgentsRequest(agent_names=("alice",)),
            )


# ── Company update ─────────────────────────────────────────────


@pytest.mark.unit
class TestUpdateCompany:
    async def test_update_company_name(
        self,
        service: OrgMutationService,
    ) -> None:
        result, etag = await service.update_company(
            UpdateCompanyRequest(company_name="New Name"),
        )
        assert result["company_name"] == "New Name"
        assert etag.startswith('"')
        assert etag.endswith('"')

    async def test_update_company_autonomy(
        self,
        service: OrgMutationService,
    ) -> None:
        result, _etag = await service.update_company(
            UpdateCompanyRequest(autonomy_level=AutonomyLevel.SUPERVISED),
        )
        assert result["autonomy_level"] == "supervised"
