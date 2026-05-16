"""Tests for DockerSandbox with mocked aiodocker."""

import asyncio
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

if TYPE_CHECKING:
    from collections.abc import Iterator

import pytest

from synthorg.tools.sandbox.docker_config import DockerSandboxConfig
from synthorg.tools.sandbox.docker_sandbox import (
    DockerSandbox,
    _to_posix_bind_path,
)
from synthorg.tools.sandbox.errors import SandboxError, SandboxStartError

pytestmark = pytest.mark.unit
_DOCKER_MODULE = "synthorg.tools.sandbox.docker_sandbox.aiodocker"

# ── Helpers ──────────────────────────────────────────────────────


def _make_mock_docker() -> MagicMock:
    """Create a mock aiodocker.Docker client."""
    mock_docker = MagicMock()
    mock_docker.version = AsyncMock(return_value={"ApiVersion": "1.43"})
    mock_docker.close = AsyncMock()

    # containers namespace
    mock_containers = MagicMock()
    mock_docker.containers = mock_containers

    # create() returns a container object with .id property
    mock_created_container = MagicMock()
    mock_created_container.id = "abc123def456"
    mock_containers.create = AsyncMock(
        return_value=mock_created_container,
    )

    # container object returned by .container(id)
    mock_container_obj = MagicMock()
    mock_container_obj.start = AsyncMock()
    mock_container_obj.wait = AsyncMock(
        return_value={"StatusCode": 0},
    )
    mock_container_obj.log = AsyncMock(return_value=["output line\n"])
    mock_container_obj.stop = AsyncMock()
    mock_container_obj.delete = AsyncMock()

    mock_containers.container = MagicMock(
        return_value=mock_container_obj,
    )

    return mock_docker


@contextmanager
def _patch_aiodocker(
    mock_docker: MagicMock,
) -> Iterator[Any]:
    """Create a patch for aiodocker.Docker that returns mock_docker."""
    mock_module = MagicMock()
    mock_module.Docker = MagicMock(return_value=mock_docker)
    with patch(_DOCKER_MODULE, mock_module) as p:
        yield p


# ── Constructor ──────────────────────────────────────────────────


class TestDockerSandboxInit:
    """Constructor validation."""

    def test_workspace_must_be_absolute(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="absolute path"):
            DockerSandbox(workspace=Path("relative"))

    def test_workspace_must_exist(self, tmp_path: Path) -> None:
        missing = tmp_path / "nonexistent"
        with pytest.raises(ValueError, match="does not exist"):
            DockerSandbox(workspace=missing)

    def test_valid_workspace(self, tmp_path: Path) -> None:
        sandbox = DockerSandbox(workspace=tmp_path)
        assert sandbox.workspace == tmp_path.resolve()

    def test_default_config(self, tmp_path: Path) -> None:
        sandbox = DockerSandbox(workspace=tmp_path)
        assert sandbox.config.image == "ghcr.io/aureliolo/synthorg-sandbox:latest"
        assert sandbox.config.timeout_seconds == 120.0

    def test_custom_config(self, tmp_path: Path) -> None:
        config = DockerSandboxConfig(image="custom:v1", cpu_limit=2.0)
        sandbox = DockerSandbox(config=config, workspace=tmp_path)
        assert sandbox.config.image == "custom:v1"
        assert sandbox.config.cpu_limit == 2.0


# ── CWD Validation ──────────────────────────────────────────────


class TestDockerSandboxCwdValidation:
    """Workspace boundary enforcement."""

    def test_cwd_within_workspace_accepted(
        self,
        tmp_path: Path,
    ) -> None:
        subdir = tmp_path / "sub"
        subdir.mkdir()
        sandbox = DockerSandbox(workspace=tmp_path)
        # Should not raise
        sandbox._validate_cwd(subdir)

    def test_workspace_root_accepted(self, tmp_path: Path) -> None:
        sandbox = DockerSandbox(workspace=tmp_path)
        sandbox._validate_cwd(tmp_path)

    def test_cwd_outside_workspace_rejected(
        self,
        tmp_path: Path,
    ) -> None:
        outside = tmp_path.parent / "outside"
        outside.mkdir(exist_ok=True)
        sandbox = DockerSandbox(workspace=tmp_path)
        with pytest.raises(SandboxError, match="outside workspace"):
            sandbox._validate_cwd(outside)


