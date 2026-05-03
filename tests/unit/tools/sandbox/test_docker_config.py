"""Tests for DockerSandboxConfig validation."""

import pytest
from pydantic import ValidationError

from synthorg.tools.sandbox.docker_config import DockerSandboxConfig
from synthorg.tools.sandbox.policy import NetworkPolicy, SandboxPolicy

pytestmark = pytest.mark.unit


# The autouse fixture `_isolate_sandbox_image_env` lives in conftest.py so
# every test in this directory starts with a clean SYNTHORG_SANDBOX_IMAGE
# env var. Tests here that need a specific value still use monkeypatch.setenv
# explicitly.


class TestDockerSandboxConfigDefaults:
    """Default values are sensible."""

    def test_defaults(self) -> None:
        config = DockerSandboxConfig()
        assert config.image == "ghcr.io/aureliolo/synthorg-sandbox:latest"
        assert config.network == "none"
        assert config.network_overrides == {}
        assert config.runtime_overrides == {}
        assert config.allowed_hosts == ()
        assert config.memory_limit == "512m"
        assert config.cpu_limit == 1.0
        assert config.timeout_seconds == 120.0
        assert config.pids_limit == 64
        assert config.tmpfs_size == "64m"
        assert config.sidecar_pids_limit == 32
        assert config.sidecar_tmpfs_size == "8m"
        assert config.mount_mode == "ro"
        assert config.runtime is None

    def test_frozen(self) -> None:
        config = DockerSandboxConfig()
        with pytest.raises(ValidationError):
            config.image = "other:latest"  # type: ignore[misc]


