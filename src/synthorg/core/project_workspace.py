"""Persistent per-project workspace domain model.

A :class:`ProjectWorkspace` is the 1:1 mapping between a
:class:`~synthorg.core.project.Project` and the persistent, git-backed
working tree that survives across agents, tasks and sessions.  The row
records where the workspace lives on the persistent volume and which git
backend provisioned it, so a session restart re-locates the same
directory without re-deriving environment precedence and a configured
backend switch can be detected against the persisted kind.
"""

from typing import Final

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from synthorg.core.enums import GitBackendType
from synthorg.core.types import NotBlankStr

_DEFAULT_BRANCH: Final[str] = "main"


class ProjectWorkspace(BaseModel):
    """Persistent git-backed workspace bound to a single project.

    Attributes:
        project_id: Owning project identifier (primary key, 1:1 with
            ``Project.id``).
        workspace_path: Absolute on-volume path of the project working
            tree (``<base>/projects/<project_id>``).  Persisted so a
            restart re-locates the same directory deterministically.
        git_backend_kind: Which backend provisioned the repository; lets
            a config switch detect a mismatch against the live config.
        remote_ref: External remote URL or connection-catalog name for
            the ``EXTERNAL_REMOTE`` backend; ``None`` for embedded/local.
        default_branch: Default branch the backend provisions and the
            merge/push queue targets.
        created_at: Provisioning timestamp (timezone-aware, UTC).
        updated_at: Last mutation timestamp (timezone-aware, UTC).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    project_id: NotBlankStr = Field(description="Owning project identifier (PK)")
    workspace_path: NotBlankStr = Field(
        description="Absolute on-volume path of the project working tree",
    )
    git_backend_kind: GitBackendType = Field(
        description="Backend that provisioned this workspace",
    )
    remote_ref: NotBlankStr | None = Field(
        default=None,
        description="External remote URL / connection name (external backend)",
    )
    default_branch: NotBlankStr = Field(
        default=NotBlankStr(_DEFAULT_BRANCH),
        description="Default branch the backend provisions",
    )
    created_at: AwareDatetime = Field(description="Provisioning timestamp (UTC)")
    updated_at: AwareDatetime = Field(description="Last mutation timestamp (UTC)")
