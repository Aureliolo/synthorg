"""Codebase structure-map domain model.

A :class:`CodebaseStructureMap` is a navigable, deterministic model of an
imported codebase: its modules, entry points, test suites, build files,
and declared dependencies. Built once per brownfield import by the
structure-map scanner (no LLM), persisted 1:1 per project, and queried by
agents through a dedicated tool so the analysis pass and follow-up work
are grounded in a concrete map rather than free-text retrieval alone.

The nested collections are deliberately small frozen value objects so the
whole map round-trips as JSON in a single persisted row.
"""

from enum import StrEnum
from typing import Annotated

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
)

from synthorg.core.types import NotBlankStr

Sha256Hex = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
"""A 64-character lowercase hex SHA-256 digest."""

RelPath = Annotated[str, StringConstraints(min_length=1, max_length=1024)]
"""A repository-relative path (bounded, non-empty)."""


class Ecosystem(StrEnum):
    """Language/packaging ecosystem a scanner recognises.

    ``GENERIC`` is the safe-default fallback used when no ecosystem-specific
    scanner matched; it carries file-tree structure without dependency facts.
    """

    PYTHON = "python"
    JAVASCRIPT = "javascript"
    TYPESCRIPT = "typescript"
    GO = "go"
    RUST = "rust"
    GENERIC = "generic"


class ModuleKind(StrEnum):
    """Structural kind of a discovered module."""

    PACKAGE = "package"
    MODULE = "module"
    DIRECTORY = "directory"


class EntryPointKind(StrEnum):
    """Classification of an executable entry point."""

    CONSOLE_SCRIPT = "console_script"
    MAIN_MODULE = "main_module"
    BINARY = "binary"
    WEB_SERVICE = "web_service"
    UNKNOWN = "unknown"


class DependencyScope(StrEnum):
    """When a declared dependency is required."""

    RUNTIME = "runtime"
    DEVELOPMENT = "development"
    BUILD = "build"
    OPTIONAL = "optional"


class Module(BaseModel):
    """A source module/package within the codebase."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    path: RelPath = Field(description="Repository-relative module path")
    language: Ecosystem = Field(description="Primary language/ecosystem")
    kind: ModuleKind = Field(description="Structural kind of the module")


class EntryPoint(BaseModel):
    """An executable entry point (console script, main module, binary)."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    path: RelPath = Field(description="Repository-relative entry-point path")
    kind: EntryPointKind = Field(description="Entry-point classification")
    command: NotBlankStr | None = Field(
        default=None,
        description="Invocation command/name when declared (e.g. console script)",
    )


class TestSuite(BaseModel):
    """A discovered test location and its framework, when detectable."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    path: RelPath = Field(description="Repository-relative test path")
    framework: NotBlankStr | None = Field(
        default=None,
        description="Detected test framework (e.g. pytest, jest) when known",
    )


class BuildFile(BaseModel):
    """A build/packaging manifest discovered in the tree."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    path: RelPath = Field(description="Repository-relative build-file path")
    tool: NotBlankStr = Field(
        description="Build/packaging tool the file belongs to (e.g. pyproject)",
    )


class Dependency(BaseModel):
    """A declared third-party dependency."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    name: NotBlankStr = Field(description="Dependency package name")
    ecosystem: Ecosystem = Field(description="Declaring ecosystem")
    scope: DependencyScope = Field(
        default=DependencyScope.RUNTIME,
        description="When the dependency is required",
    )
    version_spec: NotBlankStr | None = Field(
        default=None,
        description="Declared version constraint, when present",
    )


class CodebaseStructureMap(BaseModel):
    """Navigable structure model of an imported codebase (1:1 per project).

    Attributes:
        project_id: Owning project identifier (primary key, 1:1 with
            ``Project.id`` and :class:`ProjectWorkspace`).
        source_ref: The import source reference (remote URL or local path)
            this map was scanned from; distinguishes a same-source re-scan
            from importing a different codebase.
        modules: Discovered source modules/packages.
        entry_points: Discovered executable entry points.
        test_suites: Discovered test locations.
        build_files: Discovered build/packaging manifests.
        dependencies: Declared third-party dependencies.
        scanned_at: Scan timestamp (timezone-aware, UTC).
        content_hash: Stable digest of the scan result; an unchanged hash
            on a same-source re-import short-circuits the work.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    project_id: NotBlankStr = Field(description="Owning project identifier (PK)")
    source_ref: NotBlankStr = Field(
        description="Import source reference this map was scanned from",
    )
    modules: tuple[Module, ...] = Field(
        default=(), description="Discovered source modules/packages"
    )
    entry_points: tuple[EntryPoint, ...] = Field(
        default=(), description="Discovered executable entry points"
    )
    test_suites: tuple[TestSuite, ...] = Field(
        default=(), description="Discovered test locations"
    )
    build_files: tuple[BuildFile, ...] = Field(
        default=(), description="Discovered build/packaging manifests"
    )
    dependencies: tuple[Dependency, ...] = Field(
        default=(), description="Declared third-party dependencies"
    )
    scanned_at: AwareDatetime = Field(description="Scan timestamp (UTC)")
    content_hash: Sha256Hex = Field(description="Stable digest of the scan result")