class TestDockerSandboxConfigImageResolution:
    """SYNTHORG_SANDBOX_IMAGE env var drives the default image reference.

    The CLI injects the digest-pinned sandbox image reference into the
    backend container via this env var so the CLI and backend stay
    version-locked. Explicit YAML config still wins over the env var.
    Both the env-var-resolved and fallback branches emit structured log
    events so operators debugging image mismatches have a signal to follow.
    """

    def test_env_var_provides_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        pinned = (
            "ghcr.io/aureliolo/synthorg-sandbox@sha256:"
            "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
        )
        monkeypatch.setenv("SYNTHORG_SANDBOX_IMAGE", pinned)
        config = DockerSandboxConfig()
        assert config.image == pinned

    @pytest.mark.parametrize(
        ("env_action", "env_value"),
        [
            ("delenv", None),
            ("setenv", ""),
            ("setenv", "   "),
        ],
        ids=["unset", "empty", "whitespace"],
    )
    def test_fallback_when_env_var_absent_or_blank(
        self,
        monkeypatch: pytest.MonkeyPatch,
        env_action: str,
        env_value: str | None,
    ) -> None:
        if env_action == "delenv":
            monkeypatch.delenv("SYNTHORG_SANDBOX_IMAGE", raising=False)
        else:
            monkeypatch.setenv("SYNTHORG_SANDBOX_IMAGE", env_value)  # type: ignore[arg-type]
        config = DockerSandboxConfig()
        assert config.image == "ghcr.io/aureliolo/synthorg-sandbox:latest"

    def test_explicit_yaml_wins_over_env_var(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(
            "SYNTHORG_SANDBOX_IMAGE",
            "ghcr.io/aureliolo/synthorg-sandbox:env-var",
        )
        config = DockerSandboxConfig(image="explicit:yaml")
        assert config.image == "explicit:yaml"

    def test_fallback_path_logs_debug(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from synthorg.tools.sandbox import docker_config as module

        monkeypatch.delenv("SYNTHORG_SANDBOX_IMAGE", raising=False)
        recorded: list[tuple[str, str, dict[str, object]]] = []

        def _capture(event: str, **kwargs: object) -> None:
            recorded.append(("debug", event, dict(kwargs)))

        # Direct setattr + try/finally delattr -- ``module.logger`` is
        # a ``BoundLoggerLazyProxy`` whose ``debug`` attribute is
        # served via ``__getattr__``. ``monkeypatch.setattr`` would
        # restore the snapshot it captures into ``__dict__`` at
        # teardown, permanently shadowing ``__getattr__`` for the
        # lifetime of the proxy and breaking ``capture_logs()`` for
        # later tests on the same xdist worker.
        proxy = module.logger
        proxy.debug = _capture  # type: ignore[method-assign,assignment]
        try:
            DockerSandboxConfig()
            assert any(
                level == "debug" and event == "config.env_var.fallback"
                for level, event, _ in recorded
            ), f"expected fallback debug log, got: {recorded}"
        finally:
            from contextlib import suppress

            with suppress(AttributeError):
                del proxy.debug


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

    def test_runtime_overrides(self) -> None:
        overrides = {"code_execution": "runsc", "terminal": "runsc"}
        config = DockerSandboxConfig(runtime_overrides=overrides)
        assert config.runtime_overrides == overrides

    def test_policy_none_by_default(self) -> None:
        config = DockerSandboxConfig()
        assert config.policy is None

    def test_policy_accepted(self) -> None:
        policy = SandboxPolicy(
            network=NetworkPolicy(mode="bridge"),
        )
        config = DockerSandboxConfig(policy=policy)
        assert config.policy is not None
        assert config.policy.network.mode == "bridge"


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

    def test_pids_limit_zero_rejected(self) -> None:
        with pytest.raises(ValidationError, match="pids_limit"):
            DockerSandboxConfig(pids_limit=0)

    def test_pids_limit_exceeds_max_rejected(self) -> None:
        with pytest.raises(ValidationError, match="pids_limit"):
            DockerSandboxConfig(pids_limit=1025)

    def test_sidecar_pids_limit_zero_rejected(self) -> None:
        with pytest.raises(ValidationError, match="sidecar_pids_limit"):
            DockerSandboxConfig(sidecar_pids_limit=0)

    def test_sidecar_pids_limit_exceeds_max_rejected(self) -> None:
        with pytest.raises(ValidationError, match="sidecar_pids_limit"):
            DockerSandboxConfig(sidecar_pids_limit=1025)

    def test_blank_tmpfs_size_rejected(self) -> None:
        with pytest.raises(ValidationError):
            DockerSandboxConfig(tmpfs_size="")

    def test_blank_sidecar_tmpfs_size_rejected(self) -> None:
        with pytest.raises(ValidationError):
            DockerSandboxConfig(sidecar_tmpfs_size="")

    @pytest.mark.parametrize(
        "value",
        ["064m", "-5m", "1t", "abc", "1.5g", "0"],
    )
    def test_malformed_tmpfs_size_rejected(self, value: str) -> None:
        with pytest.raises(ValidationError, match="tmpfs_size"):
            DockerSandboxConfig(tmpfs_size=value)

    @pytest.mark.parametrize(
        "value",
        ["064m", "-5m", "1t", "abc", "1.5g", "0"],
    )
    def test_malformed_sidecar_tmpfs_size_rejected(self, value: str) -> None:
        with pytest.raises(ValidationError, match="sidecar_tmpfs_size"):
            DockerSandboxConfig(sidecar_tmpfs_size=value)

    @pytest.mark.parametrize(
        "value",
        ["1", "1k", "64m", "8G", "1024K", "512M"],
    )
    def test_well_formed_tmpfs_size_accepted(self, value: str) -> None:
        config = DockerSandboxConfig(tmpfs_size=value, sidecar_tmpfs_size=value)
        assert config.tmpfs_size == value
        assert config.sidecar_tmpfs_size == value

    def test_custom_pids_and_tmpfs_accepted(self) -> None:
        # Operators tightening or widening the limits should work
        # within range without triggering unrelated validators.
        config = DockerSandboxConfig(
            pids_limit=128,
            tmpfs_size="256m",
            sidecar_pids_limit=64,
            sidecar_tmpfs_size="16m",
        )
        assert config.pids_limit == 128
        assert config.tmpfs_size == "256m"
        assert config.sidecar_pids_limit == 64
        assert config.sidecar_tmpfs_size == "16m"


class TestDockerSandboxConfigAllowedHostsValidation:
    """allowed_hosts entries must be host:port format."""

    def test_valid_host_port_accepted(self) -> None:
        config = DockerSandboxConfig(
            allowed_hosts=("example.com:443", "db.local:5432"),
        )
        assert config.allowed_hosts == ("example.com:443", "db.local:5432")

    def test_ip_host_port_accepted(self) -> None:
        config = DockerSandboxConfig(
            allowed_hosts=("192.168.1.1:8080",),
        )
        assert config.allowed_hosts == ("192.168.1.1:8080",)

    def test_missing_port_rejected(self) -> None:
        with pytest.raises(ValidationError, match="host:port"):
            DockerSandboxConfig(allowed_hosts=("example.com",))

    def test_empty_host_rejected(self) -> None:
        with pytest.raises(ValidationError, match=r"hostname or IP"):
            DockerSandboxConfig(allowed_hosts=(":443",))

    def test_wildcard_host_rejected(self) -> None:
        with pytest.raises(ValidationError, match=r"hostname or IP"):
            DockerSandboxConfig(allowed_hosts=("*:443",))

    def test_invalid_port_not_a_number_rejected(self) -> None:
        with pytest.raises(ValidationError, match="port"):
            DockerSandboxConfig(allowed_hosts=("example.com:abc",))

    def test_port_zero_rejected(self) -> None:
        with pytest.raises(ValidationError, match="port"):
            DockerSandboxConfig(allowed_hosts=("example.com:0",))

    def test_port_exceeds_max_rejected(self) -> None:
        with pytest.raises(ValidationError, match="port"):
            DockerSandboxConfig(allowed_hosts=("example.com:65536",))

    def test_port_at_max_accepted(self) -> None:
        config = DockerSandboxConfig(
            allowed_hosts=("example.com:65535",),
        )
        assert config.allowed_hosts == ("example.com:65535",)

    def test_port_one_accepted(self) -> None:
        config = DockerSandboxConfig(
            allowed_hosts=("example.com:1",),
        )
        assert config.allowed_hosts == ("example.com:1",)

    def test_multiple_colons_rejected(self) -> None:
        with pytest.raises(ValidationError, match="host:port"):
            DockerSandboxConfig(
                allowed_hosts=("host:80:extra",),
            )

    def test_empty_tuple_accepted(self) -> None:
        config = DockerSandboxConfig(allowed_hosts=())
        assert config.allowed_hosts == ()


class TestDockerSandboxConfigHostNetworkWithAllowedHosts:
    """network='host' with allowed_hosts is rejected."""

    def test_host_network_with_allowed_hosts_rejected(self) -> None:
        with pytest.raises(
            ValidationError,
            match=r"allowed_hosts.*host",
        ):
            DockerSandboxConfig(
                network="host",
                allowed_hosts=("example.com:443",),
            )

    def test_host_network_without_allowed_hosts_accepted(self) -> None:
        config = DockerSandboxConfig(network="host")
        assert config.network == "host"
        assert config.allowed_hosts == ()

    def test_bridge_network_with_allowed_hosts_accepted(self) -> None:
        config = DockerSandboxConfig(
            network="bridge",
            allowed_hosts=("example.com:443",),
        )
        assert config.network == "bridge"


class TestDockerSandboxConfigNetworkEnforcementSettings:
    """Tunable network enforcement settings have correct defaults."""

    def test_dns_allowed_default_true(self) -> None:
        config = DockerSandboxConfig()
        assert config.dns_allowed is True

    def test_loopback_allowed_default_true(self) -> None:
        config = DockerSandboxConfig()
        assert config.loopback_allowed is True

    def test_dns_allowed_can_be_disabled(self) -> None:
        config = DockerSandboxConfig(dns_allowed=False)
        assert config.dns_allowed is False

    def test_loopback_allowed_can_be_disabled(self) -> None:
        config = DockerSandboxConfig(loopback_allowed=False)
        assert config.loopback_allowed is False


class TestDockerSandboxConfigBeforeValidatorImmutability:
    """The network-preset before-validator must not mutate caller input."""

    def test_preset_resolution_does_not_mutate_input(self) -> None:
        import copy

        original = {
            "network": "bridge",
            "network_presets": ("python-dev",),
            "allowed_hosts": ("example.com:443",),
        }
        snapshot = copy.deepcopy(original)
        config = DockerSandboxConfig.model_validate(original)

        assert original == snapshot, "before-validator mutated caller input"
        assert "pypi.org:443" in config.allowed_hosts
        assert "example.com:443" in config.allowed_hosts

    def test_input_dict_remains_reusable(self) -> None:
        original = {
            "network": "bridge",
            "network_presets": ("git",),
        }
        first = DockerSandboxConfig.model_validate(original)
        second = DockerSandboxConfig.model_validate(original)

        assert first.allowed_hosts == second.allowed_hosts
        assert "github.com:443" in first.allowed_hosts
