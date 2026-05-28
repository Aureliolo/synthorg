"""Org memory domain models.

Frozen Pydantic models for organizational facts -- shared company-wide
knowledge such as policies, ADRs, procedures, and conventions.

Includes MVCC models for the append-only operation log and the
materialized snapshot.
"""

from typing import Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from synthorg.core.enums import (
    AutonomyLevel,
    OrgFactCategory,
    SeniorityLevel,
)
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.org_memory import ORG_MEMORY_MODEL_INVALID
from synthorg.ontology.decorator import ontology_entity

logger = get_logger(__name__)


class OrgFactAuthor(BaseModel):
    """Author of an organizational fact.

    If ``is_human`` is ``True``, ``agent_id`` must be ``None``.
    If ``is_human`` is ``False``, ``agent_id`` and ``seniority``
    are required; ``autonomy_level`` is optional (captures the
    instance-specific value at write time).

    Attributes:
        agent_id: Agent identifier (``None`` for human authors).
        seniority: Agent seniority level (``None`` for human authors).
        is_human: Whether the author is a human operator.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    agent_id: NotBlankStr | None = Field(
        default=None,
        description="Agent identifier (None for human authors)",
    )
    seniority: SeniorityLevel | None = Field(
        default=None,
        description="Agent seniority level (None for human authors)",
    )
    autonomy_level: AutonomyLevel | None = Field(
        default=None,
        description="Agent autonomy level at write time (None for human authors)",
    )
    is_human: bool = Field(
        default=False,
        description="Whether the author is a human operator",
    )

    @model_validator(mode="after")
    def _validate_author_consistency(self) -> Self:
        """Ensure human authors have no agent fields and agents have required fields.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        if self.is_human:
            if self.agent_id is not None:
                msg = "Human authors must not have an agent_id"
                logger.warning(
                    ORG_MEMORY_MODEL_INVALID,
                    model="OrgFactAuthor",
                    field="agent_id",
                    reason=msg,
                )
                raise ValueError(msg)
            if self.seniority is not None:
                msg = "Human authors must not have a seniority level"
                logger.warning(
                    ORG_MEMORY_MODEL_INVALID,
                    model="OrgFactAuthor",
                    field="seniority",
                    reason=msg,
                )
                raise ValueError(msg)
            if self.autonomy_level is not None:
                msg = "Human authors must not have an autonomy level"
                logger.warning(
                    ORG_MEMORY_MODEL_INVALID,
                    model="OrgFactAuthor",
                    field="autonomy_level",
                    reason=msg,
                )
                raise ValueError(msg)
        else:
            if self.agent_id is None:
                msg = "Non-human authors must have an agent_id"
                logger.warning(
                    ORG_MEMORY_MODEL_INVALID,
                    model="OrgFactAuthor",
                    field="agent_id",
                    reason=msg,
                )
                raise ValueError(msg)
            if self.seniority is None:
                msg = "Non-human authors must have a seniority level"
                logger.warning(
                    ORG_MEMORY_MODEL_INVALID,
                    model="OrgFactAuthor",
                    field="seniority",
                    reason=msg,
                )
                raise ValueError(msg)
        return self


@ontology_entity
class OrgFact(BaseModel):
    """An organizational fact -- a piece of shared company-wide knowledge.

    Attributes:
        id: Unique identifier for this fact.
        content: The fact content text.
        category: Category classification.
        tags: Metadata tags for cross-cutting concerns.
        author: Who created this fact.
        created_at: Creation timestamp.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    id: NotBlankStr = Field(description="Unique fact identifier")
    content: NotBlankStr = Field(description="Fact content text")
    category: OrgFactCategory = Field(description="Category classification")
    tags: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Metadata tags for cross-cutting concerns",
    )
    author: OrgFactAuthor = Field(description="Who created this fact")
    created_at: AwareDatetime = Field(description="Creation timestamp")


class OrgFactWriteRequest(BaseModel):
    """Request to write a new organizational fact.

    Attributes:
        content: The fact content text.
        category: Category classification.
        tags: Metadata tags for cross-cutting concerns.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    content: NotBlankStr = Field(description="Fact content text")
    category: OrgFactCategory = Field(description="Category classification")
    tags: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Metadata tags for cross-cutting concerns",
    )


