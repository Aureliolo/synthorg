"""Direct unit tests for the organization facade services.

Covers :class:`DepartmentService`, :class:`TeamService`, and
:class:`RoleVersionService` (happy-path + error-path per method).
:class:`CompanyReadService` delegates to an external mutation service
and is already exercised via the MCP handler tests.
"""

import json
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from synthorg.api.services.org_mutations import OrgMutationService
from synthorg.communication.mcp_errors import CapabilityNotSupportedError
from synthorg.core.domain_errors import ConflictError, NotFoundError
from synthorg.core.types import NotBlankStr
from synthorg.organization._team_service import TeamService
from synthorg.organization.services import (
    CompanyReadService,
    DepartmentService,
    RoleVersionService,
)
from synthorg.persistence.department_protocol import DepartmentRepository
from synthorg.settings.resolver import ConfigResolver
from tests._shared import FakeSettingsService, make_app_state, mock_of

pytestmark = pytest.mark.unit


# ── DepartmentService ──────────────────────────────────────────────


class TestDepartmentService:
    async def test_create_then_get_round_trip(self) -> None:
        service = DepartmentService()
        created = await service.create_department(
            name=NotBlankStr("engineering"),
            description=NotBlankStr("Builds the product"),
            actor_id=NotBlankStr("alice"),
        )
        fetched = await service.get_department(NotBlankStr(str(created.id)))
        assert fetched is not None
        assert fetched.name == "engineering"

    async def test_list_returns_newest_first(self) -> None:
        service = DepartmentService()
        first = await service.create_department(
            name=NotBlankStr("alpha"),
            description=NotBlankStr("first"),
            actor_id=NotBlankStr("alice"),
        )
        second = await service.create_department(
            name=NotBlankStr("beta"),
            description=NotBlankStr("second"),
            actor_id=NotBlankStr("alice"),
        )
        page, total = await service.list_departments()
        assert total == 2
        assert page[0].id == second.id
        assert page[1].id == first.id

    async def test_update_partial_patch(self) -> None:
        service = DepartmentService()
        created = await service.create_department(
            name=NotBlankStr("old"),
            description=NotBlankStr("initial"),
            actor_id=NotBlankStr("alice"),
        )
        updated = await service.update_department(
            department_id=NotBlankStr(str(created.id)),
            actor_id=NotBlankStr("bob"),
            name=NotBlankStr("new"),
        )
        assert updated is not None
        assert updated.name == "new"
        assert updated.description == "initial"

    async def test_update_missing_returns_none(self) -> None:
        service = DepartmentService()
        result = await service.update_department(
            department_id=NotBlankStr(str(uuid4())),
            actor_id=NotBlankStr("alice"),
            name=NotBlankStr("ghost"),
        )
        assert result is None

    async def test_update_invalid_uuid_returns_none(self) -> None:
        service = DepartmentService()
        result = await service.update_department(
            department_id=NotBlankStr("not-a-uuid"),
            actor_id=NotBlankStr("alice"),
        )
        assert result is None

    async def test_update_durable_save_failure_leaves_cache_unchanged(self) -> None:
        # A failed durable write must not leave the in-memory cache ahead of
        # the store: the mutation is applied to a copy and only committed after
        # save() succeeds. The create save succeeds; the update save raises.
        repo = mock_of[DepartmentRepository](
            save=AsyncMock(side_effect=[None, RuntimeError("db down")]),
        )
        service = DepartmentService(repo=repo)
        created = await service.create_department(
            name=NotBlankStr("old"),
            description=NotBlankStr("initial"),
            actor_id=NotBlankStr("alice"),
        )
        with pytest.raises(RuntimeError, match="db down"):
            await service.update_department(
                department_id=NotBlankStr(str(created.id)),
                actor_id=NotBlankStr("bob"),
                name=NotBlankStr("new"),
            )
        fetched = await service.get_department(NotBlankStr(str(created.id)))
        assert fetched is not None
        assert fetched.name == "old"
        assert fetched.description == "initial"

    async def test_delete_returns_true_when_present(self) -> None:
        service = DepartmentService()
        created = await service.create_department(
            name=NotBlankStr("doomed"),
            description=NotBlankStr("desc"),
            actor_id=NotBlankStr("alice"),
        )
        removed = await service.delete_department(
            department_id=NotBlankStr(str(created.id)),
            actor_id=NotBlankStr("alice"),
            reason=NotBlankStr("cleanup"),
        )
        assert removed is True
        assert await service.get_department(NotBlankStr(str(created.id))) is None

    async def test_delete_returns_false_when_absent(self) -> None:
        service = DepartmentService()
        removed = await service.delete_department(
            department_id=NotBlankStr(str(uuid4())),
            actor_id=NotBlankStr("alice"),
            reason=NotBlankStr("cleanup"),
        )
        assert removed is False

    async def test_delete_invalid_uuid_returns_false(self) -> None:
        service = DepartmentService()
        removed = await service.delete_department(
            department_id=NotBlankStr("bad-uuid"),
            actor_id=NotBlankStr("alice"),
            reason=NotBlankStr("cleanup"),
        )
        assert removed is False

    async def test_get_invalid_uuid_returns_none(self) -> None:
        service = DepartmentService()
        result = await service.get_department(NotBlankStr("not-a-uuid"))
        assert result is None

    async def test_get_health_known_department(self) -> None:
        service = DepartmentService()
        created = await service.create_department(
            name=NotBlankStr("ops"),
            description=NotBlankStr("desc"),
            actor_id=NotBlankStr("alice"),
        )
        health = await service.get_health(NotBlankStr(str(created.id)))
        assert health["status"] == "healthy"

    async def test_get_health_unknown_department(self) -> None:
        service = DepartmentService()
        health = await service.get_health(NotBlankStr(str(uuid4())))
        assert health["status"] == "unknown"
        assert health["reason"] == "not_found"