# ── Execute ─────────────────────────────────────────────────────


class TestDockerSandboxExecute:
    """Execute with mocked Docker daemon."""

    async def test_execute_success(self, tmp_path: Path) -> None:
        mock_docker = _make_mock_docker()
        sandbox = DockerSandbox(workspace=tmp_path)

        with _patch_aiodocker(mock_docker):
            result = await sandbox.execute(
                command="echo",
                args=("hello",),
            )

        assert result.success
        assert result.stdout == "output line\n"
        assert result.returncode == 0
        assert not result.timed_out

    async def test_execute_failure(self, tmp_path: Path) -> None:
        mock_docker = _make_mock_docker()
        container_obj = mock_docker.containers.container()
        container_obj.wait = AsyncMock(
            return_value={"StatusCode": 1},
        )
        container_obj.log = AsyncMock(
            return_value=["error occurred\n"],
        )

        sandbox = DockerSandbox(workspace=tmp_path)

        with _patch_aiodocker(mock_docker):
            result = await sandbox.execute(
                command="false",
                args=(),
            )

        assert not result.success
        assert result.returncode == 1

    async def test_execute_timeout(self, tmp_path: Path) -> None:
        mock_docker = _make_mock_docker()
        container_obj = mock_docker.containers.container()
        container_obj.wait = AsyncMock(
            side_effect=asyncio.TimeoutError,
        )
        container_obj.log = AsyncMock(
            return_value=["partial output\n"],
        )

        sandbox = DockerSandbox(workspace=tmp_path)

        with _patch_aiodocker(mock_docker):
            result = await sandbox.execute(
                command="sleep",
                args=("100",),
                timeout=1.0,
            )

        assert result.timed_out
        assert not result.success
        container_obj.stop.assert_awaited_once()

    async def test_execute_with_env_overrides(
        self,
        tmp_path: Path,
    ) -> None:
        mock_docker = _make_mock_docker()
        sandbox = DockerSandbox(workspace=tmp_path)

        with _patch_aiodocker(mock_docker):
            await sandbox.execute(
                command="env",
                args=(),
                env_overrides={"MY_VAR": "hello"},
            )

        create_call = mock_docker.containers.create.call_args
        config = create_call[0][0]
        assert "MY_VAR=hello" in config["Env"]

    async def test_execute_cwd_outside_workspace(
        self,
        tmp_path: Path,
    ) -> None:
        outside = tmp_path.parent / "escape"
        outside.mkdir(exist_ok=True)
        sandbox = DockerSandbox(workspace=tmp_path)

        with pytest.raises(SandboxError, match="outside workspace"):
            await sandbox.execute(
                command="echo",
                args=("test",),
                cwd=outside,
            )

    async def test_execute_custom_cwd_within_workspace(
        self,
        tmp_path: Path,
    ) -> None:
        subdir = tmp_path / "project"
        subdir.mkdir()
        mock_docker = _make_mock_docker()
        sandbox = DockerSandbox(workspace=tmp_path)

        with _patch_aiodocker(mock_docker):
            await sandbox.execute(
                command="ls",
                args=(),
                cwd=subdir,
            )

        create_call = mock_docker.containers.create.call_args
        config = create_call[0][0]
        assert config["WorkingDir"] == "/workspace/project"

    async def test_docker_unavailable_raises_start_error(
        self,
        tmp_path: Path,
    ) -> None:
        sandbox = DockerSandbox(workspace=tmp_path)

        mock_client = MagicMock()
        mock_client.version = AsyncMock(
            side_effect=ConnectionError("refused"),
        )
        mock_client.close = AsyncMock()
        mock_module = MagicMock()
        mock_module.Docker = MagicMock(return_value=mock_client)

        with (
            patch(_DOCKER_MODULE, mock_module),
            pytest.raises(
                SandboxStartError,
                match="Docker daemon unavailable",
            ),
        ):
            await sandbox.execute(
                command="echo",
                args=("test",),
            )

    async def test_image_not_found_raises_start_error(
        self,
        tmp_path: Path,
    ) -> None:
        mock_docker = _make_mock_docker()
        mock_docker.containers.create = AsyncMock(
            side_effect=Exception("image not found"),
        )
        sandbox = DockerSandbox(workspace=tmp_path)

        with (
            _patch_aiodocker(mock_docker),
            pytest.raises(
                SandboxStartError,
                match="Failed to create container",
            ),
        ):
            await sandbox.execute(
                command="echo",
                args=("test",),
            )

    async def test_oom_kill_returncode_137(
        self,
        tmp_path: Path,
    ) -> None:
        mock_docker = _make_mock_docker()
        container_obj = mock_docker.containers.container()
        container_obj.wait = AsyncMock(
            return_value={"StatusCode": 137},
        )
        container_obj.log = AsyncMock(return_value=[""])

        sandbox = DockerSandbox(workspace=tmp_path)

        with _patch_aiodocker(mock_docker):
            result = await sandbox.execute(
                command="stress",
                args=("--vm", "1"),
            )

        assert result.returncode == 137
        assert not result.success


