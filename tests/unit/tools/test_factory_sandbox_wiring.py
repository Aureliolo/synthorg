"""Unit tests for per-category sandbox wiring in build_default_tools_from_config."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from synthorg.config.schema import RootConfig
from synthorg.tools._git_base import _BaseGitTool
from synthorg.tools.base import BaseTool
from synthorg.tools.factory import build_default_tools_from_config
from synthorg.tools.file_system import BaseFileSystemTool
from synthorg.tools.sandbox.protocol import SandboxBackend
from synthorg.tools.sandbox.sandboxing_config import SandboxingConfig
from tests._shared.web_timeout import DEFAULT_TEST_WEB_REQUEST_TIMEOUT

_GIT_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "git_status",
        "git_log",
        "git_diff",
        "git_branch",
        "git_commit",
        "git_clone",
    }
)

_FS_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "read_file",
        "write_file",
        "edit_file",
        "list_directory",
        "delete_file",
    }
)

_WEB_TOOL_NAMES: frozenset[str] = frozenset({"http_request", "html_parser"})
_TERMINAL_TOOL_NAMES: frozenset[str] = frozenset({"shell_command"})

_EXPECTED_TOOL_COUNT: int = (
    len(_GIT_TOOL_NAMES)
    + len(_FS_TOOL_NAMES)
    + len(_WEB_TOOL_NAMES)
    + len(_TERMINAL_TOOL_NAMES)
    + 1  # compact_context (context management)
    + 1  # echo (reference tool)
    + 1  # code_runner (conditional on CODE_EXECUTION sandbox)
)


def _git_tools(
    tools: tuple[BaseTool, ...],
) -> list[_BaseGitTool]:
    """Filter and return git tools, asserting expected count."""
    matched = [t for t in tools if t.name in _GIT_TOOL_NAMES]
    assert len(matched) == len(_GIT_TOOL_NAMES)
    return matched  # type: ignore[return-value]  # narrowed by name membership check


@pytest.mark.unit
class TestFactorySandboxWiring:
    """Tests for per-category sandbox resolution in build_default_tools_from_config."""

    @patch(
        "synthorg.tools.sandbox.factory.SubprocessSandbox",
    )
    def test_default_config_gives_git_tools_subprocess_sandbox(
        self,
        mock_subprocess_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Default SandboxingConfig gives all git tools a subprocess sandbox."""
        mock_instance = MagicMock(spec=SandboxBackend)
        mock_subprocess_cls.return_value = mock_instance

        config = RootConfig(company_name="test-corp")
        tools = build_default_tools_from_config(
            workspace=tmp_path,
            config=config,
            web_request_timeout=DEFAULT_TEST_WEB_REQUEST_TIMEOUT,
        )

        for tool in _git_tools(tools):
            assert tool._sandbox is mock_instance

    @patch(
        "synthorg.tools.sandbox.factory.DockerSandbox",
    )
    def test_docker_default_gives_git_tools_docker_sandbox(
        self,
        mock_docker_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Docker default SandboxingConfig gives all git tools a docker sandbox."""
        mock_instance = MagicMock(spec=SandboxBackend)
        mock_docker_cls.return_value = mock_instance

        sandboxing = SandboxingConfig(default_backend="docker")
        config = RootConfig(
            company_name="test-corp",
            sandboxing=sandboxing,
        )
        tools = build_default_tools_from_config(
            workspace=tmp_path,
            config=config,
            web_request_timeout=DEFAULT_TEST_WEB_REQUEST_TIMEOUT,
        )

        for tool in _git_tools(tools):
            assert tool._sandbox is mock_instance

    @patch(
        "synthorg.tools.sandbox.factory.SubprocessSandbox",
    )
    def test_explicit_sandbox_backends_map_used(
        self,
        mock_subprocess_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Explicit sandbox_backends= map is used for resolution."""
        mock_backend = MagicMock(spec=SandboxBackend)
        config = RootConfig(company_name="test-corp")

        tools = build_default_tools_from_config(
            workspace=tmp_path,
            config=config,
            web_request_timeout=DEFAULT_TEST_WEB_REQUEST_TIMEOUT,
            sandbox_backends={"subprocess": mock_backend},
        )

        for tool in _git_tools(tools):
            assert tool._sandbox is mock_backend
        # Should NOT auto-build backends when map is provided
        mock_subprocess_cls.assert_not_called()

    @patch(
        "synthorg.tools.sandbox.factory.SubprocessSandbox",
    )
    def test_auto_build_from_config_when_neither_provided(
        self,
        mock_subprocess_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Auto-builds backends from config when no sandbox args provided."""
        mock_instance = MagicMock(spec=SandboxBackend)
        mock_subprocess_cls.return_value = mock_instance

        config = RootConfig(company_name="test-corp")
        tools = build_default_tools_from_config(
            workspace=tmp_path,
            config=config,
            web_request_timeout=DEFAULT_TEST_WEB_REQUEST_TIMEOUT,
        )

        # Should have built subprocess backend from config
        mock_subprocess_cls.assert_called_once()

        for tool in _git_tools(tools):
            assert tool._sandbox is mock_instance

    @patch(
        "synthorg.tools.sandbox.factory.SubprocessSandbox",
    )
    def test_file_system_tools_unaffected(
        self,
        mock_subprocess_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        """File system tools have no sandbox attribute regardless of config."""
        mock_subprocess_cls.return_value = MagicMock(
            spec=SandboxBackend,
        )
        config = RootConfig(company_name="test-corp")

        tools = build_default_tools_from_config(
            workspace=tmp_path,
            config=config,
            web_request_timeout=DEFAULT_TEST_WEB_REQUEST_TIMEOUT,
        )

        fs_tools = [t for t in tools if t.name in _FS_TOOL_NAMES]
        assert len(fs_tools) == len(_FS_TOOL_NAMES)
        for tool in fs_tools:
            assert isinstance(tool, BaseFileSystemTool)
            assert not hasattr(tool, "_sandbox")

    @patch(
        "synthorg.tools.sandbox.factory.SubprocessSandbox",
    )
    def test_tool_count_unchanged_with_auto_build(
        self,
        mock_subprocess_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Sandbox wiring doesn't change the number of tools returned."""
        mock_subprocess_cls.return_value = MagicMock(spec=SandboxBackend)
        config = RootConfig(company_name="test-corp")

        # Explicit sandbox_backends path
        tools_explicit = build_default_tools_from_config(
            workspace=tmp_path,
            config=config,
            web_request_timeout=DEFAULT_TEST_WEB_REQUEST_TIMEOUT,
            sandbox_backends={
                "subprocess": MagicMock(spec=SandboxBackend),
            },
        )

        # Auto-build from config path
        tools_auto = build_default_tools_from_config(
            workspace=tmp_path,
            config=config,
            web_request_timeout=DEFAULT_TEST_WEB_REQUEST_TIMEOUT,
        )

        assert len(tools_explicit) == _EXPECTED_TOOL_COUNT
        assert len(tools_auto) == _EXPECTED_TOOL_COUNT
