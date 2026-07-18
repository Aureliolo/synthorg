# module-kind: code
"""Per-item plan discussion: an immutable comment on one plan item.

A ``PlanItemComment`` is one message in the discussion thread hanging off a plan
item, written independently of the version-guarded plan row so a comment never
conflicts with a concurrent rework. Comments are immutable once written (an
append-only thread), keyed by ``(plan_id, item_id)``. A comment carries who
wrote it (an operator or a responsible agent) and, when it answers an earlier
message, the id of the comment it replies to: the item *is* the thread, so a
reply links its parent flat rather than forming a nested tree.
"""

from typing import Literal, Self
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from synthorg.core.types import NotBlankStr

#: Discriminates who authored a comment: a human operator or a responding agent.
CommentAuthorKind = Literal["human", "agent"]


class PlanItemComment(BaseModel):
    """One immutable comment on a plan item's discussion thread.

    Attributes:
        id: Comment identifier (entity primary key).
        plan_id: The plan the commented item belongs to.
        item_id: The plan item this comment is attached to.
        author: Who wrote the comment (username or agent display name).
        author_kind: Whether a human or an agent wrote it.
        author_agent_id: The responding agent's id when ``author_kind`` is
            ``"agent"``; ``None`` for a human comment.
        reply_to_id: The comment this one answers, when it is a reply; ``None``
            for a top-level comment on the item.
        body: The comment text.
        created_at: When the comment was written (tz-aware UTC).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    id: UUID = Field(default_factory=uuid4, description="Comment identifier")
    plan_id: NotBlankStr = Field(description="Plan the commented item belongs to")
    item_id: NotBlankStr = Field(description="Plan item this comment is attached to")
    author: NotBlankStr = Field(description="Who wrote the comment")
    author_kind: CommentAuthorKind = Field(
        default="human", description="Whether a human or an agent wrote it"
    )
    author_agent_id: NotBlankStr | None = Field(
        default=None, description="The responding agent's id for an agent comment"
    )
    reply_to_id: UUID | None = Field(
        default=None, description="The comment this one answers, when a reply"
    )
    body: NotBlankStr = Field(description="The comment text")
    created_at: AwareDatetime = Field(description="When written (tz-aware UTC)")

    @model_validator(mode="after")
    def _validate_authorship(self) -> Self:
        """Tie ``author_agent_id`` to an agent author, both directions.

        An agent comment must name the agent that wrote it (so the reply is
        attributable), and a human comment must not carry one (a leftover
        agent id would misattribute an operator's message).

        Returns:
            ``self`` when ``author_agent_id`` is present iff the author is an
            agent.

        Raises:
            ValueError: When an agent comment lacks an id, or a human comment
                carries one.
        """
        has_agent_id = self.author_agent_id is not None
        is_agent = self.author_kind == "agent"
        if is_agent and not has_agent_id:
            msg = "an agent comment must carry an author_agent_id"
            raise ValueError(msg)
        if has_agent_id and not is_agent:
            msg = "author_agent_id is only valid for an agent comment"
            raise ValueError(msg)
        return self
