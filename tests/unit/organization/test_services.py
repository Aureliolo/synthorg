"""Direct unit tests for the organization facade services.

Covers :class:`DepartmentService`, :class:`TeamService`, and
:class:`RoleVersionService` (happy-path + error-path per method).
:class:`CompanyReadService` delegates to an external mutation service
and is already exercised via the MCP handler tests.
"""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from synthorg.communication.mcp_errors import CapabilityNotSupportedError
from synthorg.core.types import NotBlankStr
from synthorg.organization._team_service import TeamService
from synthorg.organization.services import (
    CompanyReadService,
    DepartmentService,
    RoleVersionService,
)
from synthorg.persistence.department_protocol import DepartmentRepository
from tests._shared import mock_of

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


# ── TeamService ───────────────────────────────────────────────────


class TestTeamService:
    async def test_create_then_get_round_trip(self) -> None:
        service = TeamService()
        created = await service.create_team(
            name=NotBlankStr("platform"),
            actor_id=NotBlankStr("alice"),
            department_id=NotBlankStr("infra"),
        )
        fetched = await service.get_team(NotBlankStr(str(created.id)))
        assert fetched is not None
        assert fetched.name == "platform"
        assert fetched.department_id == "infra"

    async def test_list_returns_newest_first(self) -> None:
        service = TeamService()
        first = await service.create_team(
            name=NotBlankStr("first"),
            actor_id=NotBlankStr("alice"),
        )
        second = await service.create_team(
            name=NotBlankStr("second"),
            actor_id=NotBlankStr("alice"),
        )
        page, total = await service.list_teams()
        assert total == 2
        assert page[0].id == second.id
        assert page[1].id == first.id

    async def test_update_reassigns_department(self) -> None:
        service = TeamService()
        created = await service.create_team(
            name=NotBlankStr("migrators"),
            actor_id=NotBlankStr("alice"),
        )
        updated = await service.update_team(
            team_id=NotBlankStr(str(created.id)),
            actor_id=NotBlankStr("bob"),
            department_id=NotBlankStr("new-home"),
        )
        assert updated is not None
        assert updated.department_id == "new-home"
        assert updated.name == "migrators"

    async def test_update_clears_department_when_none_passed(self) -> None:
        service = TeamService()
        created = await service.create_team(
            name=NotBlankStr("keepers"),
            actor_id=NotBlankStr("alice"),
            department_id=NotBlankStr("legacy"),
        )
        cleared = await service.update_team(
            team_id=NotBlankStr(str(created.id)),
            actor_id=NotBlankStr("bob"),
            department_id=None,
        )
        assert cleared is not None
        assert cleared.department_id is None

    async def test_update_preserves_department_when_unset(self) -> None:
        service = TeamService()
        created = await service.create_team(
            name=NotBlankStr("keepers"),
            actor_id=NotBlankStr("alice"),
            department_id=NotBlankStr("legacy"),
        )
        preserved = await service.update_team(
            team_id=NotBlankStr(str(created.id)),
            actor_id=NotBlankStr("bob"),
            name=NotBlankStr("renamed"),
        )
        assert preserved is not None
        assert preserved.department_id == "legacy"
        assert preserved.name == "renamed"

    async def test_update_missing_returns_none(self) -> None:
        service = TeamService()
        result = await service.update_team(
            team_id=NotBlankStr(str(uuid4())),
            actor_id=NotBlankStr("alice"),
        )
        assert result is None

    async def test_update_invalid_uuid_returns_none(self) -> None:
        service = TeamService()
        result = await service.update_team(
            team_id=NotBlankStr("nope"),
            actor_id=NotBlankStr("alice"),
        )
        assert result is None

    async def test_delete_present(self) -> None:
        service = TeamService()
        created = await service.create_team(
            name=NotBlankStr("doomed"),
            actor_id=NotBlankStr("alice"),
        )
        removed = await service.delete_team(
            team_id=NotBlankStr(str(created.id)),
            actor_id=NotBlankStr("alice"),
            reason=NotBlankStr("cleanup"),
        )
        assert removed is True

    async def test_delete_absent_returns_false(self) -> None:
        service = TeamService()
        removed = await service.delete_team(
            team_id=NotBlankStr(str(uuid4())),
            actor_id=NotBlankStr("alice"),
            reason=NotBlankStr("cleanup"),
        )
        assert removed is False

    async def test_delete_invalid_uuid_returns_false(self) -> None:
        service = TeamService()
        removed = await service.delete_team(
            team_id=NotBlankStr("bad"),
            actor_id=NotBlankStr("alice"),
            reason=NotBlankStr("cleanup"),
        )
        assert removed is False

    async def test_get_invalid_uuid_returns_none(self) -> None:
        service = TeamService()
        assert await service.get_team(NotBlankStr("bad")) is None


# ── CompanyReadService capability paths ────────────────────────────


class TestCompanyReadService:
    async def test_list_departments_capability_gap(self) -> None:
        class _NoLister:
            pass

        service = CompanyReadService(org_mutation=_NoLister())  # type: ignore[arg-type]
        with pytest.raises(CapabilityNotSupportedError):
            await service.list_departments()

    async def test_list_versions_capability_gap(self) -> None:
        class _NoVersions:
            pass

        service = CompanyReadService(org_mutation=_NoVersions())  # type: ignore[arg-type]
        with pytest.raises(CapabilityNotSupportedError):
            await service.list_versions()

    async def test_get_version_capability_gap(self) -> None:
        class _NoGet:
            pass

        service = CompanyReadService(org_mutation=_NoGet())  # type: ignore[arg-type]
        with pytest.raises(CapabilityNotSupportedError):
            await service.get_version(NotBlankStr("v1"))


# ── RoleVersionService capability paths ────────────────────────────


class TestRoleVersionService:
    async def test_list_versions_capability_gap_unwired(self) -> None:
        service = RoleVersionService()
        with pytest.raises(CapabilityNotSupportedError):
            await service.list_versions()

    async def test_get_version_capability_gap_unwired(self) -> None:
        service = RoleVersionService()
        with pytest.raises(CapabilityNotSupportedError):
            await service.get_version(NotBlankStr("v1"))

    async def test_list_versions_capability_gap_missing_method(self) -> None:
        class _PartialOrg:
            pass

        service = RoleVersionService(org_mutation=_PartialOrg())  # type: ignore[arg-type]
        with pytest.raises(CapabilityNotSupportedError):
            await service.list_versions()

    async def test_get_version_capability_gap_missing_method(self) -> None:
        class _PartialOrg:
            pass

        service = RoleVersionService(org_mutation=_PartialOrg())  # type: ignore[arg-type]
        with pytest.raises(CapabilityNotSupportedError):
            await service.get_version(NotBlankStr("v1"))
