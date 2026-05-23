"""Unit tests for the Nix-flake environment strategy."""

from collections.abc import Mapping
from pathlib import Path

import pytest

from synthorg.core.enums import EnvironmentType
from synthorg.core.types import NotBlankStr
from synthorg.engine.errors import EnvironmentConfigError, EnvironmentProvisionError
from synthorg.engine.workspace.environment.nix import NixEnvironmentStrategy
from synthorg.engine.workspace.environment.protocol import (
    CommandOutcome,
    EnvironmentStrategy,
)
from tests._shared import FakeClock

pytestmark = pytest.mark.unit


class _Runner:
    def __init__(self, *, exit_code: int = 0) -> None:
        self.calls: list[tuple[str, tuple[str, ...]]] = []
        self._exit_code = exit_code

    async def run(
        self,
        *,
        command: str,
        args: tuple[str, ...],
        cwd: Path,
        env: Mapping[str, str] | None = None,
        timeout: float | None = None,  # noqa: ASYNC109 -- matches runner protocol
    ) -> CommandOutcome:
        del cwd, env, timeout
        self.calls.append((command, args))
        return CommandOutcome(command=command, exit_code=self._exit_code)


def _strategy() -> NixEnvironmentStrategy:
    return NixEnvironmentStrategy(provision_timeout_seconds=60.0, clock=FakeClock())


class TestNixStrategy:
    def test_satisfies_protocol_and_kind(self) -> None:
        assert isinstance(_strategy(), EnvironmentStrategy)
        assert _strategy().kind() is EnvironmentType.NIX

    async def test_scaffold_and_idempotent(self, tmp_path: Path) -> None:
        strategy = _strategy()
        first = await strategy.scaffold(tmp_path)
        assert first.seeded is True
        assert first.files_written == ("flake.nix",)
        assert (await strategy.scaffold(tmp_path)).seeded is False

    async def test_hash_changes_with_flake(self, tmp_path: Path) -> None:
        strategy = _strategy()
        await strategy.scaffold(tmp_path)
        before = strategy.declaration_hash(tmp_path)
        (tmp_path / "flake.nix").write_text("{ edited = true; }\n", encoding="utf-8")
        assert strategy.declaration_hash(tmp_path) != before

    def test_hash_missing_flake_raises(self, tmp_path: Path) -> None:
        with pytest.raises(EnvironmentConfigError):
            _strategy().declaration_hash(tmp_path)

    async def test_provision_builds_dev_shell(self, tmp_path: Path) -> None:
        strategy = _strategy()
        await strategy.scaffold(tmp_path)
        runner = _Runner(exit_code=0)

        result = await strategy.provision(
            project_id=NotBlankStr("proj-1"),
            workspace_path=tmp_path,
            runner=runner,
            sandbox_kind=NotBlankStr("subprocess"),
        )

        assert result.environment_type is EnvironmentType.NIX
        assert result.image_ref is None
        assert runner.calls == [("nix", ("develop", "--command", "true"))]

    async def test_provision_raises_on_build_failure(self, tmp_path: Path) -> None:
        strategy = _strategy()
        await strategy.scaffold(tmp_path)
        runner = _Runner(exit_code=1)

        with pytest.raises(EnvironmentProvisionError):
            await strategy.provision(
                project_id=NotBlankStr("proj-1"),
                workspace_path=tmp_path,
                runner=runner,
                sandbox_kind=NotBlankStr("subprocess"),
            )

    async def test_provision_missing_flake_raises(self, tmp_path: Path) -> None:
        with pytest.raises(EnvironmentConfigError):
            await _strategy().provision(
                project_id=NotBlankStr("proj-1"),
                workspace_path=tmp_path,
                runner=_Runner(),
                sandbox_kind=NotBlankStr("subprocess"),
            )
