"""Pydantic args models for living-documentation MCP tools (#1976)."""

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.enums import DocType  # noqa: TC001 -- Pydantic field annotation
from synthorg.core.types import NotBlankStr  # noqa: TC001 -- Pydantic field annotation


class DocsWriteArgs(BaseModel):
    """Args for ``docs:write``.

    Accepts a project_id at the MCP boundary explicitly: MCP clients
    are operator-driven, not agent-context-bound, so the project is
    always supplied as input rather than inferred.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    project_id: NotBlankStr = Field(description="Owning project")
    title: NotBlankStr = Field(description="Document title")
    doc_type: DocType = Field(description="Doc taxonomy bucket")
    body: tuple[dict, ...] = Field(  # type: ignore[type-arg] -- block dict shape
        min_length=1,
        description="Ordered tuple of body block dicts",
    )
    tags: tuple[NotBlankStr, ...] = Field(default=())
    related_task_ids: tuple[NotBlankStr, ...] = Field(default=())
    author_agent_id: NotBlankStr = Field(
        description="Identifier for the writer agent / operator",
    )
    slug: NotBlankStr | None = Field(default=None)


class DocsReadArgs(BaseModel):
    """Args for ``docs:get``."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    project_id: NotBlankStr = Field(description="Owning project")
    slug: NotBlankStr = Field(description="Doc slug")
    version: NotBlankStr | None = Field(
        default=None,
        description="Optional commit SHA on synthorg/docs",
    )


class DocsListArgs(BaseModel):
    """Args for ``docs:list``."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    project_id: NotBlankStr = Field(description="Owning project")
    doc_type: DocType | None = Field(default=None)
    tag: NotBlankStr | None = Field(default=None)
    limit: int = Field(default=50, ge=1, le=500)
    offset: int = Field(default=0, ge=0)


class DocsSearchArgs(BaseModel):
    """Args for ``docs:search``."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    project_id: NotBlankStr = Field(description="Owning project")
    query: NotBlankStr = Field(description="Search text")
    doc_types: tuple[DocType, ...] | None = Field(default=None)
    limit: int = Field(default=8, ge=1, le=64)


class DocsHistoryArgs(BaseModel):
    """Args for ``docs:history``."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    project_id: NotBlankStr = Field(description="Owning project")
    slug: NotBlankStr = Field(description="Doc slug")
    limit: int = Field(default=50, ge=1, le=500)
