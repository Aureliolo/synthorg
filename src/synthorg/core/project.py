"""Project domain model for task collection management."""

from collections import Counter
from datetime import UTC, datetime
from typing import Self
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from synthorg.core.autonomy_enums import AutonomyLevel
from synthorg.core.project_enums import ProjectStatus
from synthorg.core.types import NotBlankStr
from synthorg.core.validation import validate_iso8601_deadline
from synthorg.ontology.decorator import ontology_entity


@ontology_entity
class Project(BaseModel):
    """A collection of related tasks with a shared goal, team, and deadline.

    Projects organize tasks into a coherent unit of work with budget
    tracking and team assignment.  Per the Design Overview glossary
    and entity relationship tree.

    Attributes:
        id: Unique project identifier (auto-generated UUID).
        name: Project display name.
        description: Detailed project description.
        team: Agent IDs assigned to this project.
        lead: Agent ID of the project lead.
        plan_id: The plan this project is currently executing, or ``None``
            before one has been approved and dispatched. Repointed by the same
            write that supersedes a retired revision, so it always names the
            live plan; earlier revisions stay reachable by querying plans
            filtered on this project.
        deadline: Optional deadline (ISO 8601 string or ``None``).
        budget: Total budget in base currency (configurable, defaults to EUR).
        status: Current project status.
        autonomy_mode: Operator-set oversight mode for this initiative. When
            set, it becomes the initiative-level autonomy override the gate
            resolves against (more specific than a department default, less
            than a per-agent override); ``None`` inherits the department or
            company default.
        version: Optimistic-concurrency revision, bumped on each persisted
            edit so a version-guarded write cannot silently clobber a
            concurrent update (e.g. two workers staffing the same lead).
        created_at: When the project was opened (tz-aware UTC). Load-bearing
            beyond the audit trail: intake bounds its reuse of an existing
            project by age, and without a recorded start there is nothing to
            measure that age against.
        updated_at: Last-revision timestamp (tz-aware UTC).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    id: UUID = Field(default_factory=uuid4, description="Unique project identifier")
    name: NotBlankStr = Field(description="Project display name")
    description: str = Field(
        default="",
        description="Detailed project description",
    )
    team: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Agent IDs assigned to this project",
    )
    lead: NotBlankStr | None = Field(
        default=None,
        description="Agent ID of the project lead",
    )
    plan_id: UUID | None = Field(
        default=None,
        description="Plan this project is currently executing",
    )
    deadline: str | None = Field(
        default=None,
        description="Optional deadline (ISO 8601 string)",
    )
    budget: float = Field(
        default=0.0,
        ge=0.0,
        description="Total budget in base currency (configurable, defaults to EUR)",
    )
    status: ProjectStatus = Field(
        default=ProjectStatus.PLANNING,
        description="Current project status",
    )
    autonomy_mode: AutonomyLevel | None = Field(
        default=None,
        description="Operator-set oversight mode for this initiative "
        "(None inherits the department or company default)",
    )
    version: int = Field(
        default=1,
        ge=1,
        description="Optimistic-concurrency revision, bumped on each edit",
    )
    created_at: AwareDatetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="When the project was opened (tz-aware UTC)",
    )
    updated_at: AwareDatetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Last-revision timestamp (tz-aware UTC)",
    )

    @model_validator(mode="after")
    def _validate_deadline_format(self) -> Self:
        """Validate deadline format if present.

        Returns:
            The validated instance (Pydantic ``model_validator`` contract).

        Raises:
            ValueError: If ``deadline`` is whitespace-only or not a valid
                ISO 8601 string.
        """
        validate_iso8601_deadline(self.deadline)
        return self

    @model_validator(mode="after")
    def _validate_collections(self) -> Self:
        """Validate collection uniqueness.

        Returns:
            The validated instance (Pydantic ``model_validator`` contract).

        Raises:
            ValueError: If ``team`` contains duplicate entries.
        """
        if len(self.team) != len(set(self.team)):
            dupes = sorted(m for m, c in Counter(self.team).items() if c > 1)
            msg = f"Duplicate entries in team: {dupes}"
            raise ValueError(msg)
        return self
