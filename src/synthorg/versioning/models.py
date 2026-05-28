"""Generic versioned snapshot model.

A ``VersionSnapshot[T]`` is an immutable historical snapshot of any
frozen Pydantic model at a specific version number.  Version snapshots
are never updated after creation -- they store the exact state of the
entity when the snapshot was taken.

Usage::

    from synthorg.versioning.models import VersionSnapshot
    from synthorg.core.agent import AgentIdentity

    snapshot: VersionSnapshot[AgentIdentity] = VersionSnapshot(
        entity_id="agent-uuid",
        version=1,
        content_hash="sha256...",
        snapshot=identity,
        saved_by="system",
        saved_at=datetime.now(UTC),
    )
"""

import re
from datetime import timedelta

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from synthorg.core.types import NotBlankStr

_CONTENT_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


class VersionSnapshot[T: BaseModel](BaseModel):
    """Immutable snapshot of a versioned entity at a point in time.

    Type-parameterised so callers retain full static type information
    about the embedded ``snapshot`` field.  The ``entity_id`` is the
    string primary key of the entity being versioned (e.g., the string
    form of an agent's UUID).

    Attributes:
        entity_id: String primary key of the versioned entity.
        version: Monotonic version counter (1-indexed, per entity).
        content_hash: SHA-256 hex digest of the entity's canonical JSON
            serialization.  Enables content-addressable deduplication.
        snapshot: The full frozen entity model at this version.
        saved_by: Identifier of the actor that triggered the snapshot.
        saved_at: Timezone-aware timestamp when the snapshot was captured.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    entity_id: NotBlankStr = Field(description="String primary key of the entity")
    version: int = Field(ge=1, description="Monotonic version counter (1-indexed)")
    content_hash: NotBlankStr = Field(
        description="SHA-256 hex digest of canonical JSON"
    )
    snapshot: T = Field(description="Full frozen entity model at this version")
    saved_by: NotBlankStr = Field(description="Actor that triggered the snapshot")
    saved_at: AwareDatetime = Field(description="When the snapshot was captured")

    @field_validator("content_hash")
    @classmethod
    def _validate_content_hash(cls, v: str) -> str:
        if not _CONTENT_HASH_RE.match(v):
            msg = (
                "content_hash must be a 64-character lowercase hex string "
                f"(SHA-256 digest), got {v!r}"
            )
            raise ValueError(msg)
        return v

    @field_validator("saved_at")
    @classmethod
    def _validate_saved_at_utc(cls, v: AwareDatetime) -> AwareDatetime:
        if v.utcoffset() != timedelta(0):
            msg = f"saved_at must be UTC, got offset {v.utcoffset()}"
            raise ValueError(msg)
        return v


class RollbackWorkflowRequest(BaseModel):
    """Request body for rolling back a workflow to a previous version.

    Lives in the versioning domain layer so the rollback service can
    validate input without importing from ``synthorg.api.dto_workflow``.

    Attributes:
        target_version: Snapshot version number to restore content from
            (monotonic counter in the workflow_definition_versions table).
        expected_revision: Current definition revision for optimistic
            concurrency on the live workflow_definitions row.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    target_version: int = Field(
        ge=1,
        description="Snapshot version to rollback to",
    )
    expected_revision: int = Field(
        ge=1,
        description="Optimistic concurrency guard on the definition revision",
    )
