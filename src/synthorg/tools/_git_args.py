"""Typed argument models for the six git tools.

Mirrors the existing JSON Schemas declared on each ``_BaseGitTool``
subclass in ``synthorg.tools.git_tools``.  Branch action and HTTP-method
style closed sets are promoted to ``Literal`` aliases so the runtime
``frozenset`` checks that follow the dispatch boundary become
unreachable.

Tools wired to consume these models:

* :class:`~synthorg.tools.git_tools.GitStatusTool` -> :class:`GitStatusArgs`
* :class:`~synthorg.tools.git_tools.GitLogTool` -> :class:`GitLogArgs`
* :class:`~synthorg.tools.git_tools.GitDiffTool` -> :class:`GitDiffArgs`
* :class:`~synthorg.tools.git_tools.GitBranchTool` -> :class:`GitBranchArgs`
* :class:`~synthorg.tools.git_tools.GitCommitTool` -> :class:`GitCommitArgs`
* :class:`~synthorg.tools.git_tools.GitCloneTool` -> :class:`GitCloneArgs`
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr  # noqa: TC001 -- Pydantic field type

_ARGS_CONFIG = ConfigDict(
    frozen=True,
    allow_inf_nan=False,
    extra="forbid",
)


GitBranchAction = Literal["list", "create", "switch", "delete"]


class GitStatusArgs(BaseModel):
    """Args for ``git_status``."""

    model_config = _ARGS_CONFIG

    short: bool = Field(default=False, description="Use short format output")
    porcelain: bool = Field(
        default=False,
        description="Use machine-readable porcelain format",
    )


class GitLogArgs(BaseModel):
    """Args for ``git_log``."""

    model_config = _ARGS_CONFIG

    max_count: int = Field(
        default=10,
        ge=1,
        le=100,
        description="Max commits to return",
    )
    oneline: bool = Field(default=False, description="Use one-line format")
    ref: NotBlankStr | None = Field(
        default=None,
        description="Branch, tag, or commit ref to start from",
    )
    author: NotBlankStr | None = Field(
        default=None,
        description="Filter commits by author pattern",
    )
    since: NotBlankStr | None = Field(
        default=None,
        description="Show commits after this date",
    )
    until: NotBlankStr | None = Field(
        default=None,
        description="Show commits before this date",
    )
    paths: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Limit to commits touching these paths",
    )


class GitDiffArgs(BaseModel):
    """Args for ``git_diff``.

    The ``ref2 requires ref1`` cross-field constraint is enforced inside
    the tool body (LLM-facing message references the param names).
    """

    model_config = _ARGS_CONFIG

    staged: bool = Field(default=False, description="Show staged (cached) changes")
    stat: bool = Field(
        default=False,
        description="Show diffstat summary instead of full diff",
    )
    ref1: NotBlankStr | None = Field(
        default=None,
        description="First ref for comparison",
    )
    ref2: NotBlankStr | None = Field(
        default=None,
        description="Second ref for comparison",
    )
    paths: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Limit diff to these paths",
    )


class GitBranchArgs(BaseModel):
    """Args for ``git_branch``.

    The ``name`` requirement for create/switch/delete actions is
    enforced inside the tool body so the LLM-facing error names the
    specific action.
    """

    model_config = _ARGS_CONFIG

    action: GitBranchAction = Field(
        default="list",
        description="Branch action to perform",
    )
    name: NotBlankStr | None = Field(
        default=None,
        description="Branch name (required for create/switch/delete)",
    )
    start_point: NotBlankStr | None = Field(
        default=None,
        description="Starting ref for branch creation",
    )
    force: bool = Field(
        default=False,
        description="Force delete (-D) instead of safe delete (-d)",
    )


class GitCommitArgs(BaseModel):
    """Args for ``git_commit``."""

    model_config = _ARGS_CONFIG

    message: NotBlankStr = Field(description="Commit message")
    paths: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Paths to stage before committing",
    )
    all: bool = Field(
        default=False,
        description="Stage all modified and deleted files",
    )


class GitCloneArgs(BaseModel):
    """Args for ``git_clone``.

    URL scheme/SSRF validation stays inside the tool body because the
    GitCloneNetworkPolicy is per-instance.
    """

    model_config = _ARGS_CONFIG

    url: NotBlankStr = Field(description="Repository URL to clone")
    directory: NotBlankStr | None = Field(
        default=None,
        description="Target directory name within workspace",
    )
    branch: NotBlankStr | None = Field(default=None, description="Branch to clone")
    depth: int | None = Field(
        default=None,
        ge=1,
        description="Shallow clone depth",
    )


__all__ = [
    "GitBranchAction",
    "GitBranchArgs",
    "GitCloneArgs",
    "GitCommitArgs",
    "GitDiffArgs",
    "GitLogArgs",
    "GitStatusArgs",
]
