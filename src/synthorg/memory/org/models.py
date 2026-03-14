"""Org memory domain models.

Frozen Pydantic models for organizational facts — shared company-wide
knowledge such as policies, ADRs, procedures, and conventions.
"""

from typing import Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from synthorg.core.enums import OrgFactCategory, SeniorityLevel  # noqa: TC001
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.observability import get_logger
from synthorg.observability.events.org_memory import ORG_MEMORY_MODEL_INVALID

logger = get_logger(__name__)


class OrgFactAuthor(BaseModel):
    """Author of an organizational fact.

    If ``is_human`` is ``True``, ``agent_id`` must be ``None``.
    If ``is_human`` is ``False``, ``agent_id`` and ``seniority``
    are required.

    Attributes:
        agent_id: Agent identifier (``None`` for human authors).
        seniority: Agent seniority level (``None`` for human authors).
        is_human: Whether the author is a human operator.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    agent_id: NotBlankStr | None = Field(
        default=None,
        description="Agent identifier (None for human authors)",
    )
    seniority: SeniorityLevel | None = Field(
        default=None,
        description="Agent seniority level (None for human authors)",
    )
    is_human: bool = Field(
        default=False,
        description="Whether the author is a human operator",
    )

    @model_validator(mode="after")
    def _validate_author_consistency(self) -> Self:
        """Ensure human authors have no agent_id and agents have required fields."""
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


class OrgFact(BaseModel):
    """An organizational fact — a piece of shared company-wide knowledge.

    Attributes:
        id: Unique identifier for this fact.
        content: The fact content text.
        category: Category classification.
        author: Who created this fact.
        created_at: Creation timestamp.
        version: Monotonically increasing version number.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    id: NotBlankStr = Field(description="Unique fact identifier")
    content: NotBlankStr = Field(description="Fact content text")
    category: OrgFactCategory = Field(description="Category classification")
    author: OrgFactAuthor = Field(description="Who created this fact")
    created_at: AwareDatetime = Field(description="Creation timestamp")
    version: int = Field(ge=1, description="Fact version number")


class OrgFactWriteRequest(BaseModel):
    """Request to write a new organizational fact.

    Attributes:
        content: The fact content text.
        category: Category classification.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    content: NotBlankStr = Field(description="Fact content text")
    category: OrgFactCategory = Field(description="Category classification")


class OrgMemoryQuery(BaseModel):
    """Query parameters for org memory retrieval.

    Attributes:
        context: Text search context (``None`` for metadata-only).
        categories: Filter by fact categories.
        limit: Maximum number of results.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

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
