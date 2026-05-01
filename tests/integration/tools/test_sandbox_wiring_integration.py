"""Integration tests for sandbox backend wiring via YAML config."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from synthorg.config.loader import load_config_from_string
from synthorg.tools._git_base import _BaseGitTool
from synthorg.tools.factory import build_default_tools_from_config
from synthorg.tools.sandbox.protocol import SandboxBackend
from tests._shared.web_timeout import DEFAULT_TEST_WEB_REQUEST_TIMEOUT


@pytest.mark.integration
class TestSandboxWiringIntegration:
    """Integration: YAML config -> SandboxingConfig -> factory -> correct backends."""

    @patch(
        "synthorg.tools.sandbox.factory.SubprocessSandbox",
    )
    def test_default_yaml_wires_subprocess_to_git_tools(
        self,
        mock_subprocess_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Default YAML config (no sandboxing section) wires subprocess."""
        mock_instance = MagicMock(spec=SandboxBackend)
        mock_subprocess_cls.return_value = mock_instance

        yaml_str = """\
company_name: test-corp
"""
        config = load_config_from_string(yaml_str)
        tools = build_default_tools_from_config(
            workspace=tmp_path,
            config=config,
            web_request_timeout=DEFAULT_TEST_WEB_REQUEST_TIMEOUT,
        )

        git_tools = [t for t in tools if isinstance(t, _BaseGitTool)]
        assert len(git_tools) == 6
        for tool in git_tools:
            assert tool._sandbox is mock_instance

    @patch(
        "synthorg.tools.sandbox.factory.DockerSandbox",
    )
    def test_docker_default_yaml_wires_docker_to_git_tools(
        self,
        mock_docker_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        """YAML with docker default wires docker backend to git tools."""
        mock_instance = MagicMock(spec=SandboxBackend)
        mock_docker_cls.return_value = mock_instance

        yaml_str = """\
company_name: test-corp
sandboxing:
  default_backend: docker
"""
        config = load_config_from_string(yaml_str)
        tools = build_default_tools_from_config(
            workspace=tmp_path,
            config=config,
            web_request_timeout=DEFAULT_TEST_WEB_REQUEST_TIMEOUT,
        )

        git_tools = [t for t in tools if isinstance(t, _BaseGitTool)]
        assert len(git_tools) == 6
        for tool in git_tools:
            assert tool._sandbox is mock_instance

    @patch(
        "synthorg.tools.sandbox.factory.DockerSandbox",
    )
    @patch(
        "synthorg.tools.sandbox.factory.SubprocessSandbox",
    )
    def test_per_category_override_yaml(
        self,
        mock_subprocess_cls: MagicMock,
        mock_docker_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        """YAML with per-category override wires correct backend per category."""
        mock_subprocess = MagicMock(spec=SandboxBackend)
        mock_docker = MagicMock(spec=SandboxBackend)
        mock_subprocess_cls.return_value = mock_subprocess
        mock_docker_cls.return_value = mock_docker

        yaml_str = """\
company_name: test-corp
sandboxing:
  default_backend: subprocess
  overrides:
    version_control: docker
"""
        config = load_config_from_string(yaml_str)
        tools = build_default_tools_from_config(
            workspace=tmp_path,
            config=config,
            web_request_timeout=DEFAULT_TEST_WEB_REQUEST_TIMEOUT,
        )

        # Git tools (version_control) should get docker backend
        git_tools = [t for t in tools if isinstance(t, _BaseGitTool)]
        assert len(git_tools) == 6
        for tool in git_tools:
            assert tool._sandbox is mock_docker

    @patch(
        "synthorg.tools.sandbox.factory.SubprocessSandbox",
    )
    def test_explicit_subprocess_config_in_yaml(
        self,
        mock_subprocess_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        """YAML subprocess config values propagate to SubprocessSandbox."""
        mock_instance = MagicMock(spec=SandboxBackend)
        mock_subprocess_cls.return_value = mock_instance

        yaml_str = """\
company_name: test-corp
sandboxing:
  default_backend: subprocess
  subprocess:
    timeout_seconds: 120
"""
        config = load_config_from_string(yaml_str)
        build_default_tools_from_config(
            workspace=tmp_path,
            config=config,
            web_request_timeout=DEFAULT_TEST_WEB_REQUEST_TIMEOUT,
        )

        call_kwargs = mock_subprocess_cls.call_args.kwargs
        assert call_kwargs["config"].timeout_seconds == 120
