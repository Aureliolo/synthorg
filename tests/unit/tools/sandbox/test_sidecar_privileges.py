"""The sidecar's capability grant has to cover its own descent.

Docker cannot hand a capability to a non-root container process: on execve the
kernel builds the permitted set from the binary's file capabilities and the
ambient set, both empty here, so ``CapAdd`` leaves a bounding ceiling and
nothing else. The sidecar therefore enters as uid 0, installs its netfilter
rules, and drops to its serving account, which is why the grant covers three
capabilities rather than one: ``NET_ADMIN`` writes the rules, and
``SETUID``/``SETGID`` are what ``setgroups(2)`` and ``setuid(2)`` themselves
require. Without the latter two the drop fails, the sidecar exits before
becoming healthy, and every egress-pinned sandbox -- the whole OpenHands loop
included -- fails to start.
"""

from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from synthorg.tools.sandbox.deployment_identity import (
    DEPLOYMENT_LABEL,
    MANAGED_LABEL,
    MANAGED_LABEL_VALUE,
    deployment_id_for,
)
from synthorg.tools.sandbox.docker_config import DockerSandboxConfig
from synthorg.tools.sandbox.docker_sandbox import DockerSandbox
from tests._shared import FakeDockerClient, JsonDict

pytestmark = pytest.mark.unit


class _RecordingDocker(FakeDockerClient):
    """A Docker client that records the create payload instead of sending it."""

    def __init__(self) -> None:
        self.created: list[JsonDict] = []
        super().__init__(SimpleNamespace(create=self._create))

    async def _create(self, config: JsonDict) -> SimpleNamespace:
        self.created.append(config)
        return SimpleNamespace(id="sidecar-container-id")


async def _sidecar_config(workspace: Path) -> JsonDict:
    """Create a sidecar and return the whole payload it asked Docker for.

    Args:
        workspace: Directory the sandbox binds as its workspace.

    Returns:
        The recorded container-creation mapping.
    """
    docker = _RecordingDocker()
    sandbox = DockerSandbox(
        config=DockerSandboxConfig(
            network="bridge",
            allowed_hosts=("gateway.internal:3001",),
        ),
        workspace=workspace,
    )
    await sandbox._create_sidecar(docker)
    return docker.created[-1]


async def _sidecar_host_config(workspace: Path) -> JsonDict:
    """Create a sidecar and return the HostConfig it asked Docker for.

    Args:
        workspace: Directory the sandbox binds as its workspace.

    Returns:
        The recorded ``HostConfig`` mapping.
    """
    return cast("JsonDict", (await _sidecar_config(workspace))["HostConfig"])


class TestCapabilityGrant:
    async def test_grants_what_writing_netfilter_rules_needs(
        self, tmp_path: Path
    ) -> None:
        host_config = await _sidecar_host_config(tmp_path)
        assert "NET_ADMIN" in cast("list[str]", host_config["CapAdd"])

    async def test_grants_what_giving_up_privilege_needs(self, tmp_path: Path) -> None:
        host_config = await _sidecar_host_config(tmp_path)
        granted = cast("list[str]", host_config["CapAdd"])
        assert {"SETUID", "SETGID"} <= set(granted)

    async def test_grants_nothing_beyond_those_three(self, tmp_path: Path) -> None:
        # CAP_NET_RAW in particular: the legacy iptables front end needs it,
        # the nft one the sidecar drives does not, and raw sockets would be
        # reachable from the namespace the sandbox shares.
        host_config = await _sidecar_host_config(tmp_path)
        granted = set(cast("list[str]", host_config["CapAdd"]))
        assert granted == {"NET_ADMIN", "SETUID", "SETGID"}

    async def test_drops_everything_it_does_not_name(self, tmp_path: Path) -> None:
        host_config = await _sidecar_host_config(tmp_path)
        assert cast("list[str]", host_config["CapDrop"]) == ["ALL"]


class TestRemainingConfinement:
    async def test_privilege_escalation_stays_blocked(self, tmp_path: Path) -> None:
        # no-new-privileges is what rules out file capabilities as an
        # alternative to entering as root, so the two decisions travel
        # together: relaxing it would reopen the option this one closes.
        host_config = await _sidecar_host_config(tmp_path)
        assert "no-new-privileges" in cast("list[str]", host_config["SecurityOpt"])

    async def test_root_filesystem_stays_read_only(self, tmp_path: Path) -> None:
        host_config = await _sidecar_host_config(tmp_path)
        assert host_config["ReadonlyRootfs"] is True

    async def test_the_container_is_never_privileged(self, tmp_path: Path) -> None:
        host_config = await _sidecar_host_config(tmp_path)
        assert not host_config.get("Privileged")


class TestReclaimability:
    """A sidecar a hard kill left behind has to be identifiable as ours.

    The boot reconciliation pass separates "a container this deployment
    created" from "another installation's live work" by these two labels
    alone. Unlabelled, an orphaned sidecar is never reclaimed and goes on
    policing egress for a sandbox that no longer exists.
    """

    async def test_the_sidecar_carries_both_deployment_labels(
        self, tmp_path: Path
    ) -> None:
        labels = cast("dict[str, str]", (await _sidecar_config(tmp_path))["Labels"])

        assert labels[MANAGED_LABEL] == MANAGED_LABEL_VALUE
        assert labels[DEPLOYMENT_LABEL] == deployment_id_for(tmp_path)