# ── Container Config ────────────────────────────────────────────


class TestDockerSandboxContainerConfig:
    """Container configuration building."""

    def test_mount_mode_rw(self, tmp_path: Path) -> None:
        config = DockerSandboxConfig(mount_mode="rw")
        sandbox = DockerSandbox(config=config, workspace=tmp_path)
        result = sandbox._build_container_config(
            command="echo",
            args=("hi",),
            container_cwd="/workspace",
            env_overrides=None,
        )
        bind = result["HostConfig"]["Binds"][0]
        assert bind.endswith(":rw")

    def test_mount_mode_ro(self, tmp_path: Path) -> None:
        config = DockerSandboxConfig(mount_mode="ro")
        sandbox = DockerSandbox(config=config, workspace=tmp_path)
        result = sandbox._build_container_config(
            command="echo",
            args=("hi",),
            container_cwd="/workspace",
            env_overrides=None,
        )
        bind = result["HostConfig"]["Binds"][0]
        assert bind.endswith(":ro")

    def test_runtime_included_when_set(self, tmp_path: Path) -> None:
        config = DockerSandboxConfig(runtime="runsc")
        sandbox = DockerSandbox(config=config, workspace=tmp_path)
        result = sandbox._build_container_config(
            command="echo",
            args=(),
            container_cwd="/workspace",
            env_overrides=None,
        )
        assert result["HostConfig"]["Runtime"] == "runsc"

    def test_runtime_excluded_when_none(
        self,
        tmp_path: Path,
    ) -> None:
        config = DockerSandboxConfig(runtime=None)
        sandbox = DockerSandbox(config=config, workspace=tmp_path)
        result = sandbox._build_container_config(
            command="echo",
            args=(),
            container_cwd="/workspace",
            env_overrides=None,
        )
        assert "Runtime" not in result["HostConfig"]

    def test_network_mode_set(self, tmp_path: Path) -> None:
        config = DockerSandboxConfig(network="bridge")
        sandbox = DockerSandbox(config=config, workspace=tmp_path)
        result = sandbox._build_container_config(
            command="echo",
            args=(),
            container_cwd="/workspace",
            env_overrides=None,
        )
        assert result["HostConfig"]["NetworkMode"] == "bridge"


# ── Cleanup ─────────────────────────────────────────────────────


class TestDockerSandboxCleanup:
    """Cleanup and resource release."""

    async def test_cleanup_closes_docker_session(
        self,
        tmp_path: Path,
    ) -> None:
        mock_docker = _make_mock_docker()
        sandbox = DockerSandbox(workspace=tmp_path)
        sandbox._docker = mock_docker

        await sandbox.cleanup()

        mock_docker.close.assert_awaited_once()
        assert sandbox._docker is None

    async def test_cleanup_without_connection(
        self,
        tmp_path: Path,
    ) -> None:
        sandbox = DockerSandbox(workspace=tmp_path)
        # Should not raise
        await sandbox.cleanup()

    async def test_cleanup_stops_tracked_containers(
        self,
        tmp_path: Path,
    ) -> None:
        mock_docker = _make_mock_docker()
        sandbox = DockerSandbox(workspace=tmp_path)
        sandbox._docker = mock_docker
        sandbox._tracked_containers = {"container1": None, "container2": None}

        await sandbox.cleanup()

        container_obj = mock_docker.containers.container.return_value
        assert container_obj.stop.await_count == 2
        assert container_obj.delete.await_count == 2
        assert sandbox._tracked_containers == {}


