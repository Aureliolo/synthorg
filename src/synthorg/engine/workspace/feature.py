# module-kind: feature
"""Workspace feature manifest.

Declares the workspace feature's surface: the
:class:`WorkspaceStateSlice` (project workspace + environment services,
artifact storage, agent workspace root). The workspace domain has no
dedicated settings namespace. Wiring stays hand-coded at boot; this
manifest is declarative and feeds the navigation index.
"""

from synthorg._core.features import FeatureManifest, FeatureModule
from synthorg.engine.workspace.state import WorkspaceStateSlice

FEATURE: FeatureModule = FeatureManifest(
    name="workspace",
    settings_namespace=None,
    state_slice=WorkspaceStateSlice,
    controllers=(),
    mcp_handlers=(),
    lifecycle_hooks=(),
    ghost_wired_symbols=(
        "ProjectWorkspaceService",
        "PushQueueCoordinator",
        "build_git_backend",
        "EmbeddedGitBackend",
        "LocalPathGitBackend",
        "ExternalRemoteGitBackend",
    ),
    depends_on=(),
)
