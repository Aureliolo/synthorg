"""Pydantic args models for research-subsystem MCP tools."""

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr
from synthorg.research.constants import (
    RESEARCH_LIST_DEFAULT_LIMIT,
    RESEARCH_LIST_MAX_LIMIT,
)
from synthorg.research.enums import ResearchRunStatus
from synthorg.research.tool import ResearchBriefArgs


class ResearchRunArgs(ResearchBriefArgs):
    """Args for ``research:run``: the agent brief args plus a project scope."""

    project_id: NotBlankStr | None = Field(
        default=None,
        description="Owning project for knowledge scoping (null = global)",
    )


class ResearchGetArgs(BaseModel):
    """Args for ``research:get``."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    run_id: NotBlankStr = Field(description="The run identifier to fetch")


class ResearchListArgs(BaseModel):
    """Args for ``research:list``."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    brief_id: NotBlankStr | None = Field(default=None)
    project_id: NotBlankStr | None = Field(default=None)
    status: ResearchRunStatus | None = Field(default=None)
    limit: int = Field(
        default=RESEARCH_LIST_DEFAULT_LIMIT, ge=1, le=RESEARCH_LIST_MAX_LIMIT
    )
    offset: int = Field(default=0, ge=0)
