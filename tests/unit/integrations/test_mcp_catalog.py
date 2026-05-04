"""Unit tests for the MCP server catalog."""

from datetime import UTC, datetime

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.integrations.connections.models import (
    AuthMethod,
    Connection,
    ConnectionType,
)
from synthorg.integrations.errors import (
    CatalogEntryNotFoundError,
    ConnectionNotFoundError,
    InvalidConnectionAuthError,
)
from synthorg.integrations.mcp_catalog.in_memory_installations import (
    InMemoryMcpInstallationRepository,
)
from synthorg.integrations.mcp_catalog.install import (
    installation_to_server_config,
    merge_installed_servers,
)
from synthorg.integrations.mcp_catalog.installations import McpInstallation
from synthorg.integrations.mcp_catalog.service import CatalogService
from synthorg.tools.mcp.config import MCPConfig, MCPServerConfig


@pytest.mark.unit
class TestCatalogService:
    """Tests for the bundled MCP catalog service."""

    async def test_browse_returns_entries(self) -> None:
        service = CatalogService()
        entries = await service.browse()
        assert len(entries) >= 8

    async def test_browse_entries_have_required_fields(self) -> None:
        service = CatalogService()
        entries = await service.browse()
        for entry in entries:
            assert entry.id
            assert entry.name
            assert entry.transport

    async def test_search_by_name(self) -> None:
        service = CatalogService()
        results = await service.search("github")
        assert len(results) >= 1
        assert any(e.id == "github-mcp" for e in results)

    async def test_search_by_tag(self) -> None:
        service = CatalogService()
        results = await service.search("database")
        assert len(results) >= 1

    async def test_search_case_insensitive(self) -> None:
        service = CatalogService()
        results = await service.search("SLACK")
        assert len(results) >= 1

    async def test_search_no_results(self) -> None:
        service = CatalogService()
        results = await service.search("zzz_nonexistent_zzz")
        assert len(results) == 0

    async def test_get_entry_found(self) -> None:
        service = CatalogService()
        entry = await service.get_entry("github-mcp")
        assert entry.name == "GitHub"

    async def test_get_entry_not_found(self) -> None:
        service = CatalogService()
        with pytest.raises(CatalogEntryNotFoundError):
            await service.get_entry("nonexistent")


class FakeConnectionCatalog:
    """Minimal in-memory catalog used by install tests."""

    def __init__(self) -> None:
        self._store: dict[str, Connection] = {}

    def add(self, conn: Connection) -> None:
        self._store[conn.name] = conn

    async def get(self, name: str) -> Connection | None:
        return self._store.get(name)


def _make_connection(
    name: str,
    conn_type: ConnectionType,
) -> Connection:
    return Connection(
        name=NotBlankStr(name),
        connection_type=conn_type,
        auth_method=AuthMethod.API_KEY,
    )


