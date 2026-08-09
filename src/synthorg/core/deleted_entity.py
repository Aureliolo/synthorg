# module-kind: code
"""What a deleted row was, so the records that name it still make sense.

Spend, metrics, approvals and decision records all name the task they are
about. Pinning the task with a foreign key made every one of them a reason
the task could never be removed: a live run could not delete a project
because one of its tasks had once spent money, and the delete failed with a
constraint name rather than an explanation.

Dropping the pin alone would trade that for a worse problem, an id that
resolves to nothing. So the id stays exactly as written and a tombstone
records what it was, who removed it and when. A cost row can always answer
"what was this for", whether or not the task is still there.

Written only when a person deletes something. Nothing the system does on its
own removes an entity, so nothing the system does on its own writes here.
"""

from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from synthorg.core.types import NotBlankStr


class DeletedEntityKind(StrEnum):
    """Which kind of entity a tombstone stands in for."""

    TASK = "task"
    PLAN = "plan"
    PROJECT = "project"


class DeletedEntity(BaseModel):
    """The record that a named entity was deleted, and what it was.

    Attributes:
        id: Row identifier.
        entity_kind: Whether the tombstone stands for a task, plan or
            project.
        entity_id: The identifier the deleted row carried. Records that
            referenced it still carry this value, which is what makes them
            resolvable rather than dangling.
        display_name: What the entity was called, so a reader gets a name
            rather than a bare identifier.
        deleted_by: Who asked for the deletion. Never ``None``: the system
            does not delete entities on its own, so a tombstone without a
            person is a tombstone that should not exist.
        deleted_at: When the deletion landed (tz-aware UTC).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    id: UUID = Field(default_factory=uuid4, description="Row identifier")
    entity_kind: DeletedEntityKind = Field(description="Task, plan or project")
    entity_id: NotBlankStr = Field(description="The identifier that was removed")
    display_name: NotBlankStr = Field(description="What the entity was called")
    deleted_by: NotBlankStr = Field(description="Who asked for the deletion")
    deleted_at: AwareDatetime = Field(description="When it landed (UTC)")

    @field_validator("deleted_at")
    @classmethod
    def _normalise_deleted_at(cls, value: datetime) -> datetime:
        """Convert an aware timestamp to UTC.

        ``AwareDatetime`` only rejects a naive value; it keeps whatever
        offset it was given, so two tombstones written a second apart from
        different offsets would sort by their wall-clock text rather than by
        when they happened.

        Returns:
            The same instant, expressed in UTC.
        """
        return value.astimezone(UTC)


__all__ = ["DeletedEntity", "DeletedEntityKind"]
