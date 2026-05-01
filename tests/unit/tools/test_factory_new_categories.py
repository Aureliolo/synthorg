"""Unit tests for tool factory with new categories (web, database, terminal)."""

from pathlib import Path

import pytest

from synthorg.tools.database.config import DatabaseConfig, DatabaseConnectionConfig
from synthorg.tools.factory import build_default_tools
from synthorg.tools.network_validator import NetworkPolicy
from synthorg.tools.terminal.config import TerminalConfig
from tests._shared.web_timeout import DEFAULT_TEST_WEB_REQUEST_TIMEOUT


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    path = tmp_path / "workspace"
    path.mkdir()
    return path


class TestFactoryWebTools:
    """Tests for web tool creation in the factory."""

    @pytest.mark.unit
    def test_web_tools_included_by_default(self, workspace: Path) -> None:
        tools = build_default_tools(
            workspace=workspace,
            web_request_timeout=DEFAULT_TEST_WEB_REQUEST_TIMEOUT,
        )
        names = {t.name for t in tools}
        assert "http_request" in names
        assert "html_parser" in names

    @pytest.mark.unit
    def test_web_search_excluded_without_provider(self, workspace: Path) -> None:
        tools = build_default_tools(
            workspace=workspace,
            web_request_timeout=DEFAULT_TEST_WEB_REQUEST_TIMEOUT,
        )
        names = {t.name for t in tools}
        assert "web_search" not in names

    @pytest.mark.unit
    def test_web_search_included_with_provider(self, workspace: Path) -> None:
        from tests.unit.tools.web.conftest import MockSearchProvider

        tools = build_default_tools(
            workspace=workspace,
            web_request_timeout=DEFAULT_TEST_WEB_REQUEST_TIMEOUT,
            web_search_provider=MockSearchProvider(),
        )
        names = {t.name for t in tools}
        assert "web_search" in names

    @pytest.mark.unit
    def test_custom_network_policy(self, workspace: Path) -> None:
        policy = NetworkPolicy(block_private_ips=False)
        tools = build_default_tools(
            workspace=workspace,
            web_request_timeout=DEFAULT_TEST_WEB_REQUEST_TIMEOUT,
            web_network_policy=policy,
        )
        http_tool = next(t for t in tools if t.name == "http_request")
        from synthorg.tools.web.http_request import HttpRequestTool

        assert isinstance(http_tool, HttpRequestTool)
        assert http_tool._network_policy.block_private_ips is False


class TestFactoryDatabaseTools:
    """Tests for database tool creation in the factory."""

    @pytest.mark.unit
    def test_no_database_tools_by_default(self, workspace: Path) -> None:
        tools = build_default_tools(
            workspace=workspace,
            web_request_timeout=DEFAULT_TEST_WEB_REQUEST_TIMEOUT,
        )
        names = {t.name for t in tools}
        assert "sql_query" not in names
        assert "schema_inspect" not in names

    @pytest.mark.unit
    def test_database_tools_with_config(self, workspace: Path, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        config = DatabaseConfig(
            connections={
                "default": DatabaseConnectionConfig(
                    database_path=str(db_path),
                ),
            },
        )
        tools = build_default_tools(
            workspace=workspace,
            web_request_timeout=DEFAULT_TEST_WEB_REQUEST_TIMEOUT,
            database_config=config,
        )
        names = {t.name for t in tools}
        assert "sql_query" in names
        assert "schema_inspect" in names

    @pytest.mark.unit
    def test_empty_connections_skips_tools(self, workspace: Path) -> None:
        config = DatabaseConfig(connections={})
        tools = build_default_tools(
            workspace=workspace,
            web_request_timeout=DEFAULT_TEST_WEB_REQUEST_TIMEOUT,
            database_config=config,
        )
        names = {t.name for t in tools}
        assert "sql_query" not in names
        assert "schema_inspect" not in names


class TestFactoryTerminalTools:
    """Tests for terminal tool creation in the factory."""

    @pytest.mark.unit
    def test_terminal_tool_included_by_default(self, workspace: Path) -> None:
        tools = build_default_tools(
            workspace=workspace,
            web_request_timeout=DEFAULT_TEST_WEB_REQUEST_TIMEOUT,
        )
        names = {t.name for t in tools}
        assert "shell_command" in names

    @pytest.mark.unit
    def test_custom_terminal_config(self, workspace: Path) -> None:
        config = TerminalConfig(command_allowlist=("ls", "cat"))
        tools = build_default_tools(
            workspace=workspace,
            web_request_timeout=DEFAULT_TEST_WEB_REQUEST_TIMEOUT,
            terminal_config=config,
        )
        shell_tool = next(t for t in tools if t.name == "shell_command")
        from synthorg.tools.terminal.shell_command import ShellCommandTool

        assert isinstance(shell_tool, ShellCommandTool)
        assert shell_tool.config.command_allowlist == ("ls", "cat")


class TestFactoryToolCount:
    """Tests for overall factory tool counts."""

    @pytest.mark.unit
    def test_default_tool_count(self, workspace: Path) -> None:
        """Default: 5 fs + 6 git + 2 web + 1 terminal + 1 context + 1 echo."""
        tools = build_default_tools(
            workspace=workspace,
            web_request_timeout=DEFAULT_TEST_WEB_REQUEST_TIMEOUT,
        )
        assert len(tools) == 16

    @pytest.mark.unit
    def test_tools_sorted_by_name(self, workspace: Path) -> None:
        tools = build_default_tools(
            workspace=workspace,
            web_request_timeout=DEFAULT_TEST_WEB_REQUEST_TIMEOUT,
        )
        names = [t.name for t in tools]
        assert names == sorted(names)
