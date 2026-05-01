"""Integration tests for tool factory + config loading pipeline."""

from pathlib import Path

import pytest

from synthorg.config.loader import load_config_from_string
from synthorg.tools.factory import (
    build_default_tools,
    build_default_tools_from_config,
)
from synthorg.tools.git_tools import GitCloneTool
from synthorg.tools.registry import ToolRegistry
from tests._shared.web_timeout import DEFAULT_TEST_WEB_REQUEST_TIMEOUT

_EXPECTED_TOOL_COUNT: int = 16


@pytest.mark.integration
class TestToolFactoryConfigIntegration:
    """Integration: YAML config -> RootConfig -> factory -> tool instances."""

    def test_yaml_with_allowlist_wires_to_clone_tool(
        self,
        tmp_path: Path,
    ) -> None:
        """YAML hostname_allowlist propagates to GitCloneTool."""
        yaml_str = """\
company_name: test-corp
git_clone:
  hostname_allowlist:
    - internal.example.com
"""
        config = load_config_from_string(yaml_str)
        tools = build_default_tools_from_config(
            workspace=tmp_path,
            config=config,
            web_request_timeout=DEFAULT_TEST_WEB_REQUEST_TIMEOUT,
        )
        clone = next(t for t in tools if t.name == "git_clone")
        assert isinstance(clone, GitCloneTool)
        assert clone._network_policy.hostname_allowlist == ("internal.example.com",)

    def test_yaml_empty_git_clone_uses_defaults(
        self,
        tmp_path: Path,
    ) -> None:
        """Empty git_clone section yields default policy."""
        yaml_str = """\
company_name: test-corp
git_clone: {}
"""
        config = load_config_from_string(yaml_str)
        tools = build_default_tools_from_config(
            workspace=tmp_path,
            config=config,
            web_request_timeout=DEFAULT_TEST_WEB_REQUEST_TIMEOUT,
        )
        clone = next(t for t in tools if t.name == "git_clone")
        assert isinstance(clone, GitCloneTool)
        assert clone._network_policy.hostname_allowlist == ()
        assert clone._network_policy.block_private_ips is True

    def test_yaml_block_private_ips_false_wires_to_clone_tool(
        self,
        tmp_path: Path,
    ) -> None:
        """YAML block_private_ips=false propagates to GitCloneTool."""
        yaml_str = """\
company_name: test-corp
git_clone:
  block_private_ips: false
"""
        config = load_config_from_string(yaml_str)
        tools = build_default_tools_from_config(
            workspace=tmp_path,
            config=config,
            web_request_timeout=DEFAULT_TEST_WEB_REQUEST_TIMEOUT,
        )
        clone = next(t for t in tools if t.name == "git_clone")
        assert isinstance(clone, GitCloneTool)
        assert clone._network_policy.block_private_ips is False

    def test_yaml_absent_git_clone_uses_defaults(
        self,
        tmp_path: Path,
    ) -> None:
        """YAML without git_clone key uses default policy."""
        yaml_str = """\
company_name: test-corp
"""
        config = load_config_from_string(yaml_str)
        tools = build_default_tools_from_config(
            workspace=tmp_path,
            config=config,
            web_request_timeout=DEFAULT_TEST_WEB_REQUEST_TIMEOUT,
        )
        clone = next(t for t in tools if t.name == "git_clone")
        assert isinstance(clone, GitCloneTool)
        assert clone._network_policy.hostname_allowlist == ()
        assert clone._network_policy.block_private_ips is True

    def test_factory_tools_form_valid_registry(
        self,
        tmp_path: Path,
    ) -> None:
        """Factory output can be wrapped in ToolRegistry without errors."""
        tools = build_default_tools(
            workspace=tmp_path,
            web_request_timeout=DEFAULT_TEST_WEB_REQUEST_TIMEOUT,
        )
        registry = ToolRegistry(tools)
        all_tools = list(registry.all_tools())
        assert len(all_tools) == _EXPECTED_TOOL_COUNT
        tool_names = {t.name for t in all_tools}
        assert "compact_context" in tool_names
        assert "read_file" in tool_names
        assert "write_file" in tool_names
