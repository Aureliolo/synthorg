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

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

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

    Cross-field rule: ``ref2`` is meaningless without ``ref1`` (it is
    the second operand of a two-ref diff), so callers that supply
    ``ref2`` alone are rejected at validation time.
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
        description="Second ref for comparison (requires ref1)",
    )
    paths: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Limit diff to these paths",
    )

    @model_validator(mode="after")
    def _ref2_requires_ref1(self) -> Self:
        """Reject ``ref2`` without ``ref1``."""
        if self.ref2 is not None and self.ref1 is None:
            msg = "ref2 requires ref1: a two-ref diff needs both operands"
            raise ValueError(msg)
        return self


class GitBranchArgs(BaseModel):
    """Args for ``git_branch``.

    Cross-field rule: ``create`` / ``switch`` / ``delete`` actions
    require ``name``; only ``list`` is allowed without it.  Validation
    runs at the typed-args boundary so a malformed payload fails
    before the tool body sees it.
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

    @model_validator(mode="after")
    def _name_required_for_mutating_actions(self) -> Self:
        """Reject create/switch/delete actions without a branch name."""
        if self.action != "list" and self.name is None:
            msg = (
                f"action {self.action!r} requires a branch ``name``; "
                "only ``list`` may be invoked without one"
            )
            raise ValueError(msg)
        return self


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
