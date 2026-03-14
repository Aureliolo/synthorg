"""Tests for DockerSandboxConfig validation."""

import pytest
from pydantic import ValidationError

from synthorg.tools.sandbox.docker_config import DockerSandboxConfig

pytestmark = [pytest.mark.unit, pytest.mark.timeout(30)]


class TestDockerSandboxConfigDefaults:
    """Default values are sensible."""

    def test_defaults(self) -> None:
        config = DockerSandboxConfig()
        assert config.image == "synthorg-sandbox:latest"
        assert config.network == "none"
        assert config.network_overrides == {}
        assert config.allowed_hosts == ()
        assert config.memory_limit == "512m"
        assert config.cpu_limit == 1.0
        assert config.timeout_seconds == 120.0
        assert config.mount_mode == "ro"
        assert config.runtime is None

    def test_frozen(self) -> None:
        config = DockerSandboxConfig()
        with pytest.raises(ValidationError):
            config.image = "other:latest"  # type: ignore[misc]


class TestDockerSandboxConfigCustomValues:
    """Custom values are accepted within bounds."""

    def test_custom_image(self) -> None:
        config = DockerSandboxConfig(image="custom:v1")
        assert config.image == "custom:v1"

    @pytest.mark.parametrize("network", ["none", "bridge", "host"])
    def test_valid_network_modes(self, network: str) -> None:
        config = DockerSandboxConfig(network=network)  # type: ignore[arg-type]
        assert config.network == network

    def test_invalid_network_mode(self) -> None:
        with pytest.raises(ValidationError):
            DockerSandboxConfig(network="overlay")  # type: ignore[arg-type]

    def test_network_overrides(self) -> None:
        overrides = {"web": "bridge", "data": "none"}
        config = DockerSandboxConfig(network_overrides=overrides)
        assert config.network_overrides == overrides

    def test_allowed_hosts(self) -> None:
        hosts = ("api.example.com:443", "db.internal:5432")
        config = DockerSandboxConfig(allowed_hosts=hosts)
        assert config.allowed_hosts == hosts

    @pytest.mark.parametrize("mount_mode", ["rw", "ro"])
    def test_valid_mount_modes(self, mount_mode: str) -> None:
        config = DockerSandboxConfig(mount_mode=mount_mode)  # type: ignore[arg-type]
        assert config.mount_mode == mount_mode

    def test_invalid_mount_mode(self) -> None:
        with pytest.raises(ValidationError):
            DockerSandboxConfig(mount_mode="wx")  # type: ignore[arg-type]

    def test_runtime_gvisor(self) -> None:
        config = DockerSandboxConfig(runtime="runsc")
        assert config.runtime == "runsc"


class TestDockerSandboxConfigBounds:
    """Field bounds are enforced."""

    def test_cpu_limit_zero_rejected(self) -> None:
        with pytest.raises(ValidationError, match="cpu_limit"):
            DockerSandboxConfig(cpu_limit=0)

    def test_cpu_limit_exceeds_max_rejected(self) -> None:
        with pytest.raises(ValidationError, match="cpu_limit"):
            DockerSandboxConfig(cpu_limit=17)

    def test_cpu_limit_at_max(self) -> None:
        config = DockerSandboxConfig(cpu_limit=16)
        assert config.cpu_limit == 16

    def test_timeout_zero_rejected(self) -> None:
        with pytest.raises(ValidationError, match="timeout_seconds"):
            DockerSandboxConfig(timeout_seconds=0)

    def test_timeout_exceeds_max_rejected(self) -> None:
        with pytest.raises(ValidationError, match="timeout_seconds"):
            DockerSandboxConfig(timeout_seconds=601)

    def test_timeout_at_max(self) -> None:
        config = DockerSandboxConfig(timeout_seconds=600)
        assert config.timeout_seconds == 600

    def test_blank_image_rejected(self) -> None:
        with pytest.raises(ValidationError):
            DockerSandboxConfig(image="")

    def test_whitespace_image_rejected(self) -> None:
        with pytest.raises(ValidationError):
            DockerSandboxConfig(image="   ")

    def test_blank_memory_limit_rejected(self) -> None:
        with pytest.raises(ValidationError):
            DockerSandboxConfig(memory_limit="")

    def test_invalid_network_override_value_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Invalid network mode"):
            DockerSandboxConfig(
                network_overrides={"web": "overlay"},
            )
