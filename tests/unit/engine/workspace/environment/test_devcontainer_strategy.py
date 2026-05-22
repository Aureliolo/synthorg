"""Unit tests for the devcontainer environment strategy."""

from collections.abc import Mapping
from pathlib import Path

import pytest
from tests._shared import FakeClock

from synthorg.core.enums import EnvironmentType
from synthorg.core.types import NotBlankStr
from synthorg.engine.errors import (
    EnvironmentBackendUnavailableError,
    EnvironmentConfigError,
    EnvironmentDockerBuildError,
    EnvironmentProvisionError,
)
from synthorg.engine.workspace.environment.devcontainer import (
    DevcontainerEnvironmentStrategy,
)
from synthorg.engine.workspace.environment.image_builder import BuildOutcome
from synthorg.engine.workspace.environment.protocol import (
    CommandOutcome,
    EnvironmentStrategy,
)

pytestmark = pytest.mark.unit

_DOCKER = NotBlankStr("docker")
_SUBPROCESS = NotBlankStr("subprocess")


class _FakeBuilder:
    def __init__(self, *, exit_code: int = 0) -> None:
        self.builds: list[NotBlankStr] = []
        self._exit_code = exit_code

    async def build(
        self,
        *,
        tag: NotBlankStr,
        dockerfile: Path,
        context_dir: Path,
        timeout: float,  # noqa: ASYNC109 -- matches ImageBuilder protocol
    ) -> BuildOutcome:
        del dockerfile, context_dir, timeout
        self.builds.append(tag)
        return BuildOutcome(tag=tag, exit_code=self._exit_code)


class _SequenceBuilder:
    """Builder returning a preset sequence of outcomes (last repeats)."""

    def __init__(self, outcomes: list[BuildOutcome]) -> None:
        self.builds: list[NotBlankStr] = []
        self._outcomes = outcomes

    async def build(
        self,
        *,
        tag: NotBlankStr,
        dockerfile: Path,
        context_dir: Path,
        timeout: float,  # noqa: ASYNC109 -- matches ImageBuilder protocol
    ) -> BuildOutcome:
        del dockerfile, context_dir, timeout
        self.builds.append(tag)
        idx = min(len(self.builds) - 1, len(self._outcomes) - 1)
        outcome = self._outcomes[idx]
        return outcome.model_copy(update={"tag": tag})


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


def _strategy(builder: _FakeBuilder | None = None) -> DevcontainerEnvironmentStrategy:
    return DevcontainerEnvironmentStrategy(
        image_builder=builder if builder is not None else _FakeBuilder(),
        docker_build_timeout_seconds=120.0,
        build_max_attempts=2,
        build_retry_base_seconds=0.0,
        build_retry_cap_seconds=0.0,
        clock=FakeClock(),
    )


def _write_devcontainer(workspace: Path, body: str) -> Path:
    target = workspace / ".devcontainer" / "devcontainer.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")
    return target


