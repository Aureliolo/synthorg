"""How a containerised backend hands its workspace to a sibling sandbox.

A bind spec is resolved by the DAEMON, in the daemon's namespace, not in the
namespace of the process that asked for it. So a backend running in a container
that passes its own ``/data/agent-workspaces`` as a bind source names a host
path that usually does not exist, and Docker creates an empty directory and
mounts that instead. The sandbox starts, sees nothing, and every command it runs
reports an honest failure about a workspace that was never there.

These tests pin the two shapes that fix it (reproduce the parent's named volume
with a subpath, or translate through the parent's bind) and, just as much, the
refusal: a containerised process whose workspace root no mount covers must raise
rather than hand back a path that will silently resolve to nothing.
"""

import socket
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from typing import cast

import pytest

from synthorg.tools.sandbox import workspace_mount
from synthorg.tools.sandbox.docker_config import DockerSandboxConfig
from synthorg.tools.sandbox.docker_sandbox import DockerSandbox, _to_posix_bind_path
from synthorg.tools.sandbox.errors import (
    SandboxSubpathUnsupportedError,
    SandboxWorkspaceUnmappableError,
)
from synthorg.tools.sandbox.workspace_mount import (
    OwnContainer,
    WorkspaceMount,
    container_id_from_hostname,
    container_id_from_mountinfo,
    discover_own_container,
    resolve_workspace_mount,
)
from tests._shared import FakeDockerClient, JsonDict

pytestmark = pytest.mark.unit

_CONTAINER_ID = "3ad75118a7443324ebe045e52e19a23e4d8659546e6e5a67a900d18cac149b5d"
_SUPPORTED_API = "1.55"

_MOUNTINFO = (
    "772 771 8:48 /data/docker/overlay2/x/merged / rw,relatime - overlay overlay rw\n"
    f"783 772 8:48 /data/docker/containers/{_CONTAINER_ID}/resolv.conf "
    "/etc/resolv.conf ro,relatime - ext4 /dev/sdd rw\n"
)


class _InspectingDocker(FakeDockerClient):
    """A Docker client that answers one container inspect from a fixture."""

    def __init__(self, mounts: list[JsonDict], *, missing: bool = False) -> None:
        self.inspected: list[str] = []
        self._mounts = mounts
        self._missing = missing
        super().__init__(SimpleNamespace(container=self._container))

    def _container(self, container_id: str) -> SimpleNamespace:
        self.inspected.append(container_id)
        return SimpleNamespace(show=self._show)

    async def _show(self) -> JsonDict:
        if self._missing:
            msg = "no such container"
            raise LookupError(msg)
        return cast("JsonDict", {"Mounts": self._mounts})


def _volume_mount(name: str, destination: str) -> JsonDict:
    return cast(
        "JsonDict",
        {
            "Type": "volume",
            "Name": name,
            "Source": f"/var/lib/docker/volumes/{name}/_data",
            "Destination": destination,
        },
    )


def _bind_mount(source: str, destination: str) -> JsonDict:
    return cast(
        "JsonDict", {"Type": "bind", "Source": source, "Destination": destination}
    )


class TestWorkspaceMountShape:
    def test_refuses_naming_neither_storage(self) -> None:
        with pytest.raises(ValueError, match="exactly one"):
            WorkspaceMount()

    def test_refuses_naming_both(self) -> None:
        with pytest.raises(ValueError, match="exactly one"):
            WorkspaceMount(volume="v", host_path="/data")

    def test_child_extends_a_volume_subpath(self) -> None:
        mount = WorkspaceMount(volume="v", subpath="agent-workspaces")
        child = mount.child(PurePosixPath("projects/proj-a"))
        assert child.volume == "v"
        assert child.subpath == "agent-workspaces/projects/proj-a"

    def test_child_extends_a_host_path(self) -> None:
        mount = WorkspaceMount(host_path="/srv/ws")
        assert mount.child(PurePosixPath("projects/proj-a")).host_path == (
            "/srv/ws/projects/proj-a"
        )

    def test_child_of_the_root_is_unchanged(self) -> None:
        mount = WorkspaceMount(volume="v", subpath="agent-workspaces")
        assert mount.child(PurePosixPath()) == mount


