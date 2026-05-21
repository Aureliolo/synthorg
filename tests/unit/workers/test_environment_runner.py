"""Unit tests for the SandboxEnvironmentRunner adapter."""

from pathlib import Path

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.engine.workspace.environment.protocol import EnvironmentCommandRunner
from synthorg.tools.sandbox.protocol import SandboxBackend
from synthorg.tools.sandbox.result import SandboxResult
from synthorg.workers.environment_runner import SandboxEnvironmentRunner
from tests._shared import mock_of

pytestmark = pytest.mark.unit


class TestSandboxEnvironmentRunner:
    def test_satisfies_runner_protocol(self) -> None:
        runner = SandboxEnvironmentRunner(
            backend=mock_of[SandboxBackend](),
            project_id=NotBlankStr("proj-1"),
        )
        assert isinstance(runner, EnvironmentCommandRunner)

    async def test_run_maps_result_and_passes_project(self) -> None:
        backend = mock_of[SandboxBackend]()
        backend.execute.return_value = SandboxResult(
            stdout="out", stderr="err", returncode=0
        )
        runner = SandboxEnvironmentRunner(
            backend=backend, project_id=NotBlankStr("proj-1")
        )

        outcome = await runner.run(
            command="sh",
            args=("-c", "echo hi"),
            cwd=Path("/w"),
            env={"FOO": "bar"},
            timeout=30.0,
        )

        assert outcome.success
        assert outcome.exit_code == 0
        assert outcome.stdout == "out"
        backend.execute.assert_awaited_once()
        kwargs = backend.execute.await_args.kwargs
        assert kwargs["project_id"] == "proj-1"
        assert kwargs["env_overrides"] == {"FOO": "bar"}

    async def test_run_propagates_failure_exit_code(self) -> None:
        backend = mock_of[SandboxBackend]()
        backend.execute.return_value = SandboxResult(
            stdout="", stderr="boom", returncode=2
        )
        runner = SandboxEnvironmentRunner(
            backend=backend, project_id=NotBlankStr("proj-1")
        )

        outcome = await runner.run(command="sh", args=("-c", "x"), cwd=Path("/w"))
        assert outcome.success is False
        assert outcome.exit_code == 2
