"""Pluggable git-backend subsystem.

Protocol + three strategies (embedded / local-path / external-remote)
+ factory + ``GitBackendType`` discriminator.  Switching the git
backend is a config change only; ``EMBEDDED`` is the safe default.
"""

from synthorg.core.project_enums import GitBackendType
from synthorg.engine.workspace.git_backend.config import (
    GitBackendConfig,
    GitBackendDeps,
)
from synthorg.engine.workspace.git_backend.embedded import EmbeddedGitBackend
from synthorg.engine.workspace.git_backend.external_remote import (
    ExternalRemoteGitBackend,
)
from synthorg.engine.workspace.git_backend.factory import build_git_backend
from synthorg.engine.workspace.git_backend.local_path import (
    LocalPathGitBackend,
)
from synthorg.engine.workspace.git_backend.protocol import (
    FetchResult,
    GitBackend,
    ProvisionResult,
    PushResult,
    ResolvedSource,
    SeedResult,
    SourceKind,
)

__all__ = [
    "EmbeddedGitBackend",
    "ExternalRemoteGitBackend",
    "FetchResult",
    "GitBackend",
    "GitBackendConfig",
    "GitBackendDeps",
    "GitBackendType",
    "LocalPathGitBackend",
    "ProvisionResult",
    "PushResult",
    "ResolvedSource",
    "SeedResult",
    "SourceKind",
    "build_git_backend",
]