# ── Health check ────────────────────────────────────────────────


class TestDockerSandboxHealthCheck:
    """Health check behavior."""

    async def test_health_check_success(self, tmp_path: Path) -> None:
        mock_docker = _make_mock_docker()
        sandbox = DockerSandbox(workspace=tmp_path)
        sandbox._docker = mock_docker

        assert await sandbox.health_check() is True

    async def test_health_check_failure(
        self,
        tmp_path: Path,
    ) -> None:
        sandbox = DockerSandbox(workspace=tmp_path)

        mock_client = MagicMock()
        mock_client.version = AsyncMock(
            side_effect=ConnectionError("refused"),
        )
        mock_client.close = AsyncMock()
        mock_module = MagicMock()
        mock_module.Docker = MagicMock(return_value=mock_client)

        with patch(_DOCKER_MODULE, mock_module):
            assert await sandbox.health_check() is False


# ── Backend type ────────────────────────────────────────────────


class TestDockerSandboxBackendType:
    """Backend type identifier."""

    def test_returns_docker(self, tmp_path: Path) -> None:
        sandbox = DockerSandbox(workspace=tmp_path)
        assert sandbox.get_backend_type() == "docker"


# ── Windows path conversion ─────────────────────────────────────


class TestWindowsPathConversion:
    """Path conversion for Docker bind mounts."""

    def test_unix_path_unchanged(self) -> None:
        with patch(
            "synthorg.tools.sandbox.docker_sandbox.platform.system",
            return_value="Linux",
        ):
            # Use PurePosixPath to avoid Windows path normalisation
            posix_path = PurePosixPath("/home/user/workspace")
            result = _to_posix_bind_path(posix_path)  # type: ignore[arg-type]
            assert result == "/home/user/workspace"

    def test_windows_path_converted(self) -> None:
        with patch(
            "synthorg.tools.sandbox.docker_sandbox.platform.system",
            return_value="Windows",
        ):
            win_path = Path("C:/Users/test/workspace")
            result = _to_posix_bind_path(win_path)
            assert result.startswith("/c/")
            assert "Users" in result
            assert "test" in result

    def test_windows_path_lowercase_drive(self) -> None:
        with patch(
            "synthorg.tools.sandbox.docker_sandbox.platform.system",
            return_value="Windows",
        ):
            win_path = Path("D:/Projects/app")
            result = _to_posix_bind_path(win_path)
            assert result.startswith("/d/")


# ── Memory limit parsing ────────────────────────────────────────


class TestMemoryLimitParsing:
    """DockerSandbox._parse_memory_limit."""

    @pytest.mark.parametrize(
        ("limit", "expected"),
        [
            ("512m", 512 * 1024**2),
            ("1g", 1024**3),
            ("256k", 256 * 1024),
            ("1024", 1024),
            ("2G", 2 * 1024**3),
        ],
    )
    def test_parse_memory_limit(
        self,
        limit: str,
        expected: int,
    ) -> None:
        assert DockerSandbox._parse_memory_limit(limit) == expected

    @pytest.mark.parametrize(
        "invalid_limit",
        ["", "   ", "abc", "512x", "0m", "-1g"],
    )
    def test_parse_memory_limit_invalid(
        self,
        invalid_limit: str,
    ) -> None:
        with pytest.raises(ValueError, match=r"[Mm]emory|invalid literal"):
            DockerSandbox._parse_memory_limit(invalid_limit)


# ── Container hardening ────────────────────────────────────────


