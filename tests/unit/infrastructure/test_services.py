"""Direct unit tests for infrastructure facade services.

Covers :class:`ProjectFacadeService`, :class:`RequestsFacadeService`,
:class:`TemplatePackFacadeService`, :class:`SetupFacadeService`, and
:class:`SimulationFacadeService` (happy-path + error-path per method).

Primitives-backed facades (settings, providers, backup, users, audit,
events, integration health) are already exercised via the MCP handler
tests (``tests/unit/meta/mcp/test_handlers_infrastructure.py``).
"""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from synthorg.communication.mcp_errors import CapabilityNotSupportedError
from synthorg.core.types import NotBlankStr
from synthorg.infrastructure.services import (
    ProjectFacadeService,
    RequestsFacadeService,
    SetupFacadeService,
    SimulationFacadeService,
    TemplatePackFacadeService,
)

pytestmark = pytest.mark.unit


# ── ProjectFacadeService ───────────────────────────────────────────


class TestProjectFacadeService:
    async def test_create_then_get_round_trip(self) -> None:
        service = ProjectFacadeService()
        created = await service.create_project(
            name=NotBlankStr("alpha"),
            description=NotBlankStr("alpha project"),
            actor_id=NotBlankStr("alice"),
        )
        fetched = await service.get_project(NotBlankStr(str(created.id)))
        assert fetched is not None
        assert fetched.name == "alpha"

    async def test_list_returns_newest_first(self) -> None:
        service = ProjectFacadeService()
        first = await service.create_project(
            name=NotBlankStr("first"),
            description=NotBlankStr("d1"),
            actor_id=NotBlankStr("alice"),
        )
        second = await service.create_project(
            name=NotBlankStr("second"),
            description=NotBlankStr("d2"),
            actor_id=NotBlankStr("alice"),
        )
        page, total = await service.list_projects()
        assert total == 2
        assert page[0].id == second.id
        assert page[1].id == first.id

    async def test_update_patches_fields(self) -> None:
        service = ProjectFacadeService()
        created = await service.create_project(
            name=NotBlankStr("original"),
            description=NotBlankStr("d"),
            actor_id=NotBlankStr("alice"),
        )
        updated = await service.update_project(
            project_id=NotBlankStr(str(created.id)),
            actor_id=NotBlankStr("bob"),
            name=NotBlankStr("patched"),
            metadata={"stage": "beta"},
        )
        assert updated is not None
        assert updated.name == "patched"
        assert updated.metadata == {"stage": "beta"}

    async def test_update_invalid_uuid_returns_none(self) -> None:
        service = ProjectFacadeService()
        assert (
            await service.update_project(
                project_id=NotBlankStr("bad"),
                actor_id=NotBlankStr("alice"),
            )
            is None
        )

    async def test_update_missing_returns_none(self) -> None:
        service = ProjectFacadeService()
        assert (
            await service.update_project(
                project_id=NotBlankStr(str(uuid4())),
                actor_id=NotBlankStr("alice"),
            )
            is None
        )

    async def test_delete_present(self) -> None:
        service = ProjectFacadeService()
        created = await service.create_project(
            name=NotBlankStr("doomed"),
            description=NotBlankStr("d"),
            actor_id=NotBlankStr("alice"),
        )
        removed = await service.delete_project(
            project_id=NotBlankStr(str(created.id)),
            actor_id=NotBlankStr("alice"),
            reason=NotBlankStr("cleanup"),
        )
        assert removed is True
        assert await service.get_project(NotBlankStr(str(created.id))) is None

    async def test_delete_absent_returns_false(self) -> None:
        service = ProjectFacadeService()
        removed = await service.delete_project(
            project_id=NotBlankStr(str(uuid4())),
            actor_id=NotBlankStr("alice"),
            reason=NotBlankStr("cleanup"),
        )
        assert removed is False

    async def test_delete_invalid_uuid_returns_false(self) -> None:
        service = ProjectFacadeService()
        removed = await service.delete_project(
            project_id=NotBlankStr("bad"),
            actor_id=NotBlankStr("alice"),
            reason=NotBlankStr("cleanup"),
        )
        assert removed is False

    async def test_get_invalid_uuid_returns_none(self) -> None:
        service = ProjectFacadeService()
        assert await service.get_project(NotBlankStr("bad")) is None


# ── RequestsFacadeService ──────────────────────────────────────────


