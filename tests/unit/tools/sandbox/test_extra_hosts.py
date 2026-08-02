"""``extra_hosts`` reaches the container that actually owns ``/etc/hosts``.

A sandbox that joins a sidecar's network namespace reads the SIDECAR's
``/etc/hosts``, and Docker refuses ``ExtraHosts`` on the joining container
outright. So the aliases have to land on the sidecar whenever egress
enforcement is on, and on the container itself only when it is not: getting
that split wrong either fails container creation or silently leaves the alias
unresolvable, which is what makes the shipped OpenHands endpoint defaults
reachable or dead.
"""

from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from synthorg.tools.sandbox.docker_config import DockerSandboxConfig
from synthorg.tools.sandbox.docker_sandbox import DockerSandbox
from synthorg.workers._openhands_wiring import _HOST_GATEWAY_ALIAS
from tests._shared import FakeDockerClient, JsonDict

pytestmark = pytest.mark.unit

_ALIAS = "host.docker.internal:host-gateway"


def _sandbox(workspace: Path, **overrides: object) -> DockerSandbox:
    config = DockerSandboxConfig(**overrides)  # type: ignore[arg-type]
    return DockerSandbox(config=config, workspace=workspace)


class _RecordingDocker(FakeDockerClient):
    """A Docker client that records the create payload instead of sending it."""

    def __init__(self) -> None:
        self.created: list[JsonDict] = []
        super().__init__(SimpleNamespace(create=self._create))

    async def _create(self, config: JsonDict) -> SimpleNamespace:
        self.created.append(config)
        return SimpleNamespace(id="sidecar-container-id")

    def host_config_of_last_create(self) -> JsonDict:
        """Return the HostConfig the sandbox asked Docker to create with.

        Returns:
            The ``HostConfig`` mapping from the most recent create call.
        """
        return cast("JsonDict", self.created[-1]["HostConfig"])


class TestConfigValidation:
    def test_accepts_a_name_target_pair(self) -> None:
        config = DockerSandboxConfig(extra_hosts=(_ALIAS,))
        assert config.extra_hosts == (_ALIAS,)

    @pytest.mark.parametrize(
        "bad",
        ["host.docker.internal", "a:b:c", ":host-gateway", "host.docker.internal:"],
    )
    def test_rejects_a_malformed_entry(self, bad: str) -> None:
        with pytest.raises(ValueError, match="extra_hosts"):
            DockerSandboxConfig(extra_hosts=(bad,))

    def test_defaults_to_no_aliases(self) -> None:
        assert DockerSandboxConfig().extra_hosts == ()


class TestContainerHostConfig:
    def test_alias_applies_when_no_sidecar_owns_the_namespace(
        self, tmp_path: Path
    ) -> None:
        sandbox = _sandbox(tmp_path, network="bridge", extra_hosts=(_ALIAS,))
        host_config = cast("JsonDict", sandbox._build_host_config())
        assert host_config["ExtraHosts"] == [_ALIAS]

    def test_alias_withheld_when_the_container_joins_a_sidecar(
        self, tmp_path: Path
    ) -> None:
        # allowed_hosts turns sidecar enforcement on, so this container will run
        # in the sidecar's namespace and Docker would reject ExtraHosts here.
        sandbox = _sandbox(
            tmp_path,
            network="bridge",
            allowed_hosts=("gateway.internal:3001",),
            extra_hosts=(_ALIAS,),
        )
        host_config = cast("JsonDict", sandbox._build_host_config())
        assert "ExtraHosts" not in host_config

    def test_absent_when_no_alias_is_configured(self, tmp_path: Path) -> None:
        sandbox = _sandbox(tmp_path, network="bridge")
        host_config = cast("JsonDict", sandbox._build_host_config())
        assert "ExtraHosts" not in host_config


class TestSidecarHostConfig:
    async def test_alias_lands_on_the_namespace_owner(self, tmp_path: Path) -> None:
        docker = _RecordingDocker()
        sandbox = _sandbox(
            tmp_path,
            network="bridge",
            allowed_hosts=("gateway.internal:3001",),
            extra_hosts=(_ALIAS,),
        )

        await sandbox._create_sidecar(docker)

        assert docker.host_config_of_last_create()["ExtraHosts"] == [_ALIAS]

    async def test_absent_when_no_alias_is_configured(self, tmp_path: Path) -> None:
        docker = _RecordingDocker()
        sandbox = _sandbox(
            tmp_path, network="bridge", allowed_hosts=("gateway.internal:3001",)
        )

        await sandbox._create_sidecar(docker)

        assert "ExtraHosts" not in docker.host_config_of_last_create()


class TestLoopAlias:
    def test_openhands_alias_satisfies_the_validator(self) -> None:
        # The wiring hands this constant straight to DockerSandboxConfig, so a
        # typo there would fail at container-build time, on a live run.
        config = DockerSandboxConfig(extra_hosts=_HOST_GATEWAY_ALIAS)
        assert config.extra_hosts == (_ALIAS,)