class TestContainerIdDiscovery:
    def test_reads_the_full_id_from_mountinfo(self, tmp_path: Path) -> None:
        mountinfo = tmp_path / "mountinfo"
        mountinfo.write_text(_MOUNTINFO, encoding="utf-8")
        assert container_id_from_mountinfo(mountinfo) == _CONTAINER_ID

    def test_absent_mountinfo_is_not_containerised(self, tmp_path: Path) -> None:
        assert container_id_from_mountinfo(tmp_path / "nothing") is None

    def test_host_mountinfo_names_no_container(self, tmp_path: Path) -> None:
        mountinfo = tmp_path / "mountinfo"
        mountinfo.write_text(
            "25 0 8:1 / / rw,relatime - ext4 /dev/sda1 rw\n", encoding="utf-8"
        )
        assert container_id_from_mountinfo(mountinfo) is None

    def test_hostname_that_is_a_short_id_is_a_candidate(self) -> None:
        assert container_id_from_hostname("3ad75118a744") == "3ad75118a744"

    def test_an_ordinary_hostname_is_not(self) -> None:
        assert container_id_from_hostname("my-laptop") is None

    def test_a_mountinfo_id_is_certain_and_a_hostname_guess_is_not(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The two consumers (the sandbox resolving a mount, the boot probe
        # reporting whether it can) ask this one function, so the certainty
        # rule cannot differ between them.
        monkeypatch.setattr(
            workspace_mount, "container_id_from_mountinfo", lambda: _CONTAINER_ID
        )
        assert discover_own_container() == OwnContainer(
            container_id=_CONTAINER_ID, certain=True
        )

        monkeypatch.setattr(
            workspace_mount, "container_id_from_mountinfo", lambda: None
        )
        monkeypatch.setattr(socket, "gethostname", lambda: "my-laptop")
        assert discover_own_container() == OwnContainer(
            container_id=None, certain=False
        )


class TestUncontainerised:
    async def test_no_container_id_leaves_the_host_path_to_the_caller(
        self, tmp_path: Path
    ) -> None:
        docker = _InspectingDocker([])
        assert (
            await resolve_workspace_mount(
                docker=docker,
                root=tmp_path,
                api_version=_SUPPORTED_API,
                container_id=None,
            )
            is None
        )
        assert docker.inspected == []


class TestVolumeParent:
    async def test_reproduces_the_volume_with_a_subpath(self) -> None:
        docker = _InspectingDocker([_volume_mount("data_synthorg-data", "/data")])
        mount = await resolve_workspace_mount(
            docker=docker,
            root=Path("/data/agent-workspaces"),
            api_version=_SUPPORTED_API,
            container_id=_CONTAINER_ID,
        )
        assert mount == WorkspaceMount(
            volume="data_synthorg-data", subpath="agent-workspaces"
        )

    async def test_root_of_the_volume_needs_no_subpath(self) -> None:
        docker = _InspectingDocker([_volume_mount("vol", "/data")])
        mount = await resolve_workspace_mount(
            docker=docker,
            root=Path("/data"),
            api_version="1.40",
            container_id=_CONTAINER_ID,
        )
        assert mount == WorkspaceMount(volume="vol", subpath="")

    async def test_the_longest_destination_wins(self) -> None:
        docker = _InspectingDocker(
            [
                _volume_mount("outer", "/data"),
                _volume_mount("inner", "/data/agent-workspaces"),
            ]
        )
        mount = await resolve_workspace_mount(
            docker=docker,
            root=Path("/data/agent-workspaces"),
            api_version=_SUPPORTED_API,
            container_id=_CONTAINER_ID,
        )
        assert mount == WorkspaceMount(volume="inner", subpath="")

    async def test_a_sibling_prefix_does_not_match(self) -> None:
        # "/data-other" starts with "/data" as a STRING but is not below it.
        docker = _InspectingDocker([_volume_mount("other", "/data-other")])
        with pytest.raises(SandboxWorkspaceUnmappableError):
            await resolve_workspace_mount(
                docker=docker,
                root=Path("/data/agent-workspaces"),
                api_version=_SUPPORTED_API,
                container_id=_CONTAINER_ID,
            )


class TestBindParent:
    async def test_translates_through_the_parent_bind(self) -> None:
        docker = _InspectingDocker([_bind_mount("/host/synthorg", "/data")])
        mount = await resolve_workspace_mount(
            docker=docker,
            root=Path("/data/agent-workspaces"),
            api_version="1.40",
            container_id=_CONTAINER_ID,
        )
        assert mount == WorkspaceMount(host_path="/host/synthorg/agent-workspaces")


class TestRefusals:
    async def test_no_covering_mount_raises_and_names_what_was_considered(
        self,
    ) -> None:
        docker = _InspectingDocker([_volume_mount("vol", "/srv")])
        with pytest.raises(SandboxWorkspaceUnmappableError) as excinfo:
            await resolve_workspace_mount(
                docker=docker,
                root=Path("/data/agent-workspaces"),
                api_version=_SUPPORTED_API,
                container_id=_CONTAINER_ID,
            )
        message = str(excinfo.value)
        assert "/data/agent-workspaces" in message
        assert "/srv" in message

    async def test_a_hostname_guess_that_is_not_a_container_is_not_fatal(self) -> None:
        docker = _InspectingDocker([], missing=True)
        assert (
            await resolve_workspace_mount(
                docker=docker,
                root=Path("/data/agent-workspaces"),
                api_version=_SUPPORTED_API,
                container_id="3ad75118a744",
                certain=False,
            )
            is None
        )

    async def test_a_known_container_that_cannot_be_inspected_raises(self) -> None:
        docker = _InspectingDocker([], missing=True)
        with pytest.raises(SandboxWorkspaceUnmappableError):
            await resolve_workspace_mount(
                docker=docker,
                root=Path("/data/agent-workspaces"),
                api_version=_SUPPORTED_API,
                container_id=_CONTAINER_ID,
            )

    @pytest.mark.parametrize("api_version", ["1.44", "1.9", "not-a-version", ""])
    async def test_a_subpath_the_daemon_cannot_serve_raises(
        self, api_version: str
    ) -> None:
        docker = _InspectingDocker([_volume_mount("vol", "/data")])
        with pytest.raises(SandboxSubpathUnsupportedError) as excinfo:
            await resolve_workspace_mount(
                docker=docker,
                root=Path("/data/agent-workspaces"),
                api_version=api_version,
                container_id=_CONTAINER_ID,
            )
        assert "1.45" in str(excinfo.value)

    async def test_the_minimum_api_version_is_accepted(self) -> None:
        docker = _InspectingDocker([_volume_mount("vol", "/data")])
        mount = await resolve_workspace_mount(
            docker=docker,
            root=Path("/data/agent-workspaces"),
            api_version="1.45",
            container_id=_CONTAINER_ID,
        )
        assert mount is not None
        assert mount.volume == "vol"


class TestHostConfigStorage:
    """What the resolved mount turns into on the container create payload."""

    def test_host_run_keeps_the_path_bind(self, tmp_path: Path) -> None:
        sandbox = DockerSandbox(workspace=tmp_path)
        host_config = cast("JsonDict", sandbox._build_host_config())
        assert host_config["Binds"] == [
            f"{_to_posix_bind_path(tmp_path)}:/workspace:ro"
        ]
        assert "Mounts" not in host_config

    def test_a_volume_parent_becomes_a_subpath_mount(self, tmp_path: Path) -> None:
        project = tmp_path / "projects" / "proj-a"
        project.mkdir(parents=True)
        sandbox = DockerSandbox(workspace=tmp_path)
        sandbox._workspace_mount = WorkspaceMount(
            volume="data_synthorg-data", subpath="agent-workspaces"
        )

        host_config = cast("JsonDict", sandbox._build_host_config(project))

        assert "Binds" not in host_config
        assert host_config["Mounts"] == [
            {
                "Type": "volume",
                "Source": "data_synthorg-data",
                "Target": "/workspace",
                "ReadOnly": True,
                "VolumeOptions": {
                    "Subpath": "agent-workspaces/projects/proj-a",
                },
            }
        ]

    def test_a_writable_mount_mode_is_carried_through(self, tmp_path: Path) -> None:
        sandbox = DockerSandbox(
            config=DockerSandboxConfig(mount_mode="rw"), workspace=tmp_path
        )
        sandbox._workspace_mount = WorkspaceMount(volume="vol", subpath="ws")

        mounts = cast("JsonDict", sandbox._build_host_config())["Mounts"]

        assert mounts[0]["ReadOnly"] is False

    def test_a_bind_parent_becomes_a_translated_bind(self, tmp_path: Path) -> None:
        project = tmp_path / "projects" / "proj-a"
        project.mkdir(parents=True)
        sandbox = DockerSandbox(workspace=tmp_path)
        sandbox._workspace_mount = WorkspaceMount(host_path="/host/ws")

        host_config = cast("JsonDict", sandbox._build_host_config(project))

        assert host_config["Binds"] == ["/host/ws/projects/proj-a:/workspace:ro"]

    def test_a_root_outside_the_resolved_workspace_is_refused(
        self, tmp_path: Path
    ) -> None:
        sandbox = DockerSandbox(workspace=tmp_path)
        sandbox._workspace_mount = WorkspaceMount(volume="vol", subpath="ws")
        with pytest.raises(SandboxWorkspaceUnmappableError, match="outside"):
            sandbox._build_host_config(tmp_path.parent)