class OrgMemoryQuery(BaseModel):
    """Query parameters for org memory retrieval.

    Attributes:
        context: Text search context (``None`` for metadata-only).
        categories: Filter by fact categories.
        limit: Maximum number of results.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    context: NotBlankStr | None = Field(
        default=None,
        description="Text search context",
    )
    categories: frozenset[OrgFactCategory] | None = Field(
        default=None,
        description="Filter by fact categories",
    )
    limit: int = Field(
        default=5,
        ge=1,
        le=100,
        description="Maximum number of results",
    )


# ── MVCC models ─────────────────────────────────────────────────


class OperationLogEntry(BaseModel):
    """Single row in the append-only operation log.

    Every publish or retract is recorded as an immutable log entry.
    The version counter is monotonically increasing per ``fact_id``.

    Attributes:
        operation_id: Globally unique operation identifier.
        fact_id: Logical fact identifier.
        operation_type: ``PUBLISH`` or ``RETRACT``.
        content: Fact body (``None`` for RETRACT operations).
        category: Fact category at time of operation.
        tags: Metadata tags at time of operation.
        author_agent_id: Agent that performed the operation
            (``None`` for human authors).
        author_seniority: Agent seniority level at write time.
        author_is_human: Whether the author is a human operator.
        author_autonomy_level: Agent autonomy level at write time.
        timestamp: UTC timestamp of the operation.
        version: Per-fact version counter (starts at 1).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    operation_id: NotBlankStr = Field(
        description="Globally unique operation identifier",
    )
    fact_id: NotBlankStr = Field(description="Logical fact identifier")
    operation_type: Literal["PUBLISH", "RETRACT"] = Field(
        description="Operation type",
    )
    content: NotBlankStr | None = Field(
        default=None,
        description="Fact body (None for RETRACT)",
    )
    category: OrgFactCategory | None = Field(
        default=None,
        description="Fact category at time of operation",
    )
    tags: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Metadata tags at time of operation",
    )
    author_agent_id: NotBlankStr | None = Field(
        default=None,
        description="Agent that performed the operation",
    )
    author_seniority: SeniorityLevel | None = Field(
        default=None,
        description="Agent seniority level at write time",
    )
    author_is_human: bool = Field(
        default=False,
        description="Whether the author is a human operator",
    )
    author_autonomy_level: AutonomyLevel | None = Field(
        default=None,
        description="Agent autonomy level at write time",
    )
    timestamp: AwareDatetime = Field(description="UTC timestamp")
    version: int = Field(ge=1, description="Per-fact version counter")

    @model_validator(mode="after")
    def _validate_content_alignment(self) -> Self:
        """Ensure PUBLISH has content and RETRACT does not.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        if self.operation_type == "PUBLISH" and self.content is None:
            msg = "PUBLISH operations must have non-None content"
            logger.warning(
                ORG_MEMORY_MODEL_INVALID,
                model="OperationLogEntry",
                field="content",
                reason=msg,
            )
            raise ValueError(msg)
        if self.operation_type == "RETRACT" and self.content is not None:
            msg = "RETRACT operations must have content=None"
            logger.warning(
                ORG_MEMORY_MODEL_INVALID,
                model="OperationLogEntry",
                field="content",
                reason=msg,
            )
            raise ValueError(msg)
        return self


class OperationLogSnapshot(BaseModel):
    """Materialized snapshot row for current committed state.

    Represents the state of a single fact at a point in time.
    Active facts have ``retracted_at=None``.

    Attributes:
        fact_id: Logical fact identifier (primary key).
        content: Current fact body.
        category: Fact category.
        tags: Current metadata tags.
        created_at: Timestamp of first PUBLISH.
        retracted_at: Timestamp of retraction (``None`` = active).
        version: Version matching most recent operation log entry.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    fact_id: NotBlankStr = Field(description="Logical fact identifier")
    content: NotBlankStr = Field(description="Current fact body")
    category: OrgFactCategory = Field(description="Fact category")
    tags: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Current metadata tags",
    )
    created_at: AwareDatetime = Field(
        description="Timestamp of first PUBLISH",
    )
    retracted_at: AwareDatetime | None = Field(
        default=None,
        description="Retraction timestamp (None = active)",
    )
    version: int = Field(ge=1, description="Most recent operation version")

    @model_validator(mode="after")
    def _validate_created_before_retracted(self) -> Self:
        """Ensure created_at is not after retracted_at.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        if self.retracted_at is not None and self.created_at > self.retracted_at:
            msg = "created_at must be <= retracted_at"
            logger.warning(
                ORG_MEMORY_MODEL_INVALID,
                model="OperationLogSnapshot",
                field="created_at",
                reason=msg,
            )
            raise ValueError(msg)
        return self