# ── TeamService (settings-backed, keyed by (department, name)) ──────


def _team_service(*departments: dict[str, object]) -> TeamService:
    """Build a settings-backed ``TeamService`` seeded with *departments*."""
    settings = FakeSettingsService(
        {("company", "departments"): json.dumps(list(departments))}
    )
    app_state = make_app_state(settings_service=settings)
    return TeamService(app_state=app_state)


def _dept(name: str, teams: list[dict[str, object]] | None = None) -> dict[str, object]:
    """Build a department dict with an optional teams list."""
    return {"name": name, "teams": teams or []}


class TestTeamService:
    async def test_create_then_get_round_trip(self) -> None:
        service = _team_service(_dept("engineering"))
        created = await service.create_team(
            department=NotBlankStr("engineering"),
            name=NotBlankStr("backend"),
            lead=NotBlankStr("alice"),
            actor_id=NotBlankStr("alice"),
            members=["bob", "carol"],
        )
        assert created["department"] == "engineering"
        assert created["name"] == "backend"
        assert created["lead"] == "alice"
        assert created["members"] == ["bob", "carol"]
        fetched = await service.get_team(
            department=NotBlankStr("engineering"),
            team_name=NotBlankStr("backend"),
        )
        assert fetched == created

    async def test_create_in_missing_department_raises(self) -> None:
        service = _team_service(_dept("engineering"))
        with pytest.raises(NotFoundError):
            await service.create_team(
                department=NotBlankStr("marketing"),
                name=NotBlankStr("brand"),
                lead=NotBlankStr("alice"),
                actor_id=NotBlankStr("alice"),
            )

    async def test_create_duplicate_name_conflicts(self) -> None:
        service = _team_service(
            _dept("engineering", [{"name": "backend", "lead": "alice"}])
        )
        with pytest.raises(ConflictError):
            await service.create_team(
                department=NotBlankStr("engineering"),
                name=NotBlankStr("Backend"),
                lead=NotBlankStr("bob"),
                actor_id=NotBlankStr("bob"),
            )

    async def test_list_sorts_by_department_then_name(self) -> None:
        service = _team_service(
            _dept("platform", [{"name": "sre", "lead": "z"}]),
            _dept(
                "engineering",
                [{"name": "frontend", "lead": "y"}, {"name": "backend", "lead": "x"}],
            ),
        )
        page, total = await service.list_teams()
        assert total == 3
        assert [(t["department"], t["name"]) for t in page] == [
            ("engineering", "backend"),
            ("engineering", "frontend"),
            ("platform", "sre"),
        ]

    async def test_list_paginates(self) -> None:
        service = _team_service(
            _dept(
                "engineering",
                [
                    {"name": "backend", "lead": "x"},
                    {"name": "frontend", "lead": "y"},
                    {"name": "mobile", "lead": "z"},
                ],
            )
        )
        page, total = await service.list_teams(offset=1, limit=1)
        assert total == 3
        assert [t["name"] for t in page] == ["frontend"]

    async def test_update_rename(self) -> None:
        service = _team_service(
            _dept("engineering", [{"name": "backend", "lead": "alice"}])
        )
        updated = await service.update_team(
            department=NotBlankStr("engineering"),
            team_name=NotBlankStr("backend"),
            actor_id=NotBlankStr("bob"),
            name=NotBlankStr("core"),
        )
        assert updated is not None
        assert updated["name"] == "core"
        assert updated["lead"] == "alice"
        assert (
            await service.get_team(
                department=NotBlankStr("engineering"),
                team_name=NotBlankStr("backend"),
            )
            is None
        )

    async def test_update_change_lead_and_members(self) -> None:
        service = _team_service(
            _dept("engineering", [{"name": "backend", "lead": "alice"}])
        )
        updated = await service.update_team(
            department=NotBlankStr("engineering"),
            team_name=NotBlankStr("backend"),
            actor_id=NotBlankStr("bob"),
            lead=NotBlankStr("carol"),
            members=["dave"],
        )
        assert updated is not None
        assert updated["lead"] == "carol"
        assert updated["members"] == ["dave"]

    async def test_update_missing_team_returns_none(self) -> None:
        service = _team_service(_dept("engineering"))
        result = await service.update_team(
            department=NotBlankStr("engineering"),
            team_name=NotBlankStr("ghost"),
            actor_id=NotBlankStr("alice"),
            lead=NotBlankStr("bob"),
        )
        assert result is None

    async def test_update_missing_department_returns_none(self) -> None:
        service = _team_service(_dept("engineering"))
        result = await service.update_team(
            department=NotBlankStr("marketing"),
            team_name=NotBlankStr("brand"),
            actor_id=NotBlankStr("alice"),
            lead=NotBlankStr("bob"),
        )
        assert result is None

    async def test_update_rename_conflict(self) -> None:
        service = _team_service(
            _dept(
                "engineering",
                [{"name": "backend", "lead": "a"}, {"name": "frontend", "lead": "b"}],
            )
        )
        with pytest.raises(ConflictError):
            await service.update_team(
                department=NotBlankStr("engineering"),
                team_name=NotBlankStr("backend"),
                actor_id=NotBlankStr("bob"),
                name=NotBlankStr("Frontend"),
            )

    async def test_delete_present(self) -> None:
        service = _team_service(
            _dept("engineering", [{"name": "backend", "lead": "alice"}])
        )
        removed = await service.delete_team(
            department=NotBlankStr("engineering"),
            team_name=NotBlankStr("backend"),
            actor_id=NotBlankStr("alice"),
            reason=NotBlankStr("cleanup"),
        )
        assert removed is True
        assert (
            await service.get_team(
                department=NotBlankStr("engineering"),
                team_name=NotBlankStr("backend"),
            )
            is None
        )

    async def test_delete_absent_team_returns_false(self) -> None:
        service = _team_service(_dept("engineering"))
        removed = await service.delete_team(
            department=NotBlankStr("engineering"),
            team_name=NotBlankStr("ghost"),
            actor_id=NotBlankStr("alice"),
            reason=NotBlankStr("cleanup"),
        )
        assert removed is False

    async def test_delete_absent_department_returns_false(self) -> None:
        service = _team_service(_dept("engineering"))
        removed = await service.delete_team(
            department=NotBlankStr("marketing"),
            team_name=NotBlankStr("brand"),
            actor_id=NotBlankStr("alice"),
            reason=NotBlankStr("cleanup"),
        )
        assert removed is False

    async def test_get_missing_department_returns_none(self) -> None:
        service = _team_service(_dept("engineering"))
        assert (
            await service.get_team(
                department=NotBlankStr("marketing"),
                team_name=NotBlankStr("brand"),
            )
            is None
        )