class TestDockerSandboxHardening:
    """Security hardening in container config."""

    def test_tmpfs_mount_for_tmp(self, tmp_path: Path) -> None:
        sandbox = DockerSandbox(workspace=tmp_path)
        config = sandbox._build_container_config(
            command="echo",
            args=(),
            container_cwd="/workspace",
            env_overrides=None,
        )
        assert "/tmp" in config["HostConfig"]["Tmpfs"]  # noqa: S108

    def test_pids_limit_set(self, tmp_path: Path) -> None:
        sandbox = DockerSandbox(workspace=tmp_path)
        config = sandbox._build_container_config(
            command="echo",
            args=(),
            container_cwd="/workspace",
            env_overrides=None,
        )
        assert config["HostConfig"]["PidsLimit"] == 64

    def test_readonly_rootfs_set(self, tmp_path: Path) -> None:
        sandbox = DockerSandbox(workspace=tmp_path)
        config = sandbox._build_container_config(
            command="echo",
            args=(),
            container_cwd="/workspace",
            env_overrides=None,
        )
        assert config["HostConfig"]["ReadonlyRootfs"] is True

    def test_cap_drop_all(self, tmp_path: Path) -> None:
        sandbox = DockerSandbox(workspace=tmp_path)
        config = sandbox._build_container_config(
            command="echo",
            args=(),
            container_cwd="/workspace",
            env_overrides=None,
        )
        assert config["HostConfig"]["CapDrop"] == ["ALL"]


# ── Stop/remove exception handling ─────────────────────────────


class TestDockerSandboxContainerErrorHandling:
    """Container stop/remove error paths."""

    async def test_stop_container_swallows_exception(
        self,
        tmp_path: Path,
    ) -> None:
        mock_docker = _make_mock_docker()
        container_obj = mock_docker.containers.container()
        container_obj.stop = AsyncMock(
            side_effect=RuntimeError("already stopped"),
        )
        sandbox = DockerSandbox(workspace=tmp_path)
        # Should not raise
        await sandbox._stop_container(mock_docker, "abc123def456")

    async def test_remove_container_swallows_exception(
        self,
        tmp_path: Path,
    ) -> None:
        mock_docker = _make_mock_docker()
        container_obj = mock_docker.containers.container()
        container_obj.delete = AsyncMock(
            side_effect=RuntimeError("already removed"),
        )
        sandbox = DockerSandbox(workspace=tmp_path)
        # Should not raise
        await sandbox._remove_container(mock_docker, "abc123def456")

    async def test_tracked_containers_pruned_after_execute(
        self,
        tmp_path: Path,
    ) -> None:
        mock_docker = _make_mock_docker()
        sandbox = DockerSandbox(workspace=tmp_path)

        with _patch_aiodocker(mock_docker):
            await sandbox.execute(command="echo", args=("hi",))

        # Container should be removed from tracking after execute
        assert sandbox._tracked_containers == {}

    async def test_start_failure_raises_sandbox_start_error(
        self,
        tmp_path: Path,
    ) -> None:
        mock_docker = _make_mock_docker()
        container_obj = mock_docker.containers.container()
        container_obj.start = AsyncMock(
            side_effect=RuntimeError("OOM at start"),
        )
        sandbox = DockerSandbox(workspace=tmp_path)

        with (
            _patch_aiodocker(mock_docker),
            pytest.raises(
                SandboxStartError,
                match="Failed to start container",
            ),
        ):
            await sandbox.execute(command="echo", args=("test",))


# ── Sidecar lifecycle ──────────────────────────────────────────


def _make_sidecar_config(
    allowed_hosts: tuple[str, ...] = ("example.com:443",),
    network_allow_all: bool = False,
) -> DockerSandboxConfig:
    """Create a config that triggers sidecar creation.

    Sets ``network="bridge"`` so ``_needs_sidecar()`` returns True
    (the default ``"none"`` disables sidecar enforcement).
    """
    return DockerSandboxConfig(
        allowed_hosts=allowed_hosts,
        network_allow_all=network_allow_all,
        network="bridge",
    )


