"""Models for the reports service.

Reports are snapshots of the analytics view frozen at a point in time
plus a short template descriptor so operators can generate, share, and
audit what was rendered.  The in-memory store keeps the full shape so
future durable impls can persist identical payloads.
"""

from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr  # noqa: TC001


class ReportStatus(StrEnum):
    """Lifecycle of a report artifact."""

    PENDING = "pending"
    READY = "ready"
    FAILED = "failed"


class Report(BaseModel):
    """A rendered report.

    Attributes:
        id: Stable report identifier.
        template: Template the report was rendered from.
        title: Human-readable title.
        status: Lifecycle status.
        generated_at: When the report was generated.
        author_id: Agent or operator that generated it.
        content: JSON-serialisable rendered content (shape is
            template-dependent).
        options: Options passed at generation time (preserved for
            auditability).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    template: NotBlankStr
    title: NotBlankStr
    status: ReportStatus = ReportStatus.READY
    generated_at: AwareDatetime = Field(
        default_factory=lambda: datetime.now(UTC),
    )
    author_id: NotBlankStr
    content: dict[str, object] = Field(default_factory=dict)
    options: dict[str, str] = Field(default_factory=dict)
