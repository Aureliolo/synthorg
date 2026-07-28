"""Unit tests for the MCP server catalog."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from synthorg.core.types import NotBlankStr
from synthorg.integrations.connections.models import (
    AuthMethod,
    CatalogEntry,
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


def _connectionless_catalog(tmp_path: Path) -> CatalogService:
    """A ``CatalogService`` over one synthetic connectionless entry.

    The bundled catalog ships only connection-gated servers, so the
    connectionless install / merge paths are exercised against a synthetic
    ``test-local-mcp`` entry written to a temp catalog file.
    """
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text(
        json.dumps(
            {
                "servers": [
                    {
                        "id": "test-local-mcp",
                        "name": "Test Local",
                        "description": "Connectionless test server",
                        "npm_package": "@example/server-test-local",
                        "npm_version": "1.0.0",
                        "required_connection_type": None,
                        "transport": "stdio",
                        "capabilities": ["alpha", "beta"],
                        "tags": ["test", "local"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return CatalogService(catalog_path=catalog_path)


def _dialect_catalog(tmp_path: Path) -> CatalogService:
    """A ``CatalogService`` over one synthetic database entry with a dialect.

    The bundled catalog ships no database-typed MCP server, so the
    dialect-gated install path is exercised against a synthetic
    ``test-db-mcp`` entry requiring the ``postgres`` dialect.
    """
    catalog_path = tmp_path / "db-catalog.json"
    catalog_path.write_text(
        json.dumps(
            {
                "servers": [
                    {
                        "id": "test-db-mcp",
                        "name": "Test DB",
                        "description": "Database test server",
                        "npm_package": "@example/server-test-db",
                        "npm_version": "1.0.0",
                        "required_connection_type": "database",
                        "required_dialect": "postgres",
                        "transport": "stdio",
                        "capabilities": ["sql_query"],
                        "tags": ["test", "database"],
                        "credential_env_map": {"password": "PGPASSWORD"},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return CatalogService(catalog_path=catalog_path)


@pytest.mark.unit
class TestCatalogService:
    """Tests for the bundled MCP catalog service."""

    async def test_browse_returns_entries(self) -> None:
        service = CatalogService()
        entries = await service.browse()
        assert len(entries) == 1

    async def test_browse_entries_have_required_fields(self) -> None:
        service = CatalogService()
        entries = await service.browse()
        for entry in entries:
            assert entry.id
            assert entry.name
            assert entry.transport

    async def test_search_by_name(self) -> None:
        service = CatalogService()
        results = await service.search("brave")
        assert len(results) >= 1
        assert any(e.id == "brave-search-mcp" for e in results)

    async def test_search_by_tag(self) -> None:
        service = CatalogService()
        results = await service.search("web")
        assert len(results) >= 1

    async def test_search_case_insensitive(self) -> None:
        service = CatalogService()
        results = await service.search("BRAVE")
        assert len(results) >= 1

    async def test_search_no_results(self) -> None:
        service = CatalogService()
        results = await service.search("zzz_nonexistent_zzz")
        assert len(results) == 0

    async def test_get_entry_found(self) -> None:
        service = CatalogService()
        entry = await service.get_entry("brave-search-mcp")
        assert entry.name == "Brave Search"

    async def test_get_entry_not_found(self) -> None:
        service = CatalogService()
        with pytest.raises(CatalogEntryNotFoundError):
            await service.get_entry("nonexistent")


@pytest.mark.unit
class TestCatalogEntryValidation:
    """Model invariants on ``CatalogEntry``."""

    def test_stdio_requires_npm_version(self) -> None:
        with pytest.raises(ValidationError, match="npm_version"):
            CatalogEntry(
                id="x",
                name="X",
                npm_package="@example/server-x",
                transport="stdio",
            )

    @pytest.mark.parametrize("version", ["latest", "^1.0.0", "~1.2", "1.x", "1.2"])
    def test_npm_version_must_be_exact(self, version: str) -> None:
        with pytest.raises(ValidationError, match="exact published version"):
            CatalogEntry(
                id="x",
                name="X",
                npm_package="@example/server-x",
                npm_version=version,
                transport="stdio",
            )

    @pytest.mark.parametrize("version", ["1.2.3", "2025.4.8", "1.2.3-beta.1"])
    def test_exact_npm_version_accepted(self, version: str) -> None:
        entry = CatalogEntry(
            id="x",
            name="X",
            npm_package="@example/server-x",
            npm_version=version,
            transport="stdio",
        )
        assert entry.npm_version == version

    def test_required_dialect_rejected_on_non_database_entry(self) -> None:
        with pytest.raises(ValidationError, match="only valid on a database entry"):
            CatalogEntry(
                id="x",
                name="X",
                npm_package="@example/server-x",
                npm_version="1.0.0",
                transport="stdio",
                required_connection_type=ConnectionType.GITHUB,
                required_dialect="postgres",
            )

    def test_required_dialect_allowed_on_database_entry(self) -> None:
        entry = CatalogEntry(
            id="x",
            name="X",
            npm_package="@example/server-x",
            npm_version="1.0.0",
            transport="stdio",
            required_connection_type=ConnectionType.DATABASE,
            required_dialect="postgres",
        )
        assert entry.required_dialect == "postgres"

    @pytest.mark.parametrize("env_var", ["LD_PRELOAD", "NODE_OPTIONS", "PATH", "a b"])
    def test_dangerous_credential_env_var_name_rejected(self, env_var: str) -> None:
        with pytest.raises(ValidationError):
            CatalogEntry(
                id="x",
                name="X",
                npm_package="@example/server-x",
                npm_version="1.0.0",
                transport="stdio",
                credential_env_map={"token": env_var},
            )

    def test_safe_credential_env_var_name_accepted(self) -> None:
        entry = CatalogEntry(
            id="x",
            name="X",
            npm_package="@example/server-x",
            npm_version="1.0.0",
            transport="stdio",
            credential_env_map={"token": "GITHUB_PERSONAL_ACCESS_TOKEN"},
        )
        assert entry.credential_env_map["token"] == "GITHUB_PERSONAL_ACCESS_TOKEN"

    def test_credential_map_rejected_on_streamable_http(self) -> None:
        """A credential map on a non-stdio entry would be uninstallable."""
        with pytest.raises(ValidationError, match="only supported on the stdio"):
            CatalogEntry(
                id="x",
                name="X",
                transport="streamable_http",
                credential_env_map={"token": "GITHUB_PERSONAL_ACCESS_TOKEN"},
            )


class FakeConnectionCatalog:
    """Minimal in-memory catalog used by install tests."""

    def __init__(self) -> None:
        self._store: dict[str, Connection] = {}
        self._creds: dict[str, dict[str, str]] = {}

    def add(self, conn: Connection, creds: dict[str, str] | None = None) -> None:
        self._store[conn.name] = conn
        if creds is not None:
            self._creds[conn.name] = creds

    async def get(self, name: str) -> Connection | None:
        return self._store.get(name)

    async def get_credentials(self, name: str) -> dict[str, str]:
        return dict(self._creds.get(name, {}))


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

    async def test_install_connectionless_entry(self, tmp_path: Path) -> None:
        service = _connectionless_catalog(tmp_path)
        repo = InMemoryMcpInstallationRepository()
        result = await service.install(
            "test-local-mcp",
            None,
            connection_catalog=None,
            installations_repo=repo,
        )
        assert result.catalog_entry_id == "test-local-mcp"
        assert result.server_name == "Test Local"
        assert result.connection_name is None
        # The synthetic entry declares two capabilities (alpha, beta); the
        # tool_count must reflect them exactly, not merely be non-zero.
        assert result.tool_count == 2
        stored = await repo.get(NotBlankStr("test-local-mcp"))
        assert stored is not None
        assert stored.connection_name is None

    async def test_install_with_matching_connection(self) -> None:
        service = CatalogService()
        repo = InMemoryMcpInstallationRepository()
        catalog = FakeConnectionCatalog()
        catalog.add(
            _make_connection("primary-search", ConnectionType.GENERIC_HTTP),
            {"token": "k"},
        )

        result = await service.install(
            "brave-search-mcp",
            "primary-search",
            connection_catalog=catalog,  # type: ignore[arg-type]
            installations_repo=repo,
        )
        assert result.connection_name == "primary-search"
        stored = await repo.get(NotBlankStr("brave-search-mcp"))
        assert stored is not None
        assert stored.connection_name == "primary-search"

    async def test_install_without_the_mapped_credential_is_refused(self) -> None:
        # The connection exists and is the right type, but stores nothing
        # under the field the entry maps: injection would be a silent no-op
        # and the server would launch unauthenticated.
        service = CatalogService()
        repo = InMemoryMcpInstallationRepository()
        catalog = FakeConnectionCatalog()
        catalog.add(
            _make_connection("primary-search", ConnectionType.GENERIC_HTTP),
            {"api_key": "k"},
        )

        with pytest.raises(InvalidConnectionAuthError, match="unauthenticated"):
            await service.install(
                "brave-search-mcp",
                "primary-search",
                connection_catalog=catalog,  # type: ignore[arg-type]
                installations_repo=repo,
            )

    async def test_install_idempotent(self, tmp_path: Path) -> None:
        service = _connectionless_catalog(tmp_path)
        repo = InMemoryMcpInstallationRepository()
        first = await service.install(
            "test-local-mcp",
            None,
            connection_catalog=None,
            installations_repo=repo,
        )
        second = await service.install(
            "test-local-mcp",
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
                "brave-search-mcp",
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
                "brave-search-mcp",
                "missing",
                connection_catalog=catalog,  # type: ignore[arg-type]
                installations_repo=repo,
            )

    async def test_install_connection_type_mismatch(self) -> None:
        service = CatalogService()
        repo = InMemoryMcpInstallationRepository()
        catalog = FakeConnectionCatalog()
        catalog.add(_make_connection("wrong-type", ConnectionType.GITHUB))
        with pytest.raises(InvalidConnectionAuthError):
            await service.install(
                "brave-search-mcp",
                "wrong-type",
                connection_catalog=catalog,  # type: ignore[arg-type]
                installations_repo=repo,
            )

    async def test_install_dialect_match_succeeds(self, tmp_path: Path) -> None:
        """A database entry binds a connection whose dialect matches."""
        service = _dialect_catalog(tmp_path)
        repo = InMemoryMcpInstallationRepository()
        catalog = FakeConnectionCatalog()
        catalog.add(
            _make_connection("pg-conn", ConnectionType.DATABASE),
            creds={"dialect": "postgres", "password": "pw"},
        )
        result = await service.install(
            "test-db-mcp",
            "pg-conn",
            connection_catalog=catalog,  # type: ignore[arg-type]
            installations_repo=repo,
        )
        assert result.connection_name == "pg-conn"

    async def test_install_dialect_mismatch_rejected(self, tmp_path: Path) -> None:
        """A sqlite-dialect connection cannot bind a postgres-dialect entry."""
        service = _dialect_catalog(tmp_path)
        repo = InMemoryMcpInstallationRepository()
        catalog = FakeConnectionCatalog()
        catalog.add(
            _make_connection("sqlite-conn", ConnectionType.DATABASE),
            creds={"dialect": "sqlite"},
        )
        with pytest.raises(InvalidConnectionAuthError):
            await service.install(
                "test-db-mcp",
                "sqlite-conn",
                connection_catalog=catalog,  # type: ignore[arg-type]
                installations_repo=repo,
            )

    async def test_install_dialect_match_strips_whitespace(
        self, tmp_path: Path
    ) -> None:
        """A stored dialect with surrounding whitespace still matches."""
        service = _dialect_catalog(tmp_path)
        repo = InMemoryMcpInstallationRepository()
        catalog = FakeConnectionCatalog()
        catalog.add(
            _make_connection("pg-conn", ConnectionType.DATABASE),
            creds={"dialect": "  postgres  ", "password": "pw"},
        )
        result = await service.install(
            "test-db-mcp",
            "pg-conn",
            connection_catalog=catalog,  # type: ignore[arg-type]
            installations_repo=repo,
        )
        assert result.connection_name == "pg-conn"

    async def test_uninstall_existing(self, tmp_path: Path) -> None:
        service = _connectionless_catalog(tmp_path)
        repo = InMemoryMcpInstallationRepository()
        await service.install(
            "test-local-mcp",
            None,
            connection_catalog=None,
            installations_repo=repo,
        )
        removed = await service.uninstall(
            "test-local-mcp",
            installations_repo=repo,
        )
        assert removed is True
        assert await repo.get(NotBlankStr("test-local-mcp")) is None

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
        entry = await service.get_entry("brave-search-mcp")
        server = installation_to_server_config(entry, "primary-search")
        assert server.name == "brave-search-mcp"
        assert server.transport == "stdio"
        assert server.command == "npx"
        assert "-y" in server.args
        # The npx spec is version-pinned so a reconnect can never pull a
        # newly-published (potentially compromised) 'latest'.
        assert f"{entry.npm_package}@{entry.npm_version}" in server.args
        # The connection name is recorded on an explicit field (secrets are
        # resolved and injected at connect time, never persisted here).
        assert server.connection_name == "primary-search"
        assert server.credential_env_map == {"token": "BRAVE_API_KEY"}
        assert "SYNTHORG_CONNECTION" not in server.env

    async def test_installation_to_server_connectionless(self, tmp_path: Path) -> None:
        service = _connectionless_catalog(tmp_path)
        entry = await service.get_entry("test-local-mcp")
        server = installation_to_server_config(entry, None)
        assert server.name == "test-local-mcp"
        assert server.env == {}

    async def test_merge_skips_duplicates(self) -> None:
        service = CatalogService()
        entries = await service.browse()
        entries_by_id = {e.id: e for e in entries}
        base = MCPConfig(
            servers=(
                MCPServerConfig(
                    name="brave-search-mcp",
                    transport="stdio",
                    command="custom-command",
                    args=("--existing",),
                ),
            ),
        )
        install = McpInstallation(
            catalog_entry_id=NotBlankStr("brave-search-mcp"),
            connection_name=NotBlankStr("primary-search"),
            installed_at=datetime.now(UTC),
        )
        merged = merge_installed_servers(base, (install,), entries_by_id)
        # Base config wins for overlapping names.
        assert len(merged.servers) == 1
        assert merged.servers[0].command == "custom-command"

    async def test_merge_adds_new_entries(self, tmp_path: Path) -> None:
        service = _connectionless_catalog(tmp_path)
        entries = await service.browse()
        entries_by_id = {e.id: e for e in entries}
        base = MCPConfig(servers=())
        install = McpInstallation(
            catalog_entry_id=NotBlankStr("test-local-mcp"),
            connection_name=None,
            installed_at=datetime.now(UTC),
        )
        merged = merge_installed_servers(base, (install,), entries_by_id)
        assert len(merged.servers) == 1
        assert merged.servers[0].name == "test-local-mcp"

    async def test_merge_skips_unknown_entry(self) -> None:
        base = MCPConfig(servers=())
        install = McpInstallation(
            catalog_entry_id=NotBlankStr("not-in-catalog"),
            connection_name=None,
            installed_at=datetime.now(UTC),
        )
        merged = merge_installed_servers(base, (install,), {})
        assert merged.servers == ()
