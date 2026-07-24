"""Durable marker that an approval's resume dispatch may be unfinished.

Deciding an approval is two writes that cannot be made one: the decision
lands on the :class:`~synthorg.core.approval.ApprovalItem` (moving it off
PENDING), and only then does the resume flow wake the parked task. A
process death between them strands the task forever -- nothing is PENDING
any more, so neither a redelivered chat event nor the dashboard can act on
it, and no sweep would notice.

An intent row is written *before* the decision write and deleted *after*
the resume dispatch returns, so its presence at startup means "this
approval's resume might not have run". It deliberately carries no copy of
the decision: the ``ApprovalItem`` is the system of record, so the drain
reads the outcome from there and the two can never disagree.
"""

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr


class ResumeIntent(BaseModel):
    """An in-flight approval decision whose resume may not have dispatched.

    Attributes:
        approval_id: The approval being decided. Also the primary key:
            one approval has at most one in-flight resume, so a retry
            (or a losing concurrent decider) overwrites rather than
            accumulating duplicate rows.
        recorded_at: When the intent was written, immediately before the
            decision write.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    approval_id: NotBlankStr = Field(description="Approval item identifier")
    recorded_at: AwareDatetime = Field(description="When the intent was recorded")


__all__ = ["ResumeIntent"]
