"""Persistent per-project reproducible-environment domain model.

A :class:`ProjectEnvironment` is the 1:1 mapping between a
:class:`~synthorg.core.project.Project` and the reproducible dev
environment declared in its workspace.  The row records which declaration
format provisioned the environment, a content hash of the declaration so
a re-provision is skipped when nothing changed, and (for the devcontainer
image path) the built image reference.  The declaration files themselves
live in the git-backed workspace; this row is the durable provisioning
cache across sessions.
"""

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from synthorg.core.enums import EnvironmentType  # noqa: TC001
from synthorg.core.types import NotBlankStr  # noqa: TC001


class ProjectEnvironment(BaseModel):
    """Persistent reproducible environment bound to a single project.

    Attributes:
        project_id: Owning project identifier (primary key, 1:1 with
            ``Project.id``).
        environment_type: Which declaration format provisioned the
            environment; a config switch detects a mismatch against the
            live config.
        declaration_hash: Stable content hash of the declaration files
            (and listed lockfiles) for this format.  A persisted row whose
            hash matches the live declaration short-circuits re-provision.
        image_ref: Built Docker image reference for the ``DEVCONTAINER``
            image-build path; ``None`` for the bootstrap (manifest / nix)
            paths.
        provisioned_at: First provisioning timestamp (timezone-aware, UTC).
        updated_at: Last re-provision timestamp (timezone-aware, UTC).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    project_id: NotBlankStr = Field(description="Owning project identifier (PK)")
    environment_type: EnvironmentType = Field(
        description="Declaration format that provisioned this environment",
    )
    declaration_hash: NotBlankStr = Field(
        description="Content hash of the declaration files",
    )
    image_ref: NotBlankStr | None = Field(
        default=None,
        description="Built image reference (devcontainer image path only)",
    )
    provisioned_at: AwareDatetime = Field(
        description="First provisioning timestamp (UTC)",
    )
    updated_at: AwareDatetime = Field(description="Last re-provision timestamp (UTC)")