class TestRequestsFacadeService:
    async def test_create_then_get_round_trip(self) -> None:
        service = RequestsFacadeService()
        created = await service.create_request(
            title=NotBlankStr("add-feature"),
            body=NotBlankStr("Please add X"),
            requested_by=NotBlankStr("alice"),
        )
        fetched = await service.get_request(NotBlankStr(str(created.id)))
        assert fetched is not None
        assert fetched.title == "add-feature"

    async def test_list_is_newest_first(self) -> None:
        service = RequestsFacadeService()
        first = await service.create_request(
            title=NotBlankStr("a"),
            body=NotBlankStr("b"),
            requested_by=NotBlankStr("alice"),
        )
        second = await service.create_request(
            title=NotBlankStr("b"),
            body=NotBlankStr("b"),
            requested_by=NotBlankStr("alice"),
        )
        page, total = await service.list_requests()
        assert total == 2
        assert page[0].id == second.id
        assert page[1].id == first.id

    async def test_get_invalid_uuid_returns_none(self) -> None:
        service = RequestsFacadeService()
        assert await service.get_request(NotBlankStr("bad")) is None


# ── TemplatePackFacadeService ──────────────────────────────────────


class TestTemplatePackFacadeService:
    async def test_install_then_get(self) -> None:
        service = TemplatePackFacadeService()
        created = await service.install_pack(
            name=NotBlankStr("starter"),
            version=NotBlankStr("1.0.0"),
            actor_id=NotBlankStr("alice"),
        )
        fetched = await service.get_pack(NotBlankStr(str(created.id)))
        assert fetched is not None
        assert fetched.name == "starter"
        assert fetched.version == "1.0.0"

    async def test_uninstall_present(self) -> None:
        service = TemplatePackFacadeService()
        created = await service.install_pack(
            name=NotBlankStr("x"),
            version=NotBlankStr("0.0.1"),
            actor_id=NotBlankStr("alice"),
        )
        removed = await service.uninstall_pack(
            pack_id=NotBlankStr(str(created.id)),
            actor_id=NotBlankStr("alice"),
            reason=NotBlankStr("cleanup"),
        )
        assert removed is True

    async def test_uninstall_absent(self) -> None:
        service = TemplatePackFacadeService()
        removed = await service.uninstall_pack(
            pack_id=NotBlankStr(str(uuid4())),
            actor_id=NotBlankStr("alice"),
            reason=NotBlankStr("cleanup"),
        )
        assert removed is False

    async def test_uninstall_invalid_uuid(self) -> None:
        service = TemplatePackFacadeService()
        removed = await service.uninstall_pack(
            pack_id=NotBlankStr("bad"),
            actor_id=NotBlankStr("alice"),
            reason=NotBlankStr("cleanup"),
        )
        assert removed is False

    async def test_get_invalid_uuid(self) -> None:
        service = TemplatePackFacadeService()
        assert await service.get_pack(NotBlankStr("bad")) is None

    async def test_list_is_newest_first(self) -> None:
        service = TemplatePackFacadeService()
        first = await service.install_pack(
            name=NotBlankStr("a"),
            version=NotBlankStr("1"),
            actor_id=NotBlankStr("alice"),
        )
        second = await service.install_pack(
            name=NotBlankStr("b"),
            version=NotBlankStr("1"),
            actor_id=NotBlankStr("alice"),
        )
        page, total = await service.list_packs()
        assert total == 2
        assert page[0].id == second.id
        assert page[1].id == first.id


# ── SetupFacadeService ─────────────────────────────────────────────


class TestSetupFacadeService:
    async def test_status_defaults_to_uninitialised(self) -> None:
        service = SetupFacadeService()
        status = await service.get_status()
        assert status["initialised"] is False
        assert status["initialised_at"] is None

    async def test_initialize_is_capability_gap(self) -> None:
        service = SetupFacadeService()
        with pytest.raises(CapabilityNotSupportedError):
            await service.initialize(config={})


# ── SimulationFacadeService ────────────────────────────────────────


class TestSimulationFacadeService:
    async def test_list_capability_gap_without_method(self) -> None:
        service = SimulationFacadeService(state=SimpleNamespace())  # type: ignore[arg-type]
        with pytest.raises(CapabilityNotSupportedError):
            await service.list_simulations()

    async def test_list_delegates_when_available(self) -> None:
        class _State:
            def list_scenarios(self) -> tuple[object, ...]:
                return ("a", "b")

        service = SimulationFacadeService(state=_State())  # type: ignore[arg-type]
        page, total = await service.list_simulations()
        assert page == ("a", "b")
        assert total == 2

    async def test_get_capability_gap_without_method(self) -> None:
        service = SimulationFacadeService(state=SimpleNamespace())  # type: ignore[arg-type]
        with pytest.raises(CapabilityNotSupportedError):
            await service.get_simulation(NotBlankStr("any"))

    async def test_get_delegates_when_available(self) -> None:
        class _State:
            def get_scenario(self, scenario_id: str) -> object | None:
                return {"id": scenario_id}

        service = SimulationFacadeService(state=_State())  # type: ignore[arg-type]
        result = await service.get_simulation(NotBlankStr("alpha"))
        assert result == {"id": "alpha"}

    async def test_create_is_capability_gap(self) -> None:
        service = SimulationFacadeService(state=SimpleNamespace())  # type: ignore[arg-type]
        with pytest.raises(CapabilityNotSupportedError):
            await service.create_simulation()