def _make_mock_docker_with_sidecar() -> MagicMock:
    """Create a mock Docker client that supports sidecar containers."""
    mock_docker = _make_mock_docker()

    # Sidecar container (second call to create).
    sidecar_container = MagicMock()
    sidecar_container.id = "sidecar_999aaa"
    sidecar_container.start = AsyncMock()
    sidecar_container.show = AsyncMock(
        return_value={
            "State": {"Health": {"Status": "healthy"}},
        },
    )
    sidecar_container.stop = AsyncMock()
    sidecar_container.delete = AsyncMock()

    # Sandbox container (first return from create in normal flow).
    sandbox_container = MagicMock()
    sandbox_container.id = "sandbox_abc123"
    sandbox_container.start = AsyncMock()
    sandbox_container.wait = AsyncMock(
        return_value={"StatusCode": 0},
    )
    sandbox_container.log = AsyncMock(return_value=["sidecar ok\n"])
    sandbox_container.stop = AsyncMock()
    sandbox_container.delete = AsyncMock()

    # create() returns sidecar first, then sandbox.
    mock_docker.containers.create = AsyncMock(
        side_effect=[sidecar_container, sandbox_container],
    )

    # container(id) returns the right object based on ID.
    def _container_by_id(cid: str) -> MagicMock:
        if cid == sidecar_container.id:
            return sidecar_container
        return sandbox_container

    mock_docker.containers.container = MagicMock(
        side_effect=_container_by_id,
    )

    return mock_docker


