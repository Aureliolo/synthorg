"""Tests for SandboxBackend protocol."""

from collections.abc import Mapping
from pathlib import Path

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.tools.sandbox.docker_sandbox import DockerSandbox
from synthorg.tools.sandbox.protocol import SandboxBackend
from synthorg.tools.sandbox.result import SandboxResult
from synthorg.tools.sandbox.subprocess_sandbox import SubprocessSandbox

pytestmark = [pytest.mark.unit, pytest.mark.timeout(30)]


class _FakeSandbox:
    """Minimal fake that satisfies the SandboxBackend protocol."""

    async def execute(
        self,
        *,
        command: str,
        args: tuple[str, ...],
        cwd: Path | None = None,
        env_overrides: Mapping[str, str] | None = None,
        timeout: float | None = None,  # noqa: ASYNC109
    ) -> SandboxResult:
        return SandboxResult(
            stdout="fake",
            stderr="",
            returncode=0,
        )

    async def cleanup(self) -> None:
        pass

    async def health_check(self) -> bool:
        return True

    def get_backend_type(self) -> NotBlankStr:
        return NotBlankStr("fake")


class TestSandboxBackendProtocol:
    """SandboxBackend is runtime_checkable and satisfied by impls."""

    def test_protocol_is_runtime_checkable(self) -> None:
        assert isinstance(_FakeSandbox(), SandboxBackend)

    def test_subprocess_sandbox_satisfies_protocol(
        self,
        subprocess_sandbox: SubprocessSandbox,
    ) -> None:
        assert isinstance(subprocess_sandbox, SandboxBackend)

    def test_docker_sandbox_satisfies_protocol(
        self,
        tmp_path: Path,
    ) -> None:
        sandbox = DockerSandbox(workspace=tmp_path)
        assert isinstance(sandbox, SandboxBackend)

    def test_arbitrary_object_does_not_satisfy(self) -> None:
        assert not isinstance(object(), SandboxBackend)
