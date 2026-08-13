"""Shared fixtures for terminal tool tests."""

import pytest

from synthorg.tools.sandbox.result import SandboxResult
from synthorg.tools.terminal.config import TerminalConfig
from synthorg.tools.terminal.shell_command import ShellCommandTool
from tests._shared import FakeSandbox


@pytest.fixture
def mock_sandbox() -> FakeSandbox:
    return FakeSandbox(SandboxResult(stdout="hello world", stderr="", returncode=0))


@pytest.fixture
def shell_tool(mock_sandbox: FakeSandbox) -> ShellCommandTool:
    return ShellCommandTool(sandbox=mock_sandbox)


@pytest.fixture
def restricted_tool(mock_sandbox: FakeSandbox) -> ShellCommandTool:
    config = TerminalConfig(
        command_allowlist=("ls", "cat", "echo"),
    )
    return ShellCommandTool(sandbox=mock_sandbox, config=config)
