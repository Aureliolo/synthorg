"""Git-backend factory.

Maps :class:`~synthorg.core.project_enums.GitBackendType` to a concrete
:class:`GitBackend` via the ``StrEnum``-keyed
:class:`~synthorg.core.registry.StrategyRegistry`.  A kind whose
required dependency is absent raises :class:`GitBackendConfigError` at
construction (fail fast), exactly like the autonomy strategy factory.
"""

from typing import TYPE_CHECKING

from synthorg.core.project_enums import GitBackendType
from synthorg.core.registry import StrategyRegistry
from synthorg.engine.errors import GitBackendConfigError
from synthorg.engine.workspace.git_backend.embedded import EmbeddedGitBackend
from synthorg.engine.workspace.git_backend.external_remote import (
    ExternalRemoteGitBackend,
)
from synthorg.engine.workspace.git_backend.local_path import (
    LocalPathGitBackend,
)

if TYPE_CHECKING:
    from synthorg.engine.workspace.git_backend.config import (
        GitBackendConfig,
        GitBackendDeps,
    )
    from synthorg.engine.workspace.git_backend.protocol import GitBackend


def _build_embedded(
    config: GitBackendConfig,
    deps: GitBackendDeps,
) -> GitBackend:
    if deps.workspace_base_root is None:
        msg = (
            "EMBEDDED git backend requires a 'workspace_base_root' "
            "dependency but none was provided"
        )
        raise GitBackendConfigError(msg)
    return EmbeddedGitBackend(
        base_root=deps.workspace_base_root,
        embedded_subdir=config.embedded_subdir,
        cmd_timeout=config.git_cmd_timeout_seconds,
        clock=deps.clock,
    )


def _build_local_path(
    config: GitBackendConfig,
    deps: GitBackendDeps,
) -> GitBackend:
    if not config.local_repo_path:
        msg = "LOCAL_PATH git backend requires 'local_repo_path' in config"
        raise GitBackendConfigError(msg)
    return LocalPathGitBackend(
        local_repo_path=config.local_repo_path,
        cmd_timeout=config.git_cmd_timeout_seconds,
        clock=deps.clock,
    )


def _build_external_remote(
    config: GitBackendConfig,
    deps: GitBackendDeps,
) -> GitBackend:
    if not config.remote_connection_name:
        msg = "EXTERNAL_REMOTE git backend requires 'remote_connection_name' in config"
        raise GitBackendConfigError(msg)
    if deps.connection_catalog is None:
        msg = (
            "EXTERNAL_REMOTE git backend requires a 'connection_catalog' "
            "dependency but none was provided"
        )
        raise GitBackendConfigError(msg)
    return ExternalRemoteGitBackend(
        connection_name=config.remote_connection_name,
        connection_catalog=deps.connection_catalog,
        cmd_timeout=config.git_cmd_timeout_seconds,
        resilience=config.resilience,
        forge_provisioning_enabled=config.forge_provisioning_enabled,
        forge_repo_private=config.forge_repo_private,
        forge_api_timeout=config.forge_api_timeout_seconds,
        clock=deps.clock,
    )


_REGISTRY: StrategyRegistry[GitBackend] = StrategyRegistry(
    {
        GitBackendType.EMBEDDED: _build_embedded,
        GitBackendType.LOCAL_PATH: _build_local_path,
        GitBackendType.EXTERNAL_REMOTE: _build_external_remote,
    },
    kind="git_backend",
)


def build_git_backend(
    config: GitBackendConfig,
    deps: GitBackendDeps,
) -> GitBackend:
    """Build the configured :class:`GitBackend`.

    Args:
        config: The strategy discriminator + per-impl tuning.
        deps: Runtime collaborators (base root, catalog, secret, clock).

    Returns:
        A strategy satisfying the ``GitBackend`` protocol.

    Raises:
        StrategyFactoryNotFoundError: Unknown ``config.kind``.
        GitBackendConfigError: A strategy is missing a required
            dependency or addressing field.
    """
    return _REGISTRY.build(config.kind, config, deps)