class TestDevcontainerStrategy:
    def test_protocol_and_kind(self) -> None:
        assert isinstance(_strategy(), EnvironmentStrategy)
        assert _strategy().kind() is EnvironmentType.DEVCONTAINER

    async def test_scaffold_and_idempotent(self, tmp_path: Path) -> None:
        strategy = _strategy()
        result = await strategy.scaffold(tmp_path)
        assert result.seeded is True
        assert result.files_written == (".devcontainer/devcontainer.json",)
        assert (tmp_path / ".devcontainer" / "devcontainer.json").is_file()
        assert (await strategy.scaffold(tmp_path)).seeded is False

    async def test_requires_docker_backend(self, tmp_path: Path) -> None:
        _write_devcontainer(tmp_path, '{"image": "debian:bookworm-slim"}')
        with pytest.raises(EnvironmentBackendUnavailableError):
            await _strategy().provision(
                project_id=NotBlankStr("proj-1"),
                workspace_path=tmp_path,
                runner=_Runner(),
                sandbox_kind=_SUBPROCESS,
            )

    async def test_image_used_as_is(self, tmp_path: Path) -> None:
        _write_devcontainer(tmp_path, '{"image": "debian:bookworm-slim"}')
        builder = _FakeBuilder()

        result = await _strategy(builder).provision(
            project_id=NotBlankStr("proj-1"),
            workspace_path=tmp_path,
            runner=_Runner(),
            sandbox_kind=_DOCKER,
        )

        assert result.image_ref == "debian:bookworm-slim"
        assert builder.builds == []  # no build for a pre-built image

    async def test_build_path_builds_and_tags(self, tmp_path: Path) -> None:
        _write_devcontainer(tmp_path, '{"build": {"dockerfile": "Dockerfile"}}')
        (tmp_path / ".devcontainer" / "Dockerfile").write_text(
            "FROM debian:bookworm-slim\n", encoding="utf-8"
        )
        builder = _FakeBuilder()

        result = await _strategy(builder).provision(
            project_id=NotBlankStr("Proj-1"),
            workspace_path=tmp_path,
            runner=_Runner(),
            sandbox_kind=_DOCKER,
        )

        assert len(builder.builds) == 1
        # Tag is lowercased + deterministic by declaration hash prefix.
        assert result.image_ref is not None
        assert str(result.image_ref).startswith("synthorg-project-proj-1:")
        assert result.image_ref == builder.builds[0]

    async def test_build_failure_raises(self, tmp_path: Path) -> None:
        _write_devcontainer(tmp_path, '{"build": {"dockerfile": "Dockerfile"}}')
        (tmp_path / ".devcontainer" / "Dockerfile").write_text(
            "FROM x\n", encoding="utf-8"
        )
        builder = _FakeBuilder(exit_code=1)

        with pytest.raises(EnvironmentDockerBuildError):
            await _strategy(builder).provision(
                project_id=NotBlankStr("proj-1"),
                workspace_path=tmp_path,
                runner=_Runner(),
                sandbox_kind=_DOCKER,
            )

    async def test_post_create_runs_and_env_forwarded(self, tmp_path: Path) -> None:
        _write_devcontainer(
            tmp_path,
            '{"image": "debian:bookworm-slim", '
            '"postCreateCommand": "echo hi", '
            '"containerEnv": {"FOO": "bar"}}',
        )
        runner = _Runner()

        result = await _strategy().provision(
            project_id=NotBlankStr("proj-1"),
            workspace_path=tmp_path,
            runner=runner,
            sandbox_kind=_DOCKER,
        )

        assert runner.calls == [("sh", ("-c", "echo hi"))]
        assert result.env_vars == {"FOO": "bar"}

    async def test_post_create_failure_raises(self, tmp_path: Path) -> None:
        _write_devcontainer(
            tmp_path,
            '{"image": "debian:bookworm-slim", "postCreateCommand": "boom"}',
        )
        with pytest.raises(EnvironmentProvisionError):
            await _strategy().provision(
                project_id=NotBlankStr("proj-1"),
                workspace_path=tmp_path,
                runner=_Runner(exit_code=1),
                sandbox_kind=_DOCKER,
            )

    async def test_missing_image_and_build_raises(self, tmp_path: Path) -> None:
        _write_devcontainer(tmp_path, '{"name": "x"}')
        with pytest.raises(EnvironmentConfigError):
            await _strategy().provision(
                project_id=NotBlankStr("proj-1"),
                workspace_path=tmp_path,
                runner=_Runner(),
                sandbox_kind=_DOCKER,
            )

    def test_hash_changes_with_dockerfile(self, tmp_path: Path) -> None:
        _write_devcontainer(tmp_path, '{"build": {"dockerfile": "Dockerfile"}}')
        dockerfile = tmp_path / ".devcontainer" / "Dockerfile"
        dockerfile.write_text("FROM a\n", encoding="utf-8")
        strategy = _strategy()
        before = strategy.declaration_hash(tmp_path)

        dockerfile.write_text("FROM b\n", encoding="utf-8")
        assert strategy.declaration_hash(tmp_path) != before

    def test_dockerfile_escape_rejected(self, tmp_path: Path) -> None:
        _write_devcontainer(
            tmp_path, '{"build": {"dockerfile": "../../etc/Dockerfile"}}'
        )
        with pytest.raises(EnvironmentConfigError):
            _strategy().declaration_hash(tmp_path)

    async def _provision_build(self, tmp_path: Path, builder: _SequenceBuilder) -> None:
        _write_devcontainer(tmp_path, '{"build": {"dockerfile": "Dockerfile"}}')
        (tmp_path / ".devcontainer" / "Dockerfile").write_text(
            "FROM debian:bookworm-slim\n", encoding="utf-8"
        )
        await _strategy(builder).provision(  # type: ignore[arg-type]
            project_id=NotBlankStr("proj-1"),
            workspace_path=tmp_path,
            runner=_Runner(),
            sandbox_kind=_DOCKER,
        )

    async def test_transient_build_retried_then_succeeds(self, tmp_path: Path) -> None:
        # A timed-out build is transient: retry, then succeed.
        builder = _SequenceBuilder(
            [
                BuildOutcome(tag=NotBlankStr("t"), exit_code=-1, timed_out=True),
                BuildOutcome(tag=NotBlankStr("t"), exit_code=0),
            ]
        )
        await self._provision_build(tmp_path, builder)
        assert len(builder.builds) == 2

    async def test_transient_marker_in_log_retried(self, tmp_path: Path) -> None:
        builder = _SequenceBuilder(
            [
                BuildOutcome(
                    tag=NotBlankStr("t"),
                    exit_code=1,
                    log="failed to pull: connection refused",
                ),
                BuildOutcome(tag=NotBlankStr("t"), exit_code=0),
            ]
        )
        await self._provision_build(tmp_path, builder)
        assert len(builder.builds) == 2

    async def test_deterministic_failure_not_retried(self, tmp_path: Path) -> None:
        # A plain non-zero exit with no transient marker is deterministic.
        builder = _SequenceBuilder(
            [BuildOutcome(tag=NotBlankStr("t"), exit_code=1, log="invalid Dockerfile")]
        )
        with pytest.raises(EnvironmentDockerBuildError):
            await self._provision_build(tmp_path, builder)
        assert len(builder.builds) == 1  # not retried

    async def test_transient_failure_exhausts_retries(self, tmp_path: Path) -> None:
        builder = _SequenceBuilder(
            [BuildOutcome(tag=NotBlankStr("t"), exit_code=-1, timed_out=True)]
        )
        with pytest.raises(EnvironmentDockerBuildError):
            await self._provision_build(tmp_path, builder)
        assert len(builder.builds) == 2  # build_max_attempts in _strategy


class TestBuildOutcomeValidator:
    def test_timed_out_requires_minus_one_exit(self) -> None:
        with pytest.raises(ValueError, match="exit_code -1"):
            BuildOutcome(tag=NotBlankStr("t"), exit_code=0, timed_out=True)

    def test_timed_out_with_minus_one_ok(self) -> None:
        outcome = BuildOutcome(tag=NotBlankStr("t"), exit_code=-1, timed_out=True)
        assert outcome.success is False
