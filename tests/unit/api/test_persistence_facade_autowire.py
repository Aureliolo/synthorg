"""Unit tests for persistence-gated infrastructure read-facade auto-wiring.

Covers ``wire_persistence_facades`` and its user / backup / ontology /
MCP-catalog branches: each facade wires only once its backing service reached
its slice during startup, and re-running the sweep never replaces a live facade.
"""

import pytest

from synthorg.api.api_core_state import ApiCoreStateSlice
from synthorg.api.auth.service import AuthService
from synthorg.api.lifecycle_helpers.persistence_facade_autowire import (
    wire_persistence_facades,
)
from synthorg.api.state import AppState
from synthorg.backup.service import BackupService
from synthorg.backup.state import BackupStateSlice
from synthorg.infrastructure.services import BackupFacadeService, UserFacadeService
from synthorg.infrastructure.state import FacadesStateSlice
from synthorg.integrations.connections.catalog import ConnectionCatalog
from synthorg.integrations.mcp_catalog.installations import McpInstallationRepository
from synthorg.integrations.mcp_catalog.service import CatalogService
from synthorg.integrations.mcp_facades import (
    MCPCatalogFacadeService,
    OntologyFacadeService,
)
from synthorg.integrations.state import IntegrationsStateSlice
from synthorg.ontology.service import OntologyService
from synthorg.ontology.state import OntologyStateSlice
from tests._shared import make_app_state, mock_of

pytestmark = pytest.mark.unit


def _app_state(  # noqa: PLR0913 -- independent per-facade dependency flags
    *,
    with_auth: bool = False,
    with_backup: bool = False,
    with_ontology: bool = False,
    with_catalog: bool = False,
    with_installations: bool = False,
    with_connection_catalog: bool = False,
) -> AppState:
    """Compose an app state with the requested backing services present.

    ``with_catalog``, ``with_installations``, and ``with_connection_catalog``
    are independent so the MCP-catalog facade's three-dependency gate can be
    exercised with any part absent.

    Returns:
        The composed ``AppState``.
    """
    api_core: dict[str, object] = {}
    backup: dict[str, object] = {}
    ontology: dict[str, object] = {}
    integrations: dict[str, object] = {}
    if with_auth:
        api_core["auth_service"] = mock_of[AuthService]()
    if with_backup:
        backup["service"] = mock_of[BackupService]()
    if with_ontology:
        ontology["service"] = mock_of[OntologyService]()
    if with_catalog:
        integrations["mcp_catalog_service"] = mock_of[CatalogService]()
    if with_installations:
        integrations["mcp_installations_repo"] = mock_of[McpInstallationRepository]()
    if with_connection_catalog:
        integrations["connection_catalog"] = mock_of[ConnectionCatalog]()
    return make_app_state(
        slices={
            ApiCoreStateSlice: api_core,
            BackupStateSlice: backup,
            OntologyStateSlice: ontology,
            IntegrationsStateSlice: integrations,
        },
    )


class TestUserFacadeWiring:
    async def test_wired_when_auth_present(self) -> None:
        app_state = _app_state(with_auth=True)
        await wire_persistence_facades(app_state)
        assert app_state.slice(FacadesStateSlice).user_facade_service is not None

    async def test_absent_without_auth(self) -> None:
        app_state = _app_state(with_auth=False)
        await wire_persistence_facades(app_state)
        assert app_state.slice(FacadesStateSlice).user_facade_service is None

    async def test_idempotent_keeps_existing(self) -> None:
        app_state = _app_state(with_auth=True)
        existing = UserFacadeService(auth_service=mock_of[AuthService]())
        app_state.wire(FacadesStateSlice, user_facade_service=existing)
        await wire_persistence_facades(app_state)
        assert app_state.slice(FacadesStateSlice).user_facade_service is existing


class TestBackupFacadeWiring:
    async def test_wired_when_backup_service_present(self) -> None:
        app_state = _app_state(with_backup=True)
        await wire_persistence_facades(app_state)
        assert app_state.slice(FacadesStateSlice).backup_facade_service is not None

    async def test_absent_without_backup_service(self) -> None:
        app_state = _app_state(with_backup=False)
        await wire_persistence_facades(app_state)
        assert app_state.slice(FacadesStateSlice).backup_facade_service is None

    async def test_idempotent_keeps_existing(self) -> None:
        app_state = _app_state(with_backup=True)
        existing = BackupFacadeService(service=mock_of[BackupService]())
        app_state.wire(FacadesStateSlice, backup_facade_service=existing)
        await wire_persistence_facades(app_state)
        assert app_state.slice(FacadesStateSlice).backup_facade_service is existing


class TestOntologyFacadeWiring:
    async def test_wired_when_ontology_service_present(self) -> None:
        app_state = _app_state(with_ontology=True)
        await wire_persistence_facades(app_state)
        assert app_state.slice(FacadesStateSlice).ontology_facade_service is not None

    async def test_absent_without_ontology_service(self) -> None:
        app_state = _app_state(with_ontology=False)
        await wire_persistence_facades(app_state)
        assert app_state.slice(FacadesStateSlice).ontology_facade_service is None

    async def test_idempotent_keeps_existing(self) -> None:
        app_state = _app_state(with_ontology=True)
        existing = OntologyFacadeService(ontology=mock_of[OntologyService]())
        app_state.wire(FacadesStateSlice, ontology_facade_service=existing)
        await wire_persistence_facades(app_state)
        assert app_state.slice(FacadesStateSlice).ontology_facade_service is existing


class TestMcpCatalogFacadeWiring:
    async def test_wired_when_all_three_present(self) -> None:
        app_state = _app_state(
            with_catalog=True,
            with_installations=True,
            with_connection_catalog=True,
        )
        await wire_persistence_facades(app_state)
        assert app_state.slice(FacadesStateSlice).mcp_catalog_facade_service is not None

    async def test_absent_without_catalog_or_repo(self) -> None:
        app_state = _app_state(with_catalog=False, with_installations=False)
        await wire_persistence_facades(app_state)
        assert app_state.slice(FacadesStateSlice).mcp_catalog_facade_service is None

    async def test_absent_when_only_catalog_present(self) -> None:
        app_state = _app_state(with_catalog=True, with_installations=False)
        await wire_persistence_facades(app_state)
        assert app_state.slice(FacadesStateSlice).mcp_catalog_facade_service is None

    async def test_absent_when_only_installations_present(self) -> None:
        app_state = _app_state(with_catalog=False, with_installations=True)
        await wire_persistence_facades(app_state)
        assert app_state.slice(FacadesStateSlice).mcp_catalog_facade_service is None

    async def test_absent_without_connection_catalog(self) -> None:
        app_state = _app_state(
            with_catalog=True,
            with_installations=True,
            with_connection_catalog=False,
        )
        await wire_persistence_facades(app_state)
        assert app_state.slice(FacadesStateSlice).mcp_catalog_facade_service is None

    async def test_idempotent_keeps_existing(self) -> None:
        app_state = _app_state(
            with_catalog=True,
            with_installations=True,
            with_connection_catalog=True,
        )
        existing = MCPCatalogFacadeService(
            catalog=mock_of[CatalogService](),
            installations=mock_of[McpInstallationRepository](),
            connection_catalog=mock_of[ConnectionCatalog](),
        )
        app_state.wire(FacadesStateSlice, mcp_catalog_facade_service=existing)
        await wire_persistence_facades(app_state)
        assert app_state.slice(FacadesStateSlice).mcp_catalog_facade_service is existing
