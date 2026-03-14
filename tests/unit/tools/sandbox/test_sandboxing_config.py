"""Tests for SandboxingConfig validation."""

import pytest
from pydantic import ValidationError

from synthorg.tools.sandbox.docker_config import DockerSandboxConfig
from synthorg.tools.sandbox.sandboxing_config import SandboxingConfig

pytestmark = [pytest.mark.unit, pytest.mark.timeout(30)]


class TestSandboxingConfigDefaults:
    """Default values and frozen behavior."""

    def test_defaults(self) -> None:
        config = SandboxingConfig()
        assert config.default_backend == "subprocess"
        assert config.overrides == {}
        assert config.subprocess.timeout_seconds == 30.0
        assert config.docker.image == "synthorg-sandbox:latest"

    def test_frozen(self) -> None:
        config = SandboxingConfig()
        with pytest.raises(ValidationError):
            config.default_backend = "docker"  # type: ignore[misc]


class TestSandboxingConfigCustomValues:
    """Custom values are accepted."""

    def test_docker_default_backend(self) -> None:
        config = SandboxingConfig(default_backend="docker")
        assert config.default_backend == "docker"

    def test_invalid_backend(self) -> None:
        with pytest.raises(ValidationError):
            SandboxingConfig(default_backend="kubernetes")  # type: ignore[arg-type]

    def test_overrides(self) -> None:
        overrides: dict[str, str] = {
            "code_execution": "docker",
            "terminal": "subprocess",
        }
        config = SandboxingConfig(overrides=overrides)  # type: ignore[arg-type]
        assert config.overrides == overrides

    def test_invalid_override_backend_rejected(self) -> None:
        with pytest.raises(ValidationError, match="literal_error"):
            SandboxingConfig(
                overrides={"code_execution": "kubernetes"},  # type: ignore[dict-item]
            )

    def test_custom_docker_config(self) -> None:
        docker = DockerSandboxConfig(image="custom:v2", cpu_limit=4.0)
        config = SandboxingConfig(docker=docker)
        assert config.docker.image == "custom:v2"
        assert config.docker.cpu_limit == 4.0


class TestBackendForCategory:
    """backend_for_category routing logic."""

    def test_returns_default_when_no_override(self) -> None:
        config = SandboxingConfig(default_backend="subprocess")
        assert config.backend_for_category("file_system") == "subprocess"

    def test_returns_override_when_present(self) -> None:
        config = SandboxingConfig(
            default_backend="subprocess",
            overrides={"code_execution": "docker"},
        )
        assert config.backend_for_category("code_execution") == "docker"

    def test_returns_default_for_unconfigured_category(self) -> None:
        config = SandboxingConfig(
            default_backend="docker",
            overrides={"code_execution": "subprocess"},
        )
        assert config.backend_for_category("terminal") == "docker"

    @pytest.mark.parametrize(
        ("default", "override_backend", "expected"),
        [
            ("subprocess", "docker", "docker"),
            ("docker", "subprocess", "subprocess"),
        ],
    )
    def test_parametrized_routing(
        self,
        default: str,
        override_backend: str,
        expected: str,
    ) -> None:
        config = SandboxingConfig(
            default_backend=default,  # type: ignore[arg-type]
            overrides={"code_execution": override_backend},  # type: ignore[dict-item]
        )
        assert config.backend_for_category("code_execution") == expected
