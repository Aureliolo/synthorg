"""Citation domain model.

Represents a single source citation tracked across delegation chains.
"""

from typing import Literal

from pydantic import AnyHttpUrl, AwareDatetime, BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr  # noqa: TC001


class Citation(BaseModel):
    """A single tracked source citation.

    Citations are numbered sequentially (stable across the final report)
    and deduplicated by normalized URL within a ``CitationManager``.

    Attributes:
        number: Stable citation number (>= 1).
        url: Canonical normalized URL.
        title: Human-readable title of the source.
        first_seen_at: When this citation was first recorded.
        first_seen_by_agent_id: Agent that first encountered this source.
        accessed_via: How the source was accessed.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    number: int = Field(ge=1, description="Stable citation number")
    url: AnyHttpUrl = Field(description="Canonical normalized URL")
    title: NotBlankStr = Field(description="Source title")
    first_seen_at: AwareDatetime = Field(
        description="When first recorded",
    )
    first_seen_by_agent_id: NotBlankStr = Field(
        description="Agent that first saw this source",
    )
    accessed_via: Literal["tool", "memory", "file"] = Field(
        description="How the source was accessed",
    )