# ── CompanyReadService (durable-source reads) ──────────────────────


def _company_read_service(
    *,
    company_versions: object | None = None,
) -> CompanyReadService:
    """Build a CompanyReadService over a resolver stub with no company data."""
    resolver = mock_of[ConfigResolver](
        get_str=AsyncMock(return_value="Acme"),
        get_agents=AsyncMock(return_value=()),
        get_departments=AsyncMock(return_value=()),
    )
    return CompanyReadService(
        org_mutation=mock_of[OrgMutationService](),
        config_resolver=resolver,
        company_versions=company_versions,  # type: ignore[arg-type]
    )


class TestCompanyReadService:
    async def test_get_company_reads_config_resolver(self) -> None:
        service = _company_read_service()
        company = await service.get_company()
        assert company["company_name"] == "Acme"
        assert company["agents"] == []
        assert company["departments"] == []

    async def test_list_departments_reads_config_resolver(self) -> None:
        service = _company_read_service()
        assert await service.list_departments() == ()

    async def test_list_versions_without_repo_raises(self) -> None:
        service = _company_read_service(company_versions=None)
        with pytest.raises(CapabilityNotSupportedError):
            await service.list_versions()

    async def test_get_version_without_repo_raises(self) -> None:
        service = _company_read_service(company_versions=None)
        with pytest.raises(CapabilityNotSupportedError):
            await service.get_version(NotBlankStr("1"))


# ── RoleVersionService (durable-source reads) ──────────────────────


class TestRoleVersionService:
    async def test_list_versions_without_repo_raises(self) -> None:
        service = RoleVersionService()
        with pytest.raises(CapabilityNotSupportedError):
            await service.list_versions(role_name=NotBlankStr("engineer"))

    async def test_get_version_without_repo_raises(self) -> None:
        service = RoleVersionService()
        with pytest.raises(CapabilityNotSupportedError):
            await service.get_version(
                role_name=NotBlankStr("engineer"),
                version_id=NotBlankStr("1"),
            )
