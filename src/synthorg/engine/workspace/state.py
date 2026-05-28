"""Workspace feature state slice.

Holds the per-project workspace provisioning service, the reproducible
environment service, the artifact storage backend, and the agent
filesystem workspace root. All are wired at boot behind the provider
switch; ``None`` for empty-company / dev runs. Readers guard
accordingly (the workspace root falls back to a process-stable temp dir
at its own accessor).
"""

import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Final

from pydantic import ConfigDict

from synthorg._core.features import BaseFeatureStateSlice
from synthorg.engine.workspace.environment.service import (
    EnvironmentService,
)
from synthorg.engine.workspace.project_workspace_service import (
    ProjectWorkspaceService,
)
from synthorg.persistence.artifact_storage import (
    ArtifactStorageBackend,
)

if TYPE_CHECKING:
    from synthorg.api.state_slices import AppStateSliceMixin

_DEFAULT_WORKSPACE_TEMP_SUBDIR: Final[str] = "synthorg-agent-workspaces"


class WorkspaceStateSlice(BaseFeatureStateSlice):
    """Application-state slice owned by the workspace feature."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    project_workspace_service: ProjectWorkspaceService | None = None
    environment_service: EnvironmentService | None = None
    artifact_storage: ArtifactStorageBackend | None = None
    agent_workspace_root: Path | None = None


def agent_workspace_root_of(app_state: AppStateSliceMixin) -> Path:
    """Resolve the agent filesystem workspace root.

    Returns the pinned root when one is set; otherwise a process-stable
    temp directory, so agent filesystem / sandbox tools always observe a
    valid absolute path even on injected / dev / empty-company boots.

    Returns:
        The agent workspace root directory.
    """
    root = app_state.slice(WorkspaceStateSlice).agent_workspace_root
    if root is not None:
        return root
    return Path(tempfile.gettempdir()) / _DEFAULT_WORKSPACE_TEMP_SUBDIR
