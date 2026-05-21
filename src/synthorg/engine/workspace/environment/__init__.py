"""Pluggable per-project reproducible-environment subsystem.

A project workspace declares its dev environment in committed files (a
bootstrap manifest, a ``devcontainer.json``, or a ``flake.nix``).  The
agent sandbox provisions from the same declaration a human, CI, or a
fresh clone gets, so environments are reproducible across all three.
Mirrors the ``git_backend`` pluggable pattern: protocol + strategies +
factory + frozen config discriminator, with a safe default (``MANIFEST``).
"""

from synthorg.engine.workspace.environment.committer import (
    GitWorkspaceCommitter,
    WorkspaceCommitter,
)
from synthorg.engine.workspace.environment.config import (
    EnvironmentConfig,
    EnvironmentDeps,
)
from synthorg.engine.workspace.environment.devcontainer import (
    DevcontainerEnvironmentStrategy,
)
from synthorg.engine.workspace.environment.factory import build_environment_strategy
from synthorg.engine.workspace.environment.hash_cache import (
    ProvisionedEnvironmentCache,
)
from synthorg.engine.workspace.environment.image_builder import (
    BuildOutcome,
    ImageBuilder,
    SubprocessImageBuilder,
)
from synthorg.engine.workspace.environment.manifest import (
    BOOTSTRAP_SCRIPT_NAME,
    EnvironmentManifest,
    ManifestEnvironmentStrategy,
)
from synthorg.engine.workspace.environment.nix import NixEnvironmentStrategy
from synthorg.engine.workspace.environment.protocol import (
    CommandOutcome,
    EnvironmentCommandRunner,
    EnvironmentStrategy,
    ProvisionedEnvironment,
    ScaffoldResult,
)
from synthorg.engine.workspace.environment.service import EnvironmentService

__all__ = [
    "BOOTSTRAP_SCRIPT_NAME",
    "BuildOutcome",
    "CommandOutcome",
    "DevcontainerEnvironmentStrategy",
    "EnvironmentCommandRunner",
    "EnvironmentConfig",
    "EnvironmentDeps",
    "EnvironmentManifest",
    "EnvironmentService",
    "EnvironmentStrategy",
    "GitWorkspaceCommitter",
    "ImageBuilder",
    "ManifestEnvironmentStrategy",
    "NixEnvironmentStrategy",
    "ProvisionedEnvironment",
    "ProvisionedEnvironmentCache",
    "ScaffoldResult",
    "SubprocessImageBuilder",
    "WorkspaceCommitter",
    "build_environment_strategy",
]
