"""Unit tests for infrastructure MCP handlers.

Covers 40 tools: health, settings, providers, backup, audit, events,
users, projects, requests, setup, simulations, template packs,
integration health.
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import structlog.testing

from synthorg.communication.mcp_errors import CapabilityNotSupportedError
from synthorg.infrastructure.services import (
    AuditReadService,
    BackupFacadeService,
    EventsReadService,
    IntegrationHealthFacadeService,
    ProjectFacadeService,
    ProviderReadService,
    RequestsFacadeService,
    SettingsReadService,
    SetupFacadeService,
    SimulationFacadeService,
    TemplatePackFacadeService,
    UserFacadeService,
)
from synthorg.meta.mcp.handlers.infrastructure import INFRASTRUCTURE_HANDLERS
from synthorg.observability.events.mcp import MCP_ADMIN_OP_EXECUTED
from tests.unit.meta.mcp.conftest import make_test_actor

pytestmark = pytest.mark.unit


@pytest.fixture
def fake_settings() -> AsyncMock:
    service = AsyncMock(spec=SettingsReadService)
    service.list_settings.return_value = {"key": "value"}
    service.get_setting.return_value = "value"
    service.update_setting.return_value = None
    service.delete_setting.return_value = None
    return service


@pytest.fixture
def fake_providers() -> AsyncMock:
    service = AsyncMock(spec=ProviderReadService)
    service.list_providers.return_value = ()
    service.get_provider.return_value = None
    service.get_health.return_value = {"p1": "healthy"}
    service.test_connection.return_value = {"provider_id": "p1", "result": "ok"}
    return service


@pytest.fixture
def fake_backup() -> AsyncMock:
    service = AsyncMock(spec=BackupFacadeService)
    service.list_backups.return_value = ((), 0)
    service.get_backup.side_effect = LookupError("not found")
    service.create_backup.return_value = {"manifest": "x"}
    service.delete_backup.return_value = None
    service.restore_backup.return_value = {"backup_id": "b1", "restored": True}
    return service


@pytest.fixture
def fake_users() -> AsyncMock:
    service = AsyncMock(spec=UserFacadeService)
    service.list_users.return_value = ()
    service.get_user.return_value = None
    service.create_user.side_effect = CapabilityNotSupportedError("user_create", "x")
    service.update_user.side_effect = CapabilityNotSupportedError("user_update", "x")
    service.delete_user.side_effect = CapabilityNotSupportedError("user_delete", "x")
    return service


@pytest.fixture
def fake_audit() -> AsyncMock:
    service = AsyncMock(spec=AuditReadService)
    service.list_entries.return_value = ((), 0)
    return service


@pytest.fixture
def fake_events() -> AsyncMock:
    service = AsyncMock(spec=EventsReadService)
    service.list_events.return_value = ((), 0)
    return service


@pytest.fixture
def fake_integration_health() -> AsyncMock:
    service = AsyncMock(spec=IntegrationHealthFacadeService)
    service.get_all.return_value = {}
    service.get_one.return_value = None
    return service


@pytest.fixture
def fake_setup() -> AsyncMock:
    service = AsyncMock(spec=SetupFacadeService)
    service.get_status.return_value = {"initialised": False}
    service.initialize.side_effect = CapabilityNotSupportedError(
        "setup_initialize",
        "x",
    )
    return service


@pytest.fixture
def fake_simulation() -> AsyncMock:
    service = AsyncMock(spec=SimulationFacadeService)
    service.list_simulations.return_value = ((), 0)
    service.get_simulation.return_value = None
    service.create_simulation.side_effect = CapabilityNotSupportedError(
        "simulation_create",
        "x",
    )
    return service


@pytest.fixture
def real_projects() -> ProjectFacadeService:
    return ProjectFacadeService()


@pytest.fixture
def real_requests() -> RequestsFacadeService:
    return RequestsFacadeService()


@pytest.fixture
def real_template_packs() -> TemplatePackFacadeService:
    return TemplatePackFacadeService()


@pytest.fixture
def fake_app_state(  # noqa: PLR0913
    fake_settings: AsyncMock,
    fake_providers: AsyncMock,
    fake_backup: AsyncMock,
    fake_users: AsyncMock,
    fake_audit: AsyncMock,
    fake_events: AsyncMock,
    fake_integration_health: AsyncMock,
    fake_setup: AsyncMock,
    fake_simulation: AsyncMock,
    real_projects: ProjectFacadeService,
    real_requests: RequestsFacadeService,
    real_template_packs: TemplatePackFacadeService,
) -> SimpleNamespace:
    return SimpleNamespace(
        has_task_engine=True,
        has_cost_tracker=True,
        has_agent_registry=True,
        approval_store=object(),
        settings_read_service=fake_settings,
        provider_read_service=fake_providers,
        backup_facade_service=fake_backup,
        user_facade_service=fake_users,
        audit_read_service=fake_audit,
        events_read_service=fake_events,
        integration_health_facade_service=fake_integration_health,
        setup_facade_service=fake_setup,
        simulation_facade_service=fake_simulation,
        project_facade_service=real_projects,
        requests_facade_service=real_requests,
        template_pack_facade_service=real_template_packs,
    )


class TestHealth:
    async def test_ok(self, fake_app_state: SimpleNamespace) -> None:
        handler = INFRASTRUCTURE_HANDLERS["synthorg_health_check"]
        response = await handler(app_state=fake_app_state, arguments={})
        payload = json.loads(response)
        assert payload["status"] == "ok"
        assert payload["data"]["task_engine"] is True


class TestSettings:
    async def test_list(self, fake_app_state: SimpleNamespace) -> None:
        handler = INFRASTRUCTURE_HANDLERS["synthorg_settings_list"]
        response = await handler(app_state=fake_app_state, arguments={})
        assert json.loads(response)["status"] == "ok"

    async def test_get_missing_key(self, fake_app_state: SimpleNamespace) -> None:
        handler = INFRASTRUCTURE_HANDLERS["synthorg_settings_get"]
        response = await handler(app_state=fake_app_state, arguments={})
        assert json.loads(response)["status"] == "error"

    async def test_update_ok(self, fake_app_state: SimpleNamespace) -> None:
        handler = INFRASTRUCTURE_HANDLERS["synthorg_settings_update"]
        with structlog.testing.capture_logs() as events:
            response = await handler(
                app_state=fake_app_state,
                arguments={
                    "key": "k",
                    "value": "v",
                    "confirm": True,
                    "reason": "test",
                },
                actor=make_test_actor(),
            )
        assert json.loads(response)["status"] == "ok"
        audit = [e for e in events if e.get("event") == MCP_ADMIN_OP_EXECUTED]
        assert len(audit) == 1
        assert audit[0]["tool_name"] == "synthorg_settings_update"

    async def test_update_requires_guardrails(
        self,
        fake_app_state: SimpleNamespace,
    ) -> None:
        handler = INFRASTRUCTURE_HANDLERS["synthorg_settings_update"]
        response = await handler(
            app_state=fake_app_state,
            arguments={"key": "k", "value": "v"},
            actor=make_test_actor(),
        )
        assert json.loads(response)["domain_code"] == "guardrail_violated"

    async def test_delete_requires_guardrails(
        self,
        fake_app_state: SimpleNamespace,
    ) -> None:
        handler = INFRASTRUCTURE_HANDLERS["synthorg_settings_delete"]
        response = await handler(
            app_state=fake_app_state,
            arguments={"key": "k"},
            actor=make_test_actor(),
        )
        assert json.loads(response)["domain_code"] == "guardrail_violated"


class TestProviders:
    async def test_list(self, fake_app_state: SimpleNamespace) -> None:
        handler = INFRASTRUCTURE_HANDLERS["synthorg_providers_list"]
        response = await handler(app_state=fake_app_state, arguments={})
        assert json.loads(response)["status"] == "ok"

    async def test_get_not_found(self, fake_app_state: SimpleNamespace) -> None:
        handler = INFRASTRUCTURE_HANDLERS["synthorg_providers_get"]
        response = await handler(
            app_state=fake_app_state,
            arguments={"provider_id": "p1"},
        )
        assert json.loads(response)["domain_code"] == "not_found"

    async def test_get_health(self, fake_app_state: SimpleNamespace) -> None:
        handler = INFRASTRUCTURE_HANDLERS["synthorg_providers_get_health"]
        response = await handler(app_state=fake_app_state, arguments={})
        assert json.loads(response)["status"] == "ok"

    async def test_test_connection(self, fake_app_state: SimpleNamespace) -> None:
        handler = INFRASTRUCTURE_HANDLERS["synthorg_providers_test_connection"]
        with structlog.testing.capture_logs() as events:
            response = await handler(
                app_state=fake_app_state,
                arguments={
                    "provider_name": "p1",
                    "confirm": True,
                    "reason": "test",
                },
                actor=make_test_actor(),
            )
        audit = [e for e in events if e.get("event") == MCP_ADMIN_OP_EXECUTED]
        assert len(audit) == 1
        assert audit[0]["tool_name"] == "synthorg_providers_test_connection"
        assert json.loads(response)["status"] == "ok"


class TestBackup:
    async def test_list(self, fake_app_state: SimpleNamespace) -> None:
        handler = INFRASTRUCTURE_HANDLERS["synthorg_backup_list"]
        response = await handler(app_state=fake_app_state, arguments={})
        assert json.loads(response)["status"] == "ok"

    async def test_get_not_found(self, fake_app_state: SimpleNamespace) -> None:
        handler = INFRASTRUCTURE_HANDLERS["synthorg_backup_get"]
        response = await handler(
            app_state=fake_app_state,
            arguments={"backup_id": "b1"},
        )
        assert json.loads(response)["domain_code"] == "not_found"

    async def test_create_requires_trigger(
        self,
        fake_app_state: SimpleNamespace,
    ) -> None:
        handler = INFRASTRUCTURE_HANDLERS["synthorg_backup_create"]
        response = await handler(
            app_state=fake_app_state,
            arguments={"confirm": True, "reason": "test"},
            actor=make_test_actor(),
        )
        assert json.loads(response)["status"] == "error"

    async def test_create_requires_guardrails(
        self,
        fake_app_state: SimpleNamespace,
    ) -> None:
        handler = INFRASTRUCTURE_HANDLERS["synthorg_backup_create"]
        response = await handler(
            app_state=fake_app_state,
            arguments={"trigger": "manual"},
            actor=make_test_actor(),
        )
        assert json.loads(response)["domain_code"] == "guardrail_violated"

    async def test_delete_guardrails(self, fake_app_state: SimpleNamespace) -> None:
        handler = INFRASTRUCTURE_HANDLERS["synthorg_backup_delete"]
        with structlog.testing.capture_logs() as events:
            response = await handler(
                app_state=fake_app_state,
                arguments={"backup_id": "b1", "confirm": True, "reason": "cleanup"},
                actor=make_test_actor(),
            )
        assert json.loads(response)["status"] == "ok"
        exec_events = [e for e in events if e.get("event") == MCP_ADMIN_OP_EXECUTED]
        assert len(exec_events) == 1
        assert exec_events[0]["tool_name"] == "synthorg_backup_delete"

    async def test_restore_guardrails(self, fake_app_state: SimpleNamespace) -> None:
        handler = INFRASTRUCTURE_HANDLERS["synthorg_backup_restore"]
        with structlog.testing.capture_logs() as events:
            response = await handler(
                app_state=fake_app_state,
                arguments={"backup_id": "b1", "confirm": True, "reason": "dr"},
                actor=make_test_actor(),
            )
        assert json.loads(response)["status"] == "ok"
        exec_events = [e for e in events if e.get("event") == MCP_ADMIN_OP_EXECUTED]
        assert len(exec_events) == 1
        assert exec_events[0]["tool_name"] == "synthorg_backup_restore"


class TestUsers:
    async def test_list_capability_gap(
        self,
        fake_app_state: SimpleNamespace,
        fake_users: AsyncMock,
    ) -> None:
        fake_users.list_users.side_effect = CapabilityNotSupportedError(
            "user_list",
            "x",
        )
        handler = INFRASTRUCTURE_HANDLERS["synthorg_users_list"]
        response = await handler(app_state=fake_app_state, arguments={})
        assert json.loads(response)["domain_code"] == "not_supported"

    async def test_create_capability_gap(
        self,
        fake_app_state: SimpleNamespace,
    ) -> None:
        handler = INFRASTRUCTURE_HANDLERS["synthorg_users_create"]
        response = await handler(
            app_state=fake_app_state,
            arguments={
                "username": "alice",
                "role": "admin",
                "confirm": True,
                "reason": "test",
            },
            actor=make_test_actor(),
        )
        assert json.loads(response)["domain_code"] == "not_supported"

    async def test_create_requires_guardrails(
        self,
        fake_app_state: SimpleNamespace,
    ) -> None:
        handler = INFRASTRUCTURE_HANDLERS["synthorg_users_create"]
        response = await handler(
            app_state=fake_app_state,
            arguments={"username": "u", "role": "admin"},
            actor=make_test_actor(),
        )
        assert json.loads(response)["domain_code"] == "guardrail_violated"

    async def test_update_requires_guardrails(
        self,
        fake_app_state: SimpleNamespace,
    ) -> None:
        handler = INFRASTRUCTURE_HANDLERS["synthorg_users_update"]
        response = await handler(
            app_state=fake_app_state,
            arguments={"user_id": str(uuid4()), "updates": {}},
            actor=make_test_actor(),
        )
        assert json.loads(response)["domain_code"] == "guardrail_violated"


class TestProjects:
    async def test_create_and_get(self, fake_app_state: SimpleNamespace) -> None:
        create_handler = INFRASTRUCTURE_HANDLERS["synthorg_projects_create"]
        create_response = await create_handler(
            app_state=fake_app_state,
            arguments={"name": "p1", "description": "desc"},
            actor=make_test_actor(),
        )
        created = json.loads(create_response)
        assert created["status"] == "ok"
        project_id = created["data"]["id"]

        get_handler = INFRASTRUCTURE_HANDLERS["synthorg_projects_get"]
        get_response = await get_handler(
            app_state=fake_app_state,
            arguments={"project_id": project_id},
        )
        assert json.loads(get_response)["status"] == "ok"

    async def test_delete_requires_guardrails(
        self,
        fake_app_state: SimpleNamespace,
    ) -> None:
        handler = INFRASTRUCTURE_HANDLERS["synthorg_projects_delete"]
        response = await handler(
            app_state=fake_app_state,
            arguments={"project_id": str(uuid4())},
            actor=make_test_actor(),
        )
        assert json.loads(response)["domain_code"] == "guardrail_violated"

    async def test_list_empty(self, fake_app_state: SimpleNamespace) -> None:
        handler = INFRASTRUCTURE_HANDLERS["synthorg_projects_list"]
        response = await handler(app_state=fake_app_state, arguments={})
        assert json.loads(response)["status"] == "ok"


class TestRequests:
    async def test_create(self, fake_app_state: SimpleNamespace) -> None:
        handler = INFRASTRUCTURE_HANDLERS["synthorg_requests_create"]
        response = await handler(
            app_state=fake_app_state,
            arguments={"title": "t", "body": "b"},
            actor=make_test_actor(),
        )
        assert json.loads(response)["status"] == "ok"


class TestSetup:
    async def test_status(self, fake_app_state: SimpleNamespace) -> None:
        handler = INFRASTRUCTURE_HANDLERS["synthorg_setup_get_status"]
        response = await handler(app_state=fake_app_state, arguments={})
        assert json.loads(response)["status"] == "ok"

    async def test_initialize_capability_gap(
        self,
        fake_app_state: SimpleNamespace,
    ) -> None:
        handler = INFRASTRUCTURE_HANDLERS["synthorg_setup_initialize"]
        response = await handler(
            app_state=fake_app_state,
            arguments={
                "config": {"region": "eu"},
                "confirm": True,
                "reason": "test",
            },
            actor=make_test_actor(),
        )
        assert json.loads(response)["domain_code"] == "not_supported"

    async def test_initialize_requires_guardrails(
        self,
        fake_app_state: SimpleNamespace,
    ) -> None:
        handler = INFRASTRUCTURE_HANDLERS["synthorg_setup_initialize"]
        response = await handler(
            app_state=fake_app_state,
            arguments={},
            actor=make_test_actor(),
        )
        assert json.loads(response)["domain_code"] == "guardrail_violated"


class TestSimulations:
    async def test_list(self, fake_app_state: SimpleNamespace) -> None:
        handler = INFRASTRUCTURE_HANDLERS["synthorg_simulations_list"]
        response = await handler(app_state=fake_app_state, arguments={})
        assert json.loads(response)["status"] == "ok"

    async def test_create_capability_gap(
        self,
        fake_app_state: SimpleNamespace,
    ) -> None:
        handler = INFRASTRUCTURE_HANDLERS["synthorg_simulations_create"]
        response = await handler(app_state=fake_app_state, arguments={})
        assert json.loads(response)["domain_code"] == "not_supported"


class TestTemplatePacks:
    async def test_install_then_list(self, fake_app_state: SimpleNamespace) -> None:
        install = INFRASTRUCTURE_HANDLERS["synthorg_template_packs_install"]
        with structlog.testing.capture_logs() as events:
            response = await install(
                app_state=fake_app_state,
                arguments={
                    "name": "p",
                    "version": "1.0",
                    "confirm": True,
                    "reason": "test",
                },
                actor=make_test_actor(),
            )
        assert json.loads(response)["status"] == "ok"
        audit = [e for e in events if e.get("event") == MCP_ADMIN_OP_EXECUTED]
        assert len(audit) == 1
        assert audit[0]["tool_name"] == "synthorg_template_packs_install"
        listed = await INFRASTRUCTURE_HANDLERS["synthorg_template_packs_list"](
            app_state=fake_app_state,
            arguments={},
        )
        list_payload = json.loads(listed)
        assert list_payload["status"] == "ok"
        # The pack we just installed should appear in the list.
        names = {item.get("name") for item in list_payload.get("data", [])}
        assert "p" in names

    async def test_install_requires_guardrails(
        self,
        fake_app_state: SimpleNamespace,
    ) -> None:
        install = INFRASTRUCTURE_HANDLERS["synthorg_template_packs_install"]
        response = await install(
            app_state=fake_app_state,
            arguments={"name": "p", "version": "1.0"},
            actor=make_test_actor(),
        )
        assert json.loads(response)["domain_code"] == "guardrail_violated"

    async def test_uninstall_guardrails(
        self,
        fake_app_state: SimpleNamespace,
    ) -> None:
        handler = INFRASTRUCTURE_HANDLERS["synthorg_template_packs_uninstall"]
        response = await handler(
            app_state=fake_app_state,
            arguments={"pack_id": str(uuid4())},
            actor=make_test_actor(),
        )
        assert json.loads(response)["domain_code"] == "guardrail_violated"


class TestAuditEvents:
    async def test_audit_list(self, fake_app_state: SimpleNamespace) -> None:
        handler = INFRASTRUCTURE_HANDLERS["synthorg_audit_list"]
        response = await handler(app_state=fake_app_state, arguments={})
        assert json.loads(response)["status"] == "ok"

    async def test_events_list(self, fake_app_state: SimpleNamespace) -> None:
        handler = INFRASTRUCTURE_HANDLERS["synthorg_events_list"]
        response = await handler(app_state=fake_app_state, arguments={})
        assert json.loads(response)["status"] == "ok"


class TestIntegrationHealth:
    async def test_get_all(self, fake_app_state: SimpleNamespace) -> None:
        handler = INFRASTRUCTURE_HANDLERS["synthorg_integration_health_get_all"]
        response = await handler(app_state=fake_app_state, arguments={})
        assert json.loads(response)["status"] == "ok"

    async def test_get_not_found(self, fake_app_state: SimpleNamespace) -> None:
        handler = INFRASTRUCTURE_HANDLERS["synthorg_integration_health_get"]
        response = await handler(
            app_state=fake_app_state,
            arguments={"integration_id": "x"},
        )
        assert json.loads(response)["domain_code"] == "not_found"
