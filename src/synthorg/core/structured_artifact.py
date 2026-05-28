"""Base model for structured single-consumption artifacts."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class StructuredArtifact(BaseModel):
    """Base for structured single-consumption artifacts.

    Subclasses:
    - HandoffArtifact (agent role transitions during handoff ceremonies)
    - EvidencePackage (HITL approval payload)
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    created_at: datetime = Field(description="Artifact creation timestamp")