class TestSidecarLifecycle:
    """Sidecar creation, health check, and cleanup."""

    async def test_sidecar_created_when_allowed_hosts(
        self,
        tmp_path: Path,
    ) -> None:
        mock_docker = _make_mock_docker_with_sidecar()
        config = _make_sidecar_config()
        sandbox = DockerSandbox(config=config, workspace=tmp_path)

        with _patch_aiodocker(mock_docker):
            result = await sandbox.execute(
                command="echo",
                args=("hello",),
            )

        assert result.success
        # Two create calls: sidecar + sandbox.
        assert mock_docker.containers.create.await_count == 2

    async def test_sidecar_health_check_unhealthy(
        self,
        tmp_path: Path,
    ) -> None:
        # Set up a mock that reports unhealthy status.
        sidecar_container = MagicMock()
        sidecar_container.id = "sidecar_unhealthy"
        sidecar_container.start = AsyncMock()
        sidecar_container.show = AsyncMock(
            return_value={
                "State": {"Health": {"Status": "unhealthy"}},
            },
        )
        sidecar_container.stop = AsyncMock()
        sidecar_container.delete = AsyncMock()

        mock_docker2 = _make_mock_docker()
        mock_docker2.containers.create = AsyncMock(
            return_value=sidecar_container,
        )
        mock_docker2.containers.container = MagicMock(
            return_value=sidecar_container,
        )

        config = _make_sidecar_config()
        sandbox = DockerSandbox(config=config, workspace=tmp_path)

        with (
            _patch_aiodocker(mock_docker2),
            pytest.raises(
                SandboxStartError,
                match="unhealthy",
            ),
        ):
            await sandbox.execute(command="echo", args=("test",))

    async def test_sidecar_health_check_timeout(
        self,
        tmp_path: Path,
    ) -> None:
        sidecar_container = MagicMock()
        sidecar_container.id = "sidecar_timeout"
        sidecar_container.start = AsyncMock()
        # Health status never transitions to healthy.
        sidecar_container.show = AsyncMock(
            return_value={
                "State": {"Health": {"Status": "starting"}},
            },
        )
        sidecar_container.stop = AsyncMock()
        sidecar_container.delete = AsyncMock()

        mock_docker = _make_mock_docker()
        mock_docker.containers.create = AsyncMock(
            return_value=sidecar_container,
        )
        mock_docker.containers.container = MagicMock(
            return_value=sidecar_container,
        )

        config = _make_sidecar_config()
        sandbox = DockerSandbox(config=config, workspace=tmp_path)

        # Mock the event loop time so the test is deterministic
        # instead of relying on real wall-clock sleep.
        fake_time = 0.0

        def _fake_loop_time() -> float:
            return fake_time

        async def _fake_sleep(_: float) -> None:
            nonlocal fake_time
            # Each sleep call advances past the timeout.
            fake_time += 20.0

        loop = asyncio.get_running_loop()
        original_time = loop.time

        loop.time = _fake_loop_time  # type: ignore[method-assign]
        try:
            with (
                _patch_aiodocker(mock_docker),
                patch(
                    "synthorg.tools.sandbox.docker_sandbox._SIDECAR_HEALTH_TIMEOUT",
                    15.0,
                ),
                patch(
                    "synthorg.tools.sandbox.docker_sandbox.asyncio.sleep",
                    _fake_sleep,
                ),
                pytest.raises(SandboxStartError, match="timed out"),
            ):
                await sandbox.execute(
                    command="echo",
                    args=("test",),
                )
        finally:
            loop.time = original_time  # type: ignore[method-assign]

    async def test_sidecar_create_failure(
        self,
        tmp_path: Path,
    ) -> None:
        mock_docker = _make_mock_docker()
        mock_docker.containers.create = AsyncMock(
            side_effect=RuntimeError("image not found"),
        )

        config = _make_sidecar_config()
        sandbox = DockerSandbox(config=config, workspace=tmp_path)

        with (
            _patch_aiodocker(mock_docker),
            pytest.raises(
                SandboxStartError,
                match="Failed to create sidecar container",
            ),
        ):
            await sandbox.execute(command="echo", args=("test",))

    async def test_sidecar_cleanup_on_start_failure(
        self,
        tmp_path: Path,
    ) -> None:
        sidecar_container = MagicMock()
        sidecar_container.id = "sidecar_startfail"
        sidecar_container.start = AsyncMock(
            side_effect=RuntimeError("port conflict"),
        )
        sidecar_container.stop = AsyncMock()
        sidecar_container.delete = AsyncMock()

        mock_docker = _make_mock_docker()
        mock_docker.containers.create = AsyncMock(
            return_value=sidecar_container,
        )
        mock_docker.containers.container = MagicMock(
            return_value=sidecar_container,
        )

        config = _make_sidecar_config()
        sandbox = DockerSandbox(config=config, workspace=tmp_path)

        with (
            _patch_aiodocker(mock_docker),
            pytest.raises(SandboxStartError, match="port conflict"),
        ):
            await sandbox.execute(command="echo", args=("test",))

        # Sidecar should be cleaned up despite failure.
        sidecar_container.delete.assert_awaited()

    async def test_sidecar_env_allow_all(
        self,
        tmp_path: Path,
    ) -> None:
        sidecar_container = MagicMock()
        sidecar_container.id = "sidecar_allowall"
        sidecar_container.start = AsyncMock()
        sidecar_container.show = AsyncMock(
            return_value={
                "State": {"Health": {"Status": "healthy"}},
            },
        )
        sidecar_container.stop = AsyncMock()
        sidecar_container.delete = AsyncMock()

        sandbox_container = MagicMock()
        sandbox_container.id = "sandbox_allowall"
        sandbox_container.start = AsyncMock()
        sandbox_container.wait = AsyncMock(
            return_value={"StatusCode": 0},
        )
        sandbox_container.log = AsyncMock(return_value=["ok\n"])
        sandbox_container.stop = AsyncMock()
        sandbox_container.delete = AsyncMock()

        mock_docker = _make_mock_docker()
        mock_docker.containers.create = AsyncMock(
            side_effect=[sidecar_container, sandbox_container],
        )

        def _container_by_id(cid: str) -> MagicMock:
            if cid == sidecar_container.id:
                return sidecar_container
            return sandbox_container

        mock_docker.containers.container = MagicMock(
            side_effect=_container_by_id,
        )

        config = _make_sidecar_config(
            allowed_hosts=(),
            network_allow_all=True,
        )
        sandbox = DockerSandbox(config=config, workspace=tmp_path)

        with _patch_aiodocker(mock_docker):
            result = await sandbox.execute(
                command="echo",
                args=("hello",),
            )

        assert result.success
        # Check sidecar was created with SIDECAR_ALLOW_ALL=1.
        sidecar_create_call = mock_docker.containers.create.call_args_list[0]
        env_list = sidecar_create_call[0][0]["Env"]
        assert any("SIDECAR_ALLOW_ALL=1" in e for e in env_list)

    async def test_no_sidecar_when_no_rules(
        self,
        tmp_path: Path,
    ) -> None:
        mock_docker = _make_mock_docker()
        config = DockerSandboxConfig()  # no allowed_hosts
        sandbox = DockerSandbox(config=config, workspace=tmp_path)

        with _patch_aiodocker(mock_docker):
            result = await sandbox.execute(
                command="echo",
                args=("hello",),
            )

        assert result.success
        # Only one create call (sandbox, no sidecar).
        assert mock_docker.containers.create.await_count == 1
