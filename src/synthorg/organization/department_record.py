# module-kind: declarative
"""Durable department record.

The public, frozen form of the department state the :class:`DepartmentService`
previously held only in an in-memory ``__slots__`` dataclass. Promoting it to a
frozen Pydantic model lets the department repository persist and rehydrate
departments so ``create_department`` / ``remove_department`` survive restart.
"""

from datetime import datetime
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr


class DepartmentRecord(BaseModel):
    """A department as stored in the durable registry.

    Attributes:
        id: Stable primary key.
        name: Unique department name.
        description: Human-readable description.
        created_at: First-written timestamp (UTC-aware).
        updated_at: Last-refreshed timestamp (UTC-aware).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    name: NotBlankStr
    description: str = ""
    created_at: AwareDatetime
    updated_at: AwareDatetime


class _DepartmentRecord:
    """Mutable working record held by :class:`DepartmentService`.

    The service mutates ``name`` / ``description`` / ``updated_at`` in place
    under its lock and deep-copies on return, so the working record stays a
    light mutable struct. It projects to / from the frozen
    :class:`DepartmentRecord` at the durable-store boundary.
    """

    __slots__ = ("created_at", "description", "id", "name", "updated_at")

    def __init__(
        self,
        *,
        id: UUID,  # noqa: A002
        name: str,
        description: str,
        created_at: datetime,
    ) -> None:
        self.id = id
        self.name = name
        self.description = description
        self.created_at = created_at
        self.updated_at = created_at

    def to_dict(self) -> dict[str, object]:
        """Render the record as a JSON-serialisable mapping.

        Returns:
            The record's fields with ISO-formatted timestamps.
        """
        return {
            "id": str(self.id),
            "name": self.name,
            "description": self.description,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    def to_durable(self) -> DepartmentRecord:
        """Project to the durable frozen :class:`DepartmentRecord`.

        Returns:
            The persistable record.
        """
        return DepartmentRecord(
            id=self.id,
            name=NotBlankStr(self.name),
            description=self.description,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )

    @classmethod
    def from_durable(cls, record: DepartmentRecord) -> _DepartmentRecord:
        """Rebuild the mutable working record from a durable one.

        Returns:
            The in-memory record mirroring *record*.
        """
        rebuilt = cls(
            id=record.id,
            name=record.name,
            description=record.description,
            created_at=record.created_at,
        )
        rebuilt.updated_at = record.updated_at
        return rebuilt


__all__ = ["DepartmentRecord", "_DepartmentRecord"]
