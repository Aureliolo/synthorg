"""Tests for sandbox sidecar-based network enforcement in container config."""

from pathlib import Path
from typing import Any, Literal, cast

import pytest

from synthorg.tools.sandbox.docker_config import DockerSandboxConfig
from synthorg.tools.sandbox.docker_sandbox import DockerSandbox
from tests._shared import JsonDict

pytestmark = pytest.mark.unit


def _container_config(sandbox: DockerSandbox, **kwargs: Any) -> JsonDict:  # type: ignore[explicit-any]  # kwargs forwarded to _build_container_config
    """Return the built container config as a freely-indexable mapping."""
    return cast(JsonDict, sandbox._build_container_config(**kwargs))


def _build_config(  # noqa: PLR0913
    tmp_path: Path,
    *,
    network: Literal["none", "bridge", "host"] = "bridge",
    allowed_hosts: tuple[str, ...] = ("example.com:443",),
    dns_allowed: bool = True,
    loopback_allowed: bool = True,
    network_allow_all: bool = False,
) -> JsonDict:
    """Build container config for the given sandbox settings."""
    config = DockerSandboxConfig(
        network=network,
        allowed_hosts=allowed_hosts,
        dns_allowed=dns_allowed,
        loopback_allowed=loopback_allowed,
        network_allow_all=network_allow_all,
    )
    sandbox = DockerSandbox(config=config, workspace=tmp_path)
    return _container_config(
        sandbox,
        command="echo",
        args=("hello",),
        container_cwd="/workspace",
        env_overrides=None,
    )


# -- Sidecar enforcement: sandbox container has NO elevated privileges -----


class TestSidecarSandboxConfig:
    """When allowed_hosts is set and network is bridge, the sandbox
    container must NOT have elevated privileges (sidecar handles
    enforcement).
    """

    def test_sandbox_no_cap_add(self, tmp_path: Path) -> None:
        result = _build_config(tmp_path)
        assert "CapAdd" not in result["HostConfig"]

    def test_sandbox_user_not_root(self, tmp_path: Path) -> None:
        result = _build_config(tmp_path)
        assert "User" not in result

    def test_sandbox_no_entrypoint_override(self, tmp_path: Path) -> None:
        result = _build_config(tmp_path)
        assert "Entrypoint" not in result

    def test_sandbox_no_iptables_env_vars(self, tmp_path: Path) -> None:
        result = _build_config(tmp_path)
        env = result["Env"]
        assert not any(e.startswith("SANDBOX_ALLOWED_HOSTS=") for e in env)
        assert not any(e.startswith("SANDBOX_DNS_ALLOWED=") for e in env)
        assert not any(e.startswith("SANDBOX_LOOPBACK_ALLOWED=") for e in env)

    def test_sandbox_no_run_tmpfs(self, tmp_path: Path) -> None:
        result = _build_config(tmp_path)
        tmpfs = result["HostConfig"]["Tmpfs"]
        assert "/run" not in tmpfs

    def test_sandbox_cap_drop_all_still_present(self, tmp_path: Path) -> None:
        result = _build_config(tmp_path)
        assert result["HostConfig"]["CapDrop"] == ["ALL"]

    def test_sandbox_no_sidecar_env_vars(self, tmp_path: Path) -> None:
        result = _build_config(tmp_path)
        env = result["Env"]
        assert not any(e.startswith("SIDECAR_") for e in env)


# -- Sidecar: needs_sidecar predicate ------------------------------------


class TestNeedsSidecar:
    """_needs_sidecar returns True when enforcement should activate."""

    def test_needs_sidecar_with_hosts_and_bridge(
        self,
        tmp_path: Path,
    ) -> None:
        config = DockerSandboxConfig(
            network="bridge",
            allowed_hosts=("example.com:443",),
        )
        sandbox = DockerSandbox(config=config, workspace=tmp_path)
        assert sandbox._needs_sidecar()

    def test_no_sidecar_when_hosts_empty(self, tmp_path: Path) -> None:
        config = DockerSandboxConfig(network="bridge")
        sandbox = DockerSandbox(config=config, workspace=tmp_path)
        assert not sandbox._needs_sidecar()

    def test_no_sidecar_when_network_none(self, tmp_path: Path) -> None:
        config = DockerSandboxConfig(
            network="none",
            allowed_hosts=("example.com:443",),
        )
        sandbox = DockerSandbox(config=config, workspace=tmp_path)
        assert not sandbox._needs_sidecar()

    def test_needs_sidecar_with_allow_all(self, tmp_path: Path) -> None:
        config = DockerSandboxConfig(
            network="bridge",
            network_allow_all=True,
        )
        sandbox = DockerSandbox(config=config, workspace=tmp_path)
        assert sandbox._needs_sidecar()

    def test_default_config_no_sidecar(self, tmp_path: Path) -> None:
        sandbox = DockerSandbox(workspace=tmp_path)
        assert not sandbox._needs_sidecar()


