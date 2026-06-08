"""Environment-strategy factory.

Maps :class:`~synthorg.core.project_enums.EnvironmentType` to a concrete
:class:`EnvironmentStrategy` via the ``StrEnum``-keyed
:class:`~synthorg.core.registry.StrategyRegistry`, mirroring
``build_git_backend``.  The ``MANIFEST`` default needs no external
dependency; ``DEVCONTAINER`` falls back to the default
:class:`SubprocessImageBuilder` when no builder is injected.
"""

from typing import TYPE_CHECKING

from synthorg.core.project_enums import EnvironmentType
from synthorg.core.registry import StrategyRegistry
from synthorg.engine.workspace.environment.devcontainer import (
    DevcontainerEnvironmentStrategy,
)
from synthorg.engine.workspace.environment.image_builder import SubprocessImageBuilder
from synthorg.engine.workspace.environment.manifest import ManifestEnvironmentStrategy
from synthorg.engine.workspace.environment.nix import NixEnvironmentStrategy

if TYPE_CHECKING:
    from synthorg.engine.workspace.environment.config import (
        EnvironmentConfig,
        EnvironmentDeps,
    )
    from synthorg.engine.workspace.environment.protocol import EnvironmentStrategy


def _build_manifest(
    config: EnvironmentConfig,
    deps: EnvironmentDeps,
) -> EnvironmentStrategy:
    return ManifestEnvironmentStrategy(
        manifest_filename=config.manifest_filename,
        provision_timeout_seconds=config.provision_timeout_seconds,
        clock=deps.clock,
    )


def _build_nix(
    config: EnvironmentConfig,
    deps: EnvironmentDeps,
) -> EnvironmentStrategy:
    return NixEnvironmentStrategy(
        provision_timeout_seconds=config.provision_timeout_seconds,
        clock=deps.clock,
    )


def _build_devcontainer(
    config: EnvironmentConfig,
    deps: EnvironmentDeps,
) -> EnvironmentStrategy:
    image_builder = (
        deps.image_builder
        if deps.image_builder is not None
        else SubprocessImageBuilder()
    )
    return DevcontainerEnvironmentStrategy(
        image_builder=image_builder,
        docker_build_timeout_seconds=config.docker_build_timeout_seconds,
        build_max_attempts=config.docker_build_max_attempts,
        build_retry_base_seconds=config.docker_build_retry_base_seconds,
        build_retry_cap_seconds=config.docker_build_retry_cap_seconds,
        clock=deps.clock,
    )


_REGISTRY: StrategyRegistry[EnvironmentStrategy] = StrategyRegistry(
    {
        EnvironmentType.MANIFEST: _build_manifest,
        EnvironmentType.NIX: _build_nix,
        EnvironmentType.DEVCONTAINER: _build_devcontainer,
    },
    kind="environment_strategy",
)


def build_environment_strategy(
    config: EnvironmentConfig,
    deps: EnvironmentDeps,
) -> EnvironmentStrategy:
    """Build the configured :class:`EnvironmentStrategy`.

    Args:
        config: The strategy discriminator + per-impl tuning.
        deps: Runtime collaborators (image builder, clock).

    Returns:
        A strategy satisfying the ``EnvironmentStrategy`` protocol.

    Raises:
        StrategyFactoryNotFoundError: Unknown ``config.kind``.
    """
    return _REGISTRY.build(config.kind, config, deps)


__all__ = ["build_environment_strategy"]
