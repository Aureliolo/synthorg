# module-kind: code
"""The durable record of what moved an initiative, and who moved it.

A plan reaching COMPLETED left no actor record anywhere: the claim that
only the evaluate stage can write it was provable from a container log
and nowhere else, and a log is not evidence anyone can query. Every
audited status write on a plan or a project appends a row here, so the
question "how did this initiative get to where it is" has an answer that
outlives the process that produced it.
"""

from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from synthorg.core.types import NotBlankStr


class LifecycleEntityKind(StrEnum):
    """Which kind of entity a transition belongs to."""

    PLAN = "plan"
    PROJECT = "project"


class LifecycleTransition(BaseModel):
    """One recorded status change on a plan or a project.

    Attributes:
        id: Row identifier.
        entity_kind: Whether the row records a plan or a project move.
        entity_id: The plan / project that moved.
        from_status: The status it left, or ``None`` for the first
            observed status of an entity.
        to_status: The status it reached.
        requested_by: Who asked for the move. ``None`` means the system
            moved it on its own schedule (a reconciler pass, a rollup),
            which is itself the answer to "who".
        reason: Why, when the writer recorded one.
        entity_version: The entity's version after the move, so a reader
            can line a row up against the revision it produced.
        occurred_at: When the move landed (tz-aware UTC).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    id: UUID = Field(default_factory=uuid4, description="Row identifier")
    entity_kind: LifecycleEntityKind = Field(description="Plan or project")
    entity_id: NotBlankStr = Field(description="The entity that moved")
    from_status: NotBlankStr | None = Field(
        default=None,
        description="The status left, or None for a first observed status",
    )
    to_status: NotBlankStr = Field(description="The status reached")
    requested_by: NotBlankStr | None = Field(
        default=None,
        description="Who asked; None means the system moved it itself",
    )
    reason: NotBlankStr | None = Field(
        default=None,
        description="Why, when the writer recorded one",
    )
    entity_version: int = Field(
        ge=0,
        description="The entity's version after the move",
    )
    occurred_at: AwareDatetime = Field(description="When the move landed (UTC)")

    @field_validator("occurred_at")
    @classmethod
    def _normalise_occurred_at(cls, value: datetime) -> datetime:
        """Convert an aware timestamp to UTC.

        ``AwareDatetime`` only rejects a naive value; it keeps whatever
        offset it was given. Two rows written a second apart from
        different offsets would then sort by their wall-clock text rather
        than by when they happened, which is the one thing an ordered
        ledger is for.

        Returns:
            The same instant, expressed in UTC.
        """
        return value.astimezone(UTC)


__all__ = ["LifecycleEntityKind", "LifecycleTransition"]