# -- Network mode override -----------------------------------------------


class TestNetworkModeOverride:
    """_build_container_config with network_mode parameter."""

    def test_network_mode_overrides_default(self, tmp_path: Path) -> None:
        config = DockerSandboxConfig(network="bridge")
        sandbox = DockerSandbox(config=config, workspace=tmp_path)
        result = _container_config(
            sandbox,
            command="echo",
            args=(),
            container_cwd="/workspace",
            env_overrides=None,
            network_mode="container:abc123",
        )
        assert result["HostConfig"]["NetworkMode"] == "container:abc123"

    def test_no_override_uses_config_network(self, tmp_path: Path) -> None:
        config = DockerSandboxConfig(network="bridge")
        sandbox = DockerSandbox(config=config, workspace=tmp_path)
        result = _container_config(
            sandbox,
            command="echo",
            args=(),
            container_cwd="/workspace",
            env_overrides=None,
        )
        assert result["HostConfig"]["NetworkMode"] == "bridge"


# -- Enforcement NOT activated -------------------------------------------


class TestEnforcementInactive:
    """No sidecar modifications when conditions are not met."""

    def test_no_enforcement_when_hosts_empty(self, tmp_path: Path) -> None:
        config = DockerSandboxConfig(network="bridge")
        sandbox = DockerSandbox(config=config, workspace=tmp_path)
        result = _container_config(
            sandbox,
            command="echo",
            args=(),
            container_cwd="/workspace",
            env_overrides=None,
        )
        env = result["Env"]
        assert not any(e.startswith("SIDECAR_") for e in env)
        assert "CapAdd" not in result["HostConfig"]
        assert "User" not in result
        assert "Entrypoint" not in result

    def test_default_config_no_enforcement(self, tmp_path: Path) -> None:
        sandbox = DockerSandbox(workspace=tmp_path)
        result = _container_config(
            sandbox,
            command="echo",
            args=(),
            container_cwd="/workspace",
            env_overrides=None,
        )
        assert "CapAdd" not in result["HostConfig"]
        assert "User" not in result
        assert "Entrypoint" not in result


# -- env_overrides coexistence -------------------------------------------


class TestAllowedHostsWithEnvOverrides:
    """User-provided env_overrides coexist with enforcement vars."""

    def test_user_env_preserved(self, tmp_path: Path) -> None:
        config = DockerSandboxConfig(
            network="bridge",
            allowed_hosts=("example.com:443",),
        )
        sandbox = DockerSandbox(config=config, workspace=tmp_path)
        result = _container_config(
            sandbox,
            command="echo",
            args=(),
            container_cwd="/workspace",
            env_overrides={"MY_VAR": "hello"},
        )
        env = result["Env"]
        assert "MY_VAR=hello" in env

    @pytest.mark.parametrize(
        "env_key",
        [
            "SIDECAR_ALLOWED_HOSTS",
            "SIDECAR_DNS_ALLOWED",
            "SIDECAR_LOOPBACK_ALLOWED",
            "SIDECAR_ALLOW_ALL",
            "SANDBOX_ALLOWED_HOSTS",
            "SANDBOX_DNS_ALLOWED",
            "SANDBOX_LOOPBACK_ALLOWED",
        ],
    )
    def test_reserved_env_key_rejected(
        self,
        tmp_path: Path,
        env_key: str,
    ) -> None:
        from synthorg.tools.sandbox.errors import SandboxError

        config = DockerSandboxConfig(
            network="bridge",
            allowed_hosts=("example.com:443",),
        )
        sandbox = DockerSandbox(config=config, workspace=tmp_path)
        with pytest.raises(SandboxError, match="reserved"):
            _container_config(
                sandbox,
                command="echo",
                args=(),
                container_cwd="/workspace",
                env_overrides={env_key: "evil"},
            )
