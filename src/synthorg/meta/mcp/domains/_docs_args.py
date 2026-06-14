"""Pydantic args models for living-documentation MCP tools."""

from pydantic import Field

from synthorg.core.types import NotBlankStr
from synthorg.docs_engine.constants import DOCS_LIST_DEFAULT_LIMIT
from synthorg.docs_engine.enums import DocType
from synthorg.meta.mcp.domains._common_args import AdminGuardrailFields, _ArgsBase
from synthorg.tools.docs._args import (
    WriteLivingDocBlockArg,
)


class DocsWriteArgs(AdminGuardrailFields):
    """Args for ``docs:write`` (admin-gated write).

    Accepts a project_id at the MCP boundary explicitly: MCP clients
    are operator-driven, not agent-context-bound, so the project is
    always supplied as input rather than inferred.
    """

    project_id: NotBlankStr = Field(description="Owning project")
    title: NotBlankStr = Field(description="Document title")
    doc_type: DocType = Field(description="Doc taxonomy bucket")
    body: tuple[WriteLivingDocBlockArg, ...] = Field(
        min_length=1,
        description="Ordered tuple of typed body blocks",
    )
    tags: tuple[NotBlankStr, ...] = Field(default=())
    related_task_ids: tuple[NotBlankStr, ...] = Field(default=())
    author_agent_id: NotBlankStr = Field(
        description="Identifier for the writer agent / operator",
    )
    slug: NotBlankStr | None = Field(default=None)


class DocsReadArgs(_ArgsBase):
    """Args for ``docs:get``."""

    project_id: NotBlankStr = Field(description="Owning project")
    slug: NotBlankStr = Field(description="Doc slug")
    version: NotBlankStr | None = Field(
        default=None,
        description="Optional commit SHA on synthorg/docs",
    )


class DocsListArgs(_ArgsBase):
    """Args for ``docs:list``."""

    project_id: NotBlankStr = Field(description="Owning project")
    doc_type: DocType | None = Field(default=None)
    tag: NotBlankStr | None = Field(default=None)
    limit: int = Field(default=DOCS_LIST_DEFAULT_LIMIT, ge=1, le=500)
    offset: int = Field(default=0, ge=0)


class DocsSearchArgs(_ArgsBase):
    """Args for ``docs:search``."""

    project_id: NotBlankStr = Field(description="Owning project")
    query: NotBlankStr = Field(description="Search text")
    doc_types: tuple[DocType, ...] | None = Field(default=None)
    limit: int = Field(default=8, ge=1, le=64)


class DocsHistoryArgs(_ArgsBase):
    """Args for ``docs:history``."""

    project_id: NotBlankStr = Field(description="Owning project")
    slug: NotBlankStr = Field(description="Doc slug")
    limit: int = Field(default=50, ge=1, le=500)
