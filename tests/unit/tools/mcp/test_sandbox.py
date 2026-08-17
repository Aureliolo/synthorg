"""Tests for the container-isolation policy of stdio MCP servers."""

import pytest
import structlog

from synthorg.observability.events.mcp import MCP_SANDBOX_NETWORK_UNSAFE
from synthorg.tools.mcp.sandbox import MCPSandboxConfig
from synthorg.tools.sandbox._image_resolution import (
    get_resolved_sandbox_image,
    set_resolved_sandbox_image,
)

pytestmark = pytest.mark.unit


class TestSandboxConfig:
    def test_sandbox_on_by_default(self) -> None:
        assert MCPSandboxConfig().enabled is True

    def test_network_rejects_unknown_mode(self) -> None:
        with pytest.raises(ValueError, match="network"):
            MCPSandboxConfig(network="wide-open")  # type: ignore[arg-type]

    @pytest.mark.parametrize("mode", ["bridge", "none", "host"])
    def test_network_accepts_known_modes(self, mode: str) -> None:
        assert MCPSandboxConfig(network=mode).network == mode  # type: ignore[arg-type]

    def test_host_network_warns(self) -> None:
        """``host`` defeats isolation, so selecting it is surfaced loudly."""
        with structlog.testing.capture_logs() as cap:
            MCPSandboxConfig(network="host")
        events = [e for e in cap if e.get("event") == MCP_SANDBOX_NETWORK_UNSAFE]
        assert events
        assert events[0].get("log_level") == "warning"

    def test_bridge_network_does_not_warn(self) -> None:
        with structlog.testing.capture_logs() as cap:
            MCPSandboxConfig(network="bridge")
        assert not [e for e in cap if e.get("event") == MCP_SANDBOX_NETWORK_UNSAFE]


class TestTheRuntimeImageIsTheSandboxImage:
    """There is one image that runs untrusted code, so there is one answer.

    A separate MCP image setting is a second answer to a question an operator
    already answered by hardening and verifying the sandbox image, and the
    knob shipped defaulting to a third-party image the deployment had never
    pulled, let alone verified.
    """

    def test_image_follows_the_resolved_sandbox_image(self) -> None:
        try:
            set_resolved_sandbox_image("registry.example/verified-sandbox@sha256:abc")
            assert (
                MCPSandboxConfig().image
                == "registry.example/verified-sandbox@sha256:abc"
            )
        finally:
            set_resolved_sandbox_image(None)

    def test_unresolved_falls_back_to_the_release_pinned_image(self) -> None:
        set_resolved_sandbox_image(None)
        assert MCPSandboxConfig().image == get_resolved_sandbox_image()


class TestDeploymentAttribution:
    def test_unset_by_default_so_nothing_claims_a_foreign_container(self) -> None:
        assert MCPSandboxConfig().deployment_id is None

    def test_carries_the_id_it_is_given(self) -> None:
        assert MCPSandboxConfig(deployment_id="abc123").deployment_id == "abc123"
