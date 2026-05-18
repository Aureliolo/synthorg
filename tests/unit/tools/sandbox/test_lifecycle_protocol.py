"""Tests for sandbox lifecycle protocol types."""

import pytest

from synthorg.tools.sandbox.lifecycle.config import SandboxLifecycleConfig
from synthorg.tools.sandbox.lifecycle.per_agent import PerAgentStrategy
from synthorg.tools.sandbox.lifecycle.per_call import PerCallStrategy
from synthorg.tools.sandbox.lifecycle.per_task import PerTaskStrategy
from synthorg.tools.sandbox.lifecycle.protocol import (
    ContainerHandle,
    SandboxLifecycleStrategy,
)

pytestmark = pytest.mark.unit


class TestContainerHandle:
    """ContainerHandle construction and validation."""

    def test_valid_handle(self) -> None:
        handle = ContainerHandle(container_id="abc123")
        assert handle.container_id == "abc123"
        assert handle.sidecar_id is None
        assert handle.network_mode == "none"

    def test_with_sidecar(self) -> None:
        handle = ContainerHandle(
            container_id="sandbox-1",
            sidecar_id="sidecar-1",
            network_mode="container:sidecar-1",
        )
        assert handle.sidecar_id == "sidecar-1"
        assert handle.network_mode == "container:sidecar-1"

    def test_frozen(self) -> None:
        handle = ContainerHandle(container_id="abc123")
        with pytest.raises(AttributeError):
            handle.container_id = "other"  # type: ignore[misc]

    def test_empty_container_id_rejected(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            ContainerHandle(container_id="")

    def test_whitespace_container_id_rejected(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            ContainerHandle(container_id="   ")

    def test_slots(self) -> None:
        handle = ContainerHandle(container_id="abc123")
        assert not hasattr(handle, "__dict__")


class TestReusesContainer:
    """The ``reuses_container`` discriminator on each strategy."""

    def test_per_call_does_not_reuse(self) -> None:
        assert PerCallStrategy().reuses_container is False

    def test_per_task_reuses(self) -> None:
        assert PerTaskStrategy().reuses_container is True

    def test_per_agent_reuses(self) -> None:
        strategy = PerAgentStrategy(SandboxLifecycleConfig(strategy="per-agent"))
        assert strategy.reuses_container is True

    @pytest.mark.parametrize(
        "strategy",
        [
            PerCallStrategy(),
            PerTaskStrategy(),
            PerAgentStrategy(SandboxLifecycleConfig(strategy="per-agent")),
        ],
    )
    def test_all_satisfy_runtime_checkable_protocol(
        self,
        strategy: SandboxLifecycleStrategy,
    ) -> None:
        # ``reuses_container`` is part of the runtime-checkable Protocol,
        # so isinstance must still hold for every shipped strategy.
        assert isinstance(strategy, SandboxLifecycleStrategy)
        assert isinstance(strategy.reuses_container, bool)
