"""Vendor-agnostic external search provider protocols.

The research subsystem ships these protocols and their result models but no
concrete vendor implementations: an operator injects a provider at runtime
(via MCP bridge or a custom adapter), mirroring the existing
:class:`~synthorg.tools.web.web_search.WebSearchProvider` house pattern. A
missing provider simply means that source family does not fan out.

Web search reuses the existing ``WebSearchProvider``; this module adds the
academic and code search families.
"""

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr  # noqa: TC001 -- Pydantic field annotation
from synthorg.research.constants import RESEARCH_DEFAULT_PER_QUERY_LIMIT


class AcademicResult(BaseModel):
    """A single academic search result (paper / preprint)."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    title: NotBlankStr = Field(description="Paper title")
    identifier: NotBlankStr = Field(
        description="Stable identifier (arXiv id, DOI, corpus id, or URL)",
    )
    abstract: str = Field(description="Abstract or summary snippet")
    doi: NotBlankStr | None = Field(default=None, description="DOI, if known")
    authors: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Author display names, in listed order",
    )
    year: int | None = Field(
        default=None,
        ge=1500,
        le=2200,
        description="Publication year, if known",
    )
    url: NotBlankStr | None = Field(default=None, description="Landing-page URL")


class CodeResult(BaseModel):
    """A single code search result (a file or span in a repository)."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    title: NotBlankStr = Field(description="Result title (path or symbol)")
    repo: NotBlankStr = Field(description="Repository identifier (owner/name or URL)")
    path: NotBlankStr = Field(description="Repo-relative file path")
    snippet: str = Field(description="Matching code excerpt")
    line_start: int | None = Field(default=None, ge=1, description="1-indexed start")
    line_end: int | None = Field(default=None, ge=1, description="1-indexed end")
    ref: NotBlankStr | None = Field(
        default=None,
        description="Commit SHA / branch / tag the span was read at",
    )
    url: NotBlankStr | None = Field(default=None, description="Permalink URL")


@runtime_checkable
class AcademicSearchProvider(Protocol):
    """Abstracted academic-paper search provider."""

    async def search(
        self,
        query: str,
        max_results: int = RESEARCH_DEFAULT_PER_QUERY_LIMIT,
    ) -> list[AcademicResult]:
        """Return academic results matching *query*."""
        ...


@runtime_checkable
class CodeSearchProvider(Protocol):
    """Abstracted code search provider."""

    async def search(
        self,
        query: str,
        max_results: int = RESEARCH_DEFAULT_PER_QUERY_LIMIT,
    ) -> list[CodeResult]:
        """Return code results matching *query*."""
        ...
