"""Unit tests for integrations MCP handlers.

Covers 21 tools across MCP catalog (5), oauth (3), clients (5),
artifacts (4), ontology (4).
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
import structlog.testing

from synthorg.communication.mcp_errors import CapabilityNotSupportedError
from synthorg.integrations.mcp_services import (
    ArtifactFacadeService,
    ClientFacadeService,
    OAuthFacadeService,
)
from synthorg.meta.mcp.handlers.integrations import INTEGRATION_HANDLERS
from synthorg.observability.events.mcp import MCP_DESTRUCTIVE_OP_EXECUTED
from tests.unit.meta.mcp.conftest import make_test_actor

pytestmark = pytest.mark.unit


@pytest.fixture
def fake_catalog() -> AsyncMock:
    service = AsyncMock()
    service.list_catalog = AsyncMock(return_value=())
    service.search_catalog = AsyncMock(return_value=())
    service.get_catalog_entry = AsyncMock(return_value=None)
    service.install_catalog_entry = AsyncMock(return_value={"installed": True})
    service.uninstall_catalog_entry = AsyncMock(return_value=True)
    return service


@pytest.fixture
def fake_ontology() -> AsyncMock:
    service = AsyncMock()
    service.list_entities = AsyncMock(return_value=())
    service.get_entity = AsyncMock(return_value=None)
    service.get_relationships = AsyncMock(return_value=())
    service.search = AsyncMock(return_value=())
    return service


@pytest.fixture
def real_oauth() -> OAuthFacadeService:
    return OAuthFacadeService()


@pytest.fixture
def real_clients() -> ClientFacadeService:
    return ClientFacadeService()


@pytest.fixture
def real_artifacts() -> ArtifactFacadeService:
    # Storage backend needs an awaitable ``delete`` that returns truthy
    # so the destructive happy-path test can exercise the real service.
    storage = MagicMock()
    storage.delete = AsyncMock(return_value=True)
    return ArtifactFacadeService(storage=storage)


@pytest.fixture
def fake_app_state(
    fake_catalog: AsyncMock,
    real_oauth: OAuthFacadeService,
    real_clients: ClientFacadeService,
    real_artifacts: ArtifactFacadeService,
    fake_ontology: AsyncMock,
) -> SimpleNamespace:
    return SimpleNamespace(
        mcp_catalog_facade_service=fake_catalog,
        oauth_facade_service=real_oauth,
        client_facade_service=real_clients,
        artifact_facade_service=real_artifacts,
        ontology_facade_service=fake_ontology,
    )


class TestMCPCatalog:
    async def test_list(self, fake_app_state: SimpleNamespace) -> None:
        handler = INTEGRATION_HANDLERS["synthorg_mcp_catalog_list"]
        response = await handler(app_state=fake_app_state, arguments={})
        assert json.loads(response)["status"] == "ok"

    async def test_search_requires_query(
        self,
        fake_app_state: SimpleNamespace,
    ) -> None:
        handler = INTEGRATION_HANDLERS["synthorg_mcp_catalog_search"]
        response = await handler(app_state=fake_app_state, arguments={})
        assert json.loads(response)["status"] == "error"

    async def test_get_not_found(self, fake_app_state: SimpleNamespace) -> None:
        handler = INTEGRATION_HANDLERS["synthorg_mcp_catalog_get"]
        response = await handler(
            app_state=fake_app_state,
            arguments={"entry_id": "x"},
        )
        assert json.loads(response)["domain_code"] == "not_found"

    async def test_install(self, fake_app_state: SimpleNamespace) -> None:
        handler = INTEGRATION_HANDLERS["synthorg_mcp_catalog_install"]
        response = await handler(
            app_state=fake_app_state,
            arguments={"entry_id": "x"},
            actor=make_test_actor(),
        )
        assert json.loads(response)["status"] == "ok"

    async def test_install_capability_gap(
        self,
        fake_app_state: SimpleNamespace,
        fake_catalog: AsyncMock,
    ) -> None:
        fake_catalog.install_catalog_entry = AsyncMock(
            side_effect=CapabilityNotSupportedError("mcp_catalog_install", "x"),
        )
        handler = INTEGRATION_HANDLERS["synthorg_mcp_catalog_install"]
        response = await handler(
            app_state=fake_app_state,
            arguments={"entry_id": "x"},
            actor=make_test_actor(),
        )
        assert json.loads(response)["domain_code"] == "not_supported"

    async def test_uninstall_guardrails(
        self,
        fake_app_state: SimpleNamespace,
    ) -> None:
        handler = INTEGRATION_HANDLERS["synthorg_mcp_catalog_uninstall"]
        response = await handler(
            app_state=fake_app_state,
            arguments={"installation_id": "x"},
            actor=make_test_actor(),
        )
        assert json.loads(response)["domain_code"] == "guardrail_violated"


class TestOAuth:
    async def test_configure_and_list(
        self,
        fake_app_state: SimpleNamespace,
    ) -> None:
        configure = INTEGRATION_HANDLERS["synthorg_oauth_configure_provider"]
        response = await configure(
            app_state=fake_app_state,
            arguments={
                "name": "test-provider",
                "client_id": "c",
                "authorize_url": "https://example.test/auth",
                "token_url": "https://example.test/token",
                "scopes": ["repo"],
            },
            actor=make_test_actor(),
        )
        assert json.loads(response)["status"] == "ok"
        lst = await INTEGRATION_HANDLERS["synthorg_oauth_list_providers"](
            app_state=fake_app_state,
            arguments={},
        )
        payload = json.loads(lst)
        assert payload["status"] == "ok"
        assert len(payload["data"]) == 1

    async def test_remove_guardrails(self, fake_app_state: SimpleNamespace) -> None:
        handler = INTEGRATION_HANDLERS["synthorg_oauth_remove_provider"]
        response = await handler(
            app_state=fake_app_state,
            arguments={"name": "test-provider"},
            actor=make_test_actor(),
        )
        assert json.loads(response)["domain_code"] == "guardrail_violated"


class TestClients:
    async def test_create_and_satisfaction(
        self,
        fake_app_state: SimpleNamespace,
    ) -> None:
        create = INTEGRATION_HANDLERS["synthorg_clients_create"]
        response = await create(
            app_state=fake_app_state,
            arguments={"name": "Acme"},
            actor=make_test_actor(),
        )
        data = json.loads(response)["data"]
        client_id = data["id"]
        sat = await INTEGRATION_HANDLERS["synthorg_clients_get_satisfaction"](
            app_state=fake_app_state,
            arguments={"client_id": client_id},
        )
        assert json.loads(sat)["status"] == "ok"

    async def test_deactivate_guardrails(
        self,
        fake_app_state: SimpleNamespace,
    ) -> None:
        handler = INTEGRATION_HANDLERS["synthorg_clients_deactivate"]
        response = await handler(
            app_state=fake_app_state,
            arguments={"client_id": str(uuid4())},
            actor=make_test_actor(),
        )
        assert json.loads(response)["domain_code"] == "guardrail_violated"


class TestArtifacts:
    async def test_create_and_list(self, fake_app_state: SimpleNamespace) -> None:
        create = INTEGRATION_HANDLERS["synthorg_artifacts_create"]
        response = await create(
            app_state=fake_app_state,
            arguments={
                "name": "a.txt",
                "content_type": "text/plain",
                "size_bytes": 10,
                "storage_ref": "s3://bucket/a.txt",
            },
            actor=make_test_actor(),
        )
        assert json.loads(response)["status"] == "ok"
        listed = await INTEGRATION_HANDLERS["synthorg_artifacts_list"](
            app_state=fake_app_state,
            arguments={},
        )
        list_payload = json.loads(listed)
        assert list_payload["status"] == "ok"
        # The artifact we just created should round-trip through list.
        assert isinstance(list_payload.get("data"), list)
        assert len(list_payload["data"]) >= 1

    async def test_delete_guardrails(self, fake_app_state: SimpleNamespace) -> None:
        handler = INTEGRATION_HANDLERS["synthorg_artifacts_delete"]
        response = await handler(
            app_state=fake_app_state,
            arguments={"artifact_id": str(uuid4())},
            actor=make_test_actor(),
        )
        assert json.loads(response)["domain_code"] == "guardrail_violated"


class TestDestructiveHappyPaths:
    """Happy-path coverage for the destructive integration handlers.

    Verifies the returned envelope shape and that
    ``MCP_DESTRUCTIVE_OP_EXECUTED`` is emitted so a regression in the
    audit path is caught.
    """

    async def test_mcp_catalog_uninstall_happy(
        self,
        fake_app_state: SimpleNamespace,
    ) -> None:
        install_handler = INTEGRATION_HANDLERS["synthorg_mcp_catalog_install"]
        installed = json.loads(
            await install_handler(
                app_state=fake_app_state,
                arguments={"entry_id": "cat-1"},
                actor=make_test_actor(),
            ),
        )
        assert installed["status"] == "ok"

        uninstall_handler = INTEGRATION_HANDLERS["synthorg_mcp_catalog_uninstall"]
        with structlog.testing.capture_logs() as events:
            response = await uninstall_handler(
                app_state=fake_app_state,
                arguments={
                    "installation_id": "inst-1",
                    "reason": "cleanup",
                    "confirm": True,
                },
                actor=make_test_actor(),
            )
        assert json.loads(response)["status"] == "ok"
        exec_events = [
            e for e in events if e.get("event") == MCP_DESTRUCTIVE_OP_EXECUTED
        ]
        assert len(exec_events) == 1
        assert exec_events[0]["tool_name"] == "synthorg_mcp_catalog_uninstall"

    async def test_oauth_remove_provider_happy(
        self,
        fake_app_state: SimpleNamespace,
    ) -> None:
        configure = INTEGRATION_HANDLERS["synthorg_oauth_configure_provider"]
        await configure(
            app_state=fake_app_state,
            arguments={
                "name": "test-provider",
                "client_id": "client",
                "authorize_url": "https://localhost/auth",
                "token_url": "https://localhost/token",
                "scopes": ["read"],
            },
            actor=make_test_actor(),
        )
        remove = INTEGRATION_HANDLERS["synthorg_oauth_remove_provider"]
        with structlog.testing.capture_logs() as events:
            response = await remove(
                app_state=fake_app_state,
                arguments={
                    "name": "test-provider",
                    "reason": "rotated",
                    "confirm": True,
                },
                actor=make_test_actor(),
            )
        body = json.loads(response)
        assert body["status"] == "ok"
        assert body["data"] == {"removed": True}
        exec_events = [
            e for e in events if e.get("event") == MCP_DESTRUCTIVE_OP_EXECUTED
        ]
        assert len(exec_events) == 1
        assert exec_events[0]["tool_name"] == "synthorg_oauth_remove_provider"

    async def test_clients_deactivate_happy(
        self,
        fake_app_state: SimpleNamespace,
    ) -> None:
        create = INTEGRATION_HANDLERS["synthorg_clients_create"]
        created = json.loads(
            await create(
                app_state=fake_app_state,
                arguments={"name": "Acme"},
                actor=make_test_actor(),
            ),
        )
        client_id = created["data"]["id"]
        deactivate = INTEGRATION_HANDLERS["synthorg_clients_deactivate"]
        with structlog.testing.capture_logs() as events:
            response = await deactivate(
                app_state=fake_app_state,
                arguments={
                    "client_id": client_id,
                    "reason": "offboarded",
                    "confirm": True,
                },
                actor=make_test_actor(),
            )
        body = json.loads(response)
        assert body["status"] == "ok"
        assert body["data"] == {"deactivated": True}
        exec_events = [
            e for e in events if e.get("event") == MCP_DESTRUCTIVE_OP_EXECUTED
        ]
        assert len(exec_events) == 1
        assert exec_events[0]["tool_name"] == "synthorg_clients_deactivate"

    async def test_artifacts_delete_happy(
        self,
        fake_app_state: SimpleNamespace,
    ) -> None:
        create = INTEGRATION_HANDLERS["synthorg_artifacts_create"]
        created = json.loads(
            await create(
                app_state=fake_app_state,
                arguments={
                    "name": "report.pdf",
                    "content_type": "application/pdf",
                    "size_bytes": 1024,
                    "storage_ref": "s3://bucket/key",
                },
                actor=make_test_actor(),
            ),
        )
        artifact_id = created["data"]["id"]
        delete = INTEGRATION_HANDLERS["synthorg_artifacts_delete"]
        with structlog.testing.capture_logs() as events:
            response = await delete(
                app_state=fake_app_state,
                arguments={
                    "artifact_id": artifact_id,
                    "reason": "ttl expired",
                    "confirm": True,
                },
                actor=make_test_actor(),
            )
        body = json.loads(response)
        assert body["status"] == "ok"
        assert body["data"] == {"removed": True}
        exec_events = [
            e for e in events if e.get("event") == MCP_DESTRUCTIVE_OP_EXECUTED
        ]
        assert len(exec_events) == 1
        assert exec_events[0]["tool_name"] == "synthorg_artifacts_delete"


class TestOntology:
    async def test_list_entities(self, fake_app_state: SimpleNamespace) -> None:
        handler = INTEGRATION_HANDLERS["synthorg_ontology_list_entities"]
        response = await handler(app_state=fake_app_state, arguments={})
        assert json.loads(response)["status"] == "ok"

    async def test_get_not_found(self, fake_app_state: SimpleNamespace) -> None:
        handler = INTEGRATION_HANDLERS["synthorg_ontology_get_entity"]
        response = await handler(
            app_state=fake_app_state,
            arguments={"entity_id": "x"},
        )
        assert json.loads(response)["domain_code"] == "not_found"

    async def test_search_requires_query(
        self,
        fake_app_state: SimpleNamespace,
    ) -> None:
        handler = INTEGRATION_HANDLERS["synthorg_ontology_search"]
        response = await handler(app_state=fake_app_state, arguments={})
        assert json.loads(response)["status"] == "error"

    async def test_get_relationships(self, fake_app_state: SimpleNamespace) -> None:
        handler = INTEGRATION_HANDLERS["synthorg_ontology_get_relationships"]
        response = await handler(
            app_state=fake_app_state,
            arguments={"entity_id": "x"},
        )
        assert json.loads(response)["status"] == "ok"
