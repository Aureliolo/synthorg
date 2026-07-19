"""Unit tests for the integrations MCP facade capability guards.

Each facade in :mod:`synthorg.integrations.mcp_facades` wraps a
primitive that may not implement every method.  When the backing
object lacks the needed callable the facade raises
:class:`CapabilityNotSupportedError` instead of silently degrading.
These tests pin every such guard by injecting a backing stub that
deliberately lacks the method, so the defensive branch is exercised
(a bare ``AsyncMock`` would fabricate the attribute and skip it).
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from synthorg.communication.mcp_errors import CapabilityNotSupportedError
from synthorg.core.types import NotBlankStr
from synthorg.integrations.errors import CatalogEntryNotFoundError
from synthorg.integrations.mcp_facades import (
    ArtifactFacadeService,
    MCPCatalogFacadeService,
    OntologyFacadeService,
)

pytestmark = pytest.mark.unit


# ── MCPCatalogFacadeService ────────────────────────────────────────


def _catalog_facade() -> MCPCatalogFacadeService:
    """Build a catalog facade whose backing primitives expose nothing."""
    return MCPCatalogFacadeService(
        catalog=SimpleNamespace(),  # type: ignore[arg-type]
        installations=SimpleNamespace(),
        connection_catalog=SimpleNamespace(),  # type: ignore[arg-type]
    )


async def test_list_catalog_without_capability_raises() -> None:
    """``list_catalog`` raises when the catalog lacks ``browse``."""
    with pytest.raises(CapabilityNotSupportedError):
        await _catalog_facade().list_catalog()


async def test_search_catalog_without_capability_raises() -> None:
    """``search_catalog`` raises when the catalog lacks ``search``."""
    with pytest.raises(CapabilityNotSupportedError):
        await _catalog_facade().search_catalog(NotBlankStr("query"))


async def test_get_catalog_entry_without_capability_raises() -> None:
    """``get_catalog_entry`` raises when the catalog lacks ``get_entry``."""
    with pytest.raises(CapabilityNotSupportedError):
        await _catalog_facade().get_catalog_entry(NotBlankStr("entry-1"))


async def test_get_catalog_entry_returns_none_on_miss() -> None:
    """``get_catalog_entry`` maps the service miss-error to ``None``.

    ``CatalogService.get_entry`` raises ``CatalogEntryNotFoundError`` on
    an unknown id; the facade's documented contract is None-on-miss.
    """

    async def _raise_missing(entry_id: str) -> object:
        msg = f"no catalog entry {entry_id!r}"
        raise CatalogEntryNotFoundError(msg)

    facade = MCPCatalogFacadeService(
        catalog=SimpleNamespace(get_entry=_raise_missing),  # type: ignore[arg-type]
        installations=SimpleNamespace(),
    )
    assert await facade.get_catalog_entry(NotBlankStr("missing")) is None


async def test_install_catalog_entry_routes_to_catalog_service() -> None:
    """``install_catalog_entry`` delegates to ``CatalogService.install``.

    The connection-aware validation (type match, credential-map, dialect)
    lives on ``CatalogService.install``, so the facade must call it with the
    bound ``connection_name`` + the connection catalog + the installations
    repo, not a non-existent repo ``install`` method.
    """
    install = AsyncMock(return_value="install-result")
    conn_catalog = SimpleNamespace()
    installs_repo = SimpleNamespace()
    facade = MCPCatalogFacadeService(
        catalog=SimpleNamespace(install=install),  # type: ignore[arg-type]
        installations=installs_repo,  # type: ignore[arg-type]
        connection_catalog=conn_catalog,  # type: ignore[arg-type]
    )
    result = await facade.install_catalog_entry(
        entry_id=NotBlankStr("brave-search-mcp"),
        connection_name=NotBlankStr("brave"),
        actor_id=NotBlankStr("actor-1"),
    )
    assert result == "install-result"
    install.assert_awaited_once_with(
        "brave-search-mcp",
        "brave",
        connection_catalog=conn_catalog,
        installations_repo=installs_repo,
    )


async def test_install_catalog_entry_without_capability_raises() -> None:
    """``install_catalog_entry`` raises when the catalog lacks ``install``."""
    with pytest.raises(CapabilityNotSupportedError):
        await _catalog_facade().install_catalog_entry(
            entry_id=NotBlankStr("entry-1"),
            connection_name=None,
            actor_id=NotBlankStr("actor-1"),
        )


async def test_uninstall_catalog_entry_routes_to_catalog_service() -> None:
    """``uninstall_catalog_entry`` delegates to ``CatalogService.uninstall``."""
    uninstall = AsyncMock(return_value=True)
    installs_repo = SimpleNamespace()
    facade = MCPCatalogFacadeService(
        catalog=SimpleNamespace(uninstall=uninstall),  # type: ignore[arg-type]
        installations=installs_repo,  # type: ignore[arg-type]
        connection_catalog=SimpleNamespace(),  # type: ignore[arg-type]
    )
    removed = await facade.uninstall_catalog_entry(
        installation_id=NotBlankStr("brave-search-mcp"),
        actor_id=NotBlankStr("actor-1"),
        reason=NotBlankStr("cleanup"),
    )
    assert removed is True
    uninstall.assert_awaited_once_with(
        "brave-search-mcp",
        installations_repo=installs_repo,
    )


async def test_uninstall_catalog_entry_without_capability_raises() -> None:
    """``uninstall_catalog_entry`` raises when the catalog lacks ``uninstall``."""
    with pytest.raises(CapabilityNotSupportedError):
        await _catalog_facade().uninstall_catalog_entry(
            installation_id=NotBlankStr("inst-1"),
            actor_id=NotBlankStr("actor-1"),
            reason=NotBlankStr("cleanup"),
        )


# ── ArtifactFacadeService ──────────────────────────────────────────


async def test_delete_artifact_without_storage_delete_raises() -> None:
    """``delete_artifact`` raises when the storage backend lacks ``delete``.

    The capability guard sits after the in-memory index lookup, so an
    artifact must be indexed first for the guard to be reached.
    """
    facade = ArtifactFacadeService(storage=SimpleNamespace())
    record = await facade.create_artifact(
        name=NotBlankStr("report.pdf"),
        content_type=NotBlankStr("application/pdf"),
        size_bytes=1024,
        storage_ref=NotBlankStr("blob/report.pdf"),
        actor_id=NotBlankStr("actor-1"),
    )
    with pytest.raises(CapabilityNotSupportedError):
        await facade.delete_artifact(
            artifact_id=NotBlankStr(str(record.id)),
            actor_id=NotBlankStr("actor-1"),
            reason=NotBlankStr("obsolete"),
        )


# ── OntologyFacadeService ──────────────────────────────────────────


def _ontology_facade() -> OntologyFacadeService:
    """Build an ontology facade whose backing service exposes nothing."""
    return OntologyFacadeService(ontology=SimpleNamespace())  # type: ignore[arg-type]


async def test_list_entities_without_capability_raises() -> None:
    """``list_entities`` raises when the ontology lacks ``list_entities``."""
    with pytest.raises(CapabilityNotSupportedError):
        await _ontology_facade().list_entities()


async def test_get_entity_without_capability_raises() -> None:
    """``get_entity`` raises when the ontology lacks ``get_entity``."""
    with pytest.raises(CapabilityNotSupportedError):
        await _ontology_facade().get_entity(NotBlankStr("ent-1"))


async def test_get_relationships_without_capability_raises() -> None:
    """``get_relationships`` raises when the ontology lacks ``get_relationships``."""
    with pytest.raises(CapabilityNotSupportedError):
        await _ontology_facade().get_relationships(NotBlankStr("ent-1"))


async def test_ontology_search_without_capability_raises() -> None:
    """``search`` raises when the ontology lacks ``search``."""
    with pytest.raises(CapabilityNotSupportedError):
        await _ontology_facade().search(NotBlankStr("query"))