@pytest.mark.unit
class TestCatalogInstall:
    """Tests for ``CatalogService.install`` and ``uninstall``."""

    async def test_install_connectionless_entry(self) -> None:
        service = CatalogService()
        repo = InMemoryMcpInstallationRepository()
        result = await service.install(
            "filesystem-mcp",
            None,
            connection_catalog=None,
            installations_repo=repo,
        )
        assert result.catalog_entry_id == "filesystem-mcp"
        assert result.server_name == "Filesystem"
        assert result.connection_name is None
        assert result.tool_count >= 1
        stored = await repo.get(NotBlankStr("filesystem-mcp"))
        assert stored is not None
        assert stored.connection_name is None

    async def test_install_with_matching_connection(self) -> None:
        service = CatalogService()
        repo = InMemoryMcpInstallationRepository()
        catalog = FakeConnectionCatalog()
        catalog.add(_make_connection("primary-gh", ConnectionType.GITHUB))

        result = await service.install(
            "github-mcp",
            "primary-gh",
            connection_catalog=catalog,  # type: ignore[arg-type]
            installations_repo=repo,
        )
        assert result.connection_name == "primary-gh"
        stored = await repo.get(NotBlankStr("github-mcp"))
        assert stored is not None
        assert stored.connection_name == "primary-gh"

    async def test_install_idempotent(self) -> None:
        service = CatalogService()
        repo = InMemoryMcpInstallationRepository()
        first = await service.install(
            "memory-mcp",
            None,
            connection_catalog=None,
            installations_repo=repo,
        )
        second = await service.install(
            "memory-mcp",
            None,
            connection_catalog=None,
            installations_repo=repo,
        )
        assert first.catalog_entry_id == second.catalog_entry_id
        # Only one row remains after the re-install.
        all_rows = await repo.list_items()
        assert len(all_rows) == 1

    async def test_install_missing_entry(self) -> None:
        service = CatalogService()
        repo = InMemoryMcpInstallationRepository()
        with pytest.raises(CatalogEntryNotFoundError):
            await service.install(
                "unknown-mcp",
                None,
                connection_catalog=None,
                installations_repo=repo,
            )

    async def test_install_required_connection_missing(self) -> None:
        service = CatalogService()
        repo = InMemoryMcpInstallationRepository()
        with pytest.raises(InvalidConnectionAuthError):
            await service.install(
                "github-mcp",
                None,
                connection_catalog=None,
                installations_repo=repo,
            )

    async def test_install_connection_not_found(self) -> None:
        service = CatalogService()
        repo = InMemoryMcpInstallationRepository()
        catalog = FakeConnectionCatalog()
        with pytest.raises(ConnectionNotFoundError):
            await service.install(
                "github-mcp",
                "missing",
                connection_catalog=catalog,  # type: ignore[arg-type]
                installations_repo=repo,
            )

    async def test_install_connection_type_mismatch(self) -> None:
        service = CatalogService()
        repo = InMemoryMcpInstallationRepository()
        catalog = FakeConnectionCatalog()
        catalog.add(_make_connection("wrong-type", ConnectionType.SLACK))
        with pytest.raises(InvalidConnectionAuthError):
            await service.install(
                "github-mcp",
                "wrong-type",
                connection_catalog=catalog,  # type: ignore[arg-type]
                installations_repo=repo,
            )

    async def test_uninstall_existing(self) -> None:
        service = CatalogService()
        repo = InMemoryMcpInstallationRepository()
        await service.install(
            "puppeteer-mcp",
            None,
            connection_catalog=None,
            installations_repo=repo,
        )
        removed = await service.uninstall(
            "puppeteer-mcp",
            installations_repo=repo,
        )
        assert removed is True
        assert await repo.get(NotBlankStr("puppeteer-mcp")) is None

    async def test_uninstall_missing_is_noop(self) -> None:
        service = CatalogService()
        repo = InMemoryMcpInstallationRepository()
        removed = await service.uninstall(
            "never-installed",
            installations_repo=repo,
        )
        assert removed is False


@pytest.mark.unit
class TestInstallMerge:
    """Tests for ``installation_to_server_config`` and ``merge_installed_servers``."""

    async def test_installation_to_server_stdio(self) -> None:
        service = CatalogService()
        entry = await service.get_entry("github-mcp")
        server = installation_to_server_config(entry, "primary-gh")
        assert server.name == "github-mcp"
        assert server.transport == "stdio"
        assert server.command == "npx"
        assert "-y" in server.args
        assert entry.npm_package in server.args
        assert server.env["SYNTHORG_CONNECTION"] == "primary-gh"

    async def test_installation_to_server_connectionless(self) -> None:
        service = CatalogService()
        entry = await service.get_entry("filesystem-mcp")
        server = installation_to_server_config(entry, None)
        assert server.name == "filesystem-mcp"
        assert server.env == {}

    async def test_merge_skips_duplicates(self) -> None:
        service = CatalogService()
        entries = await service.browse()
        entries_by_id = {e.id: e for e in entries}
        base = MCPConfig(
            servers=(
                MCPServerConfig(
                    name="github-mcp",
                    transport="stdio",
                    command="custom-command",
                    args=("--existing",),
                ),
            ),
        )
        install = McpInstallation(
            catalog_entry_id=NotBlankStr("github-mcp"),
            connection_name=NotBlankStr("primary-gh"),
            installed_at=datetime.now(UTC),
        )
        merged = merge_installed_servers(base, (install,), entries_by_id)
        # Base config wins for overlapping names.
        assert len(merged.servers) == 1
        assert merged.servers[0].command == "custom-command"

    async def test_merge_adds_new_entries(self) -> None:
        service = CatalogService()
        entries = await service.browse()
        entries_by_id = {e.id: e for e in entries}
        base = MCPConfig(servers=())
        install = McpInstallation(
            catalog_entry_id=NotBlankStr("filesystem-mcp"),
            connection_name=None,
            installed_at=datetime.now(UTC),
        )
        merged = merge_installed_servers(base, (install,), entries_by_id)
        assert len(merged.servers) == 1
        assert merged.servers[0].name == "filesystem-mcp"

    async def test_merge_skips_unknown_entry(self) -> None:
        base = MCPConfig(servers=())
        install = McpInstallation(
            catalog_entry_id=NotBlankStr("not-in-catalog"),
            connection_name=None,
            installed_at=datetime.now(UTC),
        )
        merged = merge_installed_servers(base, (install,), {})
        assert merged.servers == ()
