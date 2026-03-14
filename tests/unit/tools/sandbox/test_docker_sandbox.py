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

pytestmark = [pytest.mark.unit, pytest.mark.timeout(30)]

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
        assert sandbox.config.image == "synthorg-sandbox:latest"
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
        sandbox._tracked_containers = ["container1", "container2"]

        await sandbox.cleanup()

        container_obj = mock_docker.containers.container.return_value
        assert container_obj.stop.await_count == 2
        assert container_obj.delete.await_count == 2
        assert sandbox._tracked_containers == []


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
        assert sandbox._tracked_containers == []

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
