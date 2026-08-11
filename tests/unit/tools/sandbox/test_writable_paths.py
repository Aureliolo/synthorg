"""A read-only root filesystem still has to leave a runtime somewhere to write.

The sandbox mounts one writable tmpfs, at ``/tmp``. That is enough for an
image whose runtime confines itself there, and not enough for the OpenHands
SDK: it keeps its jinja cache, skills, plugins and profiles under ``$HOME``
whatever ``persistence_dir`` the entrypoint passes, so a read-only home ends
the run at conversation construction with ``Errno 30`` before a single turn.
Declaring the extra mounts on the config keeps that an image-specific fact the
wiring states, rather than a hole punched in the hardened defaults for
everyone.
"""

from pathlib import Path
from typing import cast

import pytest

from synthorg.tools.sandbox.docker_config import (
    CONTAINER_TMP,
    CONTAINER_WORKSPACE,
    DockerSandboxConfig,
)
from synthorg.tools.sandbox.docker_sandbox import DockerSandbox
from synthorg.workers._openhands_wiring import _SDK_STATE_HOME
from tests._shared import JsonDict

pytestmark = pytest.mark.unit

_HOME = "/home/agent"


def _host_config(workspace: Path, **overrides: object) -> JsonDict:
    """Build a sandbox host config with the given config overrides.

    Args:
        workspace: Directory the sandbox binds as its workspace.
        **overrides: Fields forwarded to ``DockerSandboxConfig``.

    Returns:
        The Docker ``HostConfig`` mapping.
    """
    config = DockerSandboxConfig(**overrides)  # type: ignore[arg-type]
    sandbox = DockerSandbox(config=config, workspace=workspace)
    return cast("JsonDict", sandbox._build_host_config())


class TestExtraTmpfsPaths:
    def test_tmp_is_writable_without_asking(self, tmp_path: Path) -> None:
        mounts = cast("dict[str, str]", _host_config(tmp_path)["Tmpfs"])
        assert CONTAINER_TMP in mounts

    def test_a_declared_path_becomes_a_mount(self, tmp_path: Path) -> None:
        mounts = cast(
            "dict[str, str]",
            _host_config(tmp_path, extra_tmpfs_paths=(_HOME,))["Tmpfs"],
        )
        assert _HOME in mounts

    def test_a_declared_path_is_hardened_like_tmp(self, tmp_path: Path) -> None:
        # A writable mount the sandbox may execute from would hand back the
        # ground the read-only root filesystem takes away.
        mounts = cast(
            "dict[str, str]",
            _host_config(tmp_path, extra_tmpfs_paths=(_HOME,))["Tmpfs"],
        )
        assert mounts[_HOME] == mounts[CONTAINER_TMP]

    def test_every_mount_states_its_own_mode(self, tmp_path: Path) -> None:
        # Docker copies the mountpoint's mode from the image but not its
        # ownership, so a home the image ships 0700 for its own user becomes a
        # root-owned tmpfs: present, and unwritable by the process that needs
        # it. An inherited mode is therefore never good enough.
        mounts = cast(
            "dict[str, str]",
            _host_config(tmp_path, extra_tmpfs_paths=(_HOME,))["Tmpfs"],
        )
        assert all("mode=1777" in spec for spec in mounts.values())

    def test_the_root_filesystem_stays_read_only(self, tmp_path: Path) -> None:
        host_config = _host_config(tmp_path, extra_tmpfs_paths=(_HOME,))
        assert host_config["ReadonlyRootfs"] is True


class TestExtraTmpfsValidation:
    def test_rejects_a_relative_path(self) -> None:
        with pytest.raises(ValueError, match="absolute"):
            DockerSandboxConfig(extra_tmpfs_paths=("home/agent",))

    def test_rejects_the_workspace(self) -> None:
        # A tmpfs over the bind would hide the workspace, so every file the
        # agent produced would vanish with the container while the run still
        # reported success.
        with pytest.raises(ValueError, match="workspace"):
            DockerSandboxConfig(extra_tmpfs_paths=(CONTAINER_WORKSPACE,))

    def test_rejects_a_path_inside_the_workspace(self) -> None:
        with pytest.raises(ValueError, match="workspace"):
            DockerSandboxConfig(extra_tmpfs_paths=(f"{CONTAINER_WORKSPACE}/build",))

    def test_rejects_the_root_directory(self) -> None:
        with pytest.raises(ValueError, match="absolute"):
            DockerSandboxConfig(extra_tmpfs_paths=("/",))

    def test_rejects_a_duplicate(self) -> None:
        with pytest.raises(ValueError, match="duplicate"):
            DockerSandboxConfig(extra_tmpfs_paths=(_HOME, _HOME))

    def test_defaults_to_none(self) -> None:
        assert DockerSandboxConfig().extra_tmpfs_paths == ()


class TestOpenHandsWiring:
    def test_the_sdk_state_home_is_an_absolute_path(self) -> None:
        # Handed straight to the config, so a relative value would fail at
        # container-build time on a live run rather than here.
        config = DockerSandboxConfig(extra_tmpfs_paths=(_SDK_STATE_HOME,))
        assert config.extra_tmpfs_paths == (_SDK_STATE_HOME,)
