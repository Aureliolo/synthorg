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
from synthorg.core.workspace_sharing import ensure_shared_dir
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

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    project_workspace_service: ProjectWorkspaceService | None = None
    environment_service: EnvironmentService | None = None
    artifact_storage: ArtifactStorageBackend | None = None
    agent_workspace_root: Path | None = None


def agent_workspace_root_path(app_state: AppStateSliceMixin) -> Path:
    """Resolve the agent workspace root WITHOUT creating it.

    The read-only half of :func:`agent_workspace_root_of`, and the single owner
    of how the path is derived, so the two can never disagree about where the
    tree is. For callers that only look INSIDE it: creating the root is a
    wiring concern, and ``ensure_shared_dir`` is a mkdir plus a chmod, which on
    a dashboard-polled read path is filesystem work on the API's own event loop
    once per poll for a directory boot already made.

    Returns:
        The agent workspace root directory, which may not exist yet.
    """
    root = app_state.slice(WorkspaceStateSlice).agent_workspace_root
    if root is None:
        return Path(tempfile.gettempdir()) / _DEFAULT_WORKSPACE_TEMP_SUBDIR
    return root


def agent_workspace_root_of(app_state: AppStateSliceMixin) -> Path:
    """Resolve the agent filesystem workspace root.

    Returns the pinned root when one is set; otherwise a process-stable
    temp directory, so agent filesystem / sandbox tools always observe a
    valid absolute path even on injected / dev / empty-company boots.

    The directory is created on resolve: consumers bound file access to
    it via ``PathValidator``, which refuses a missing directory, and no
    deployment step pre-creates it on the data volume.

    Deliberately synchronous, not ``await asyncio.to_thread(...)``: every
    call site is a sync DI-style factory invoked during boot wiring or a
    rare admin-triggered reinit (never a per-request hot path), and
    ``mkdir(exist_ok=True)`` on an already-existing local directory is a
    sub-millisecond stat, not a stall worth an async cascade through
    those factories' call graphs.

    Returns:
        The agent workspace root directory (existing).
    """
    root = agent_workspace_root_path(app_state)
    ensure_shared_dir(root)
    return root
