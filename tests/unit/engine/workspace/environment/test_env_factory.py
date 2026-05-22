"""Unit tests for the environment-strategy factory."""

import pytest
from tests._shared import FakeClock

from synthorg.core.enums import EnvironmentType
from synthorg.engine.workspace.environment.config import (
    EnvironmentConfig,
    EnvironmentDeps,
)
from synthorg.engine.workspace.environment.devcontainer import (
    DevcontainerEnvironmentStrategy,
)
from synthorg.engine.workspace.environment.factory import build_environment_strategy
from synthorg.engine.workspace.environment.manifest import ManifestEnvironmentStrategy
from synthorg.engine.workspace.environment.nix import NixEnvironmentStrategy
from synthorg.engine.workspace.environment.protocol import EnvironmentStrategy

pytestmark = pytest.mark.unit


def _deps() -> EnvironmentDeps:
    return EnvironmentDeps(clock=FakeClock())


class TestEnvironmentFactory:
    def test_builds_manifest_default(self) -> None:
        strategy = build_environment_strategy(EnvironmentConfig(), _deps())
        assert isinstance(strategy, ManifestEnvironmentStrategy)
        assert isinstance(strategy, EnvironmentStrategy)
        assert strategy.kind() is EnvironmentType.MANIFEST

    def test_builds_nix(self) -> None:
        strategy = build_environment_strategy(
            EnvironmentConfig(kind=EnvironmentType.NIX), _deps()
        )
        assert isinstance(strategy, NixEnvironmentStrategy)
        assert strategy.kind() is EnvironmentType.NIX

    def test_builds_devcontainer_with_default_builder(self) -> None:
        strategy = build_environment_strategy(
            EnvironmentConfig(kind=EnvironmentType.DEVCONTAINER), _deps()
        )
        assert isinstance(strategy, DevcontainerEnvironmentStrategy)
        assert strategy.kind() is EnvironmentType.DEVCONTAINER
