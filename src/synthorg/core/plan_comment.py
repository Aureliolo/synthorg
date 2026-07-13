# module-kind: code
"""Per-item plan discussion: an immutable comment on one plan item.

A ``PlanItemComment`` is one message in the discussion thread hanging off a plan
item, written independently of the version-guarded plan row so a comment never
conflicts with a concurrent rework. Comments are immutable once written (an
append-only thread), keyed by ``(plan_id, item_id)``.
"""

from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr


class PlanItemComment(BaseModel):
    """One immutable comment on a plan item's discussion thread.

    Attributes:
        id: Comment identifier (entity primary key).
        plan_id: The plan the commented item belongs to.
        item_id: The plan item this comment is attached to.
        author: Who wrote the comment (username or agent id).
        body: The comment text.
        created_at: When the comment was written (tz-aware UTC).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    id: UUID = Field(default_factory=uuid4, description="Comment identifier")
    plan_id: NotBlankStr = Field(description="Plan the commented item belongs to")
    item_id: NotBlankStr = Field(description="Plan item this comment is attached to")
    author: NotBlankStr = Field(description="Who wrote the comment")
    body: NotBlankStr = Field(description="The comment text")
    created_at: AwareDatetime = Field(description="When written (tz-aware UTC)")
