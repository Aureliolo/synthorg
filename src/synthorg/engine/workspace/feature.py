# module-kind: feature
"""Workspace feature manifest.

Declares the workspace feature's surface: the
:class:`WorkspaceStateSlice` (project workspace + environment services,
artifact storage, agent workspace root) and the artifact REST controller
mounted by the composition root. The workspace domain has no dedicated
settings namespace.
"""

from synthorg._core.features import FeatureManifest, FeatureModule
from synthorg.api.controllers.artifacts import ArtifactController
from synthorg.engine.workspace._construction import wire_construction
from synthorg.engine.workspace.state import WorkspaceStateSlice

FEATURE: FeatureModule = FeatureManifest(
    name="workspace",
    settings_namespace=None,
    state_slice=WorkspaceStateSlice,
    controllers=(ArtifactController,),
    mcp_handlers=(),
    lifecycle_hooks=(),
    construction_wirer=wire_construction,
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
