"""Shared fixtures for terminal tool tests."""

from collections.abc import Mapping
from pathlib import Path

import pytest

from synthorg.tools.sandbox.result import SandboxResult
from synthorg.tools.terminal.config import TerminalConfig
from synthorg.tools.terminal.shell_command import ShellCommandTool


class MockSandbox:
    """Mock sandbox backend for testing."""

    def __init__(
        self,
        *,
        stdout: str = "",
        stderr: str = "",
        returncode: int = 0,
        timed_out: bool = False,
        error: Exception | None = None,
    ) -> None:
        self._stdout = stdout
        self._stderr = stderr
        self._returncode = returncode
        self._timed_out = timed_out
        self._error = error
        self.last_command: str | None = None
        self.last_args: tuple[str, ...] | None = None

    async def execute(  # noqa: PLR0913
        self,
        *,
        command: str,
        args: tuple[str, ...] = (),
        cwd: Path | None = None,
        env_overrides: Mapping[str, str] | None = None,
        timeout: float | None = None,  # noqa: ASYNC109
        owner_id: str | None = None,
        project_id: str | None = None,
    ) -> SandboxResult:
        self.last_command = command
        self.last_args = args
        if self._error:
            raise self._error
        return SandboxResult(
            stdout=self._stdout,
            stderr=self._stderr,
            returncode=self._returncode,
            timed_out=self._timed_out,
        )

    async def release_owner(
        self,
        owner_id: str,
        *,
        project_id: str | None = None,
        image_override: str | None = None,
    ) -> None:
        pass

    async def cleanup(self) -> None:
        pass

    async def health_check(self) -> bool:
        return True

    def get_backend_type(self) -> str:
        return "mock"


@pytest.fixture
def mock_sandbox() -> MockSandbox:
    return MockSandbox(stdout="hello world", returncode=0)


@pytest.fixture
def shell_tool(mock_sandbox: MockSandbox) -> ShellCommandTool:
    return ShellCommandTool(sandbox=mock_sandbox)


@pytest.fixture
def restricted_tool(mock_sandbox: MockSandbox) -> ShellCommandTool:
    config = TerminalConfig(
        command_allowlist=("ls", "cat", "echo"),
    )
    return ShellCommandTool(sandbox=mock_sandbox, config=config)
