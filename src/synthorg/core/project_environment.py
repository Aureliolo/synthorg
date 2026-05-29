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

from typing import Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from synthorg.core.enums import EnvironmentType
from synthorg.core.types import NotBlankStr


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
        description="Content hash of the declaration files (SHA-256 hex)",
        pattern=r"^[a-f0-9]{64}$",
    )
    image_ref: NotBlankStr | None = Field(
        default=None,
        description="Built image reference (devcontainer image path only)",
    )
    provisioned_at: AwareDatetime = Field(
        description="First provisioning timestamp (UTC)",
    )
    updated_at: AwareDatetime = Field(description="Last re-provision timestamp (UTC)")

    @model_validator(mode="after")
    def _check_invariants(self) -> Self:
        """Enforce the image-ref / type and timestamp-ordering invariants.

        Only the ``DEVCONTAINER`` image-build path carries an
        ``image_ref``; the bootstrap (manifest / nix) paths must not. A
        re-provision never predates the first provision.

        Returns:
            The validated instance (Pydantic ``model_validator`` contract).

        Raises:
            ValueError: If a ``DEVCONTAINER`` environment lacks an
                ``image_ref``, a non-devcontainer environment sets one,
                or ``updated_at`` predates ``created_at``.
        """
        if self.environment_type == EnvironmentType.DEVCONTAINER:
            if self.image_ref is None:
                msg = "DEVCONTAINER environment requires an image_ref"
                raise ValueError(msg)
        elif self.image_ref is not None:
            msg = f"{self.environment_type.value} environment must not set image_ref"
            raise ValueError(msg)
        if self.updated_at < self.provisioned_at:
            msg = "updated_at must not predate provisioned_at"
            raise ValueError(msg)
        return self
