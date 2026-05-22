"""Brownfield codebase intake boundary models."""

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr

_DEFAULT_TITLE: str = "Imported codebase"


class CodebaseImportSubmission(BaseModel):
    """Operator request to import an existing codebase into a project.

    Attributes:
        project_id: Target project the codebase is imported into (1:1 with
            its workspace and structure map).
        source_ref: Remote clone URL or local path to import from.
        title: Human-readable title for the indexed knowledge source.
        requested_by: Agent name or user id that requested the import.
        default_branch: Branch to provision/seed (defaults to ``main``).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    project_id: NotBlankStr = Field(description="Target project identifier")
    source_ref: NotBlankStr = Field(description="Remote URL or local path to import")
    title: NotBlankStr = Field(
        default=NotBlankStr(_DEFAULT_TITLE),
        description="Title for the indexed knowledge source",
    )
    requested_by: NotBlankStr = Field(
        default=NotBlankStr("operator"),
        description="Agent name or user id that requested the import",
    )
    default_branch: NotBlankStr = Field(
        default=NotBlankStr("main"),
        description="Branch to provision and seed",
    )


class CodebaseImportResult(BaseModel):
    """Outcome of a brownfield import.

    Attributes:
        project_id: The project the codebase was imported into.
        source_ref: The source that was imported.
        content_hash: The structure-map content hash (stable per source state).
        module_count: Number of modules discovered.
        dependency_count: Number of declared dependencies discovered.
        knowledge_source_id: Identifier of the indexed knowledge source, or
            ``None`` when indexing was skipped (unchanged re-import).
        unchanged: ``True`` when a same-source re-import short-circuited.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    project_id: NotBlankStr
    source_ref: NotBlankStr
    content_hash: NotBlankStr
    module_count: int = Field(ge=0)
    dependency_count: int = Field(ge=0)
    knowledge_source_id: NotBlankStr | None = Field(default=None)
    unchanged: bool = Field(default=False)


__all__ = ["CodebaseImportResult", "CodebaseImportSubmission"]
