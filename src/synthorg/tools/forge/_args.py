"""Frozen argument models for the resource-grouped forge tools.

Each tool dispatches on an ``action`` field (mirroring the multi-action
``git_branch`` tool). Owner / repo / path arguments become forge REST URL
path segments, so they are validated here to reject traversal (``..``),
separators, and control characters before they can reach the client.
"""

from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.types import NotBlankStr
from synthorg.engine.workspace.git_backend.forge_api.agent_models import (
    ForgeDiffSide,
    ForgeIssueState,
    ForgeMergeMethod,
    ForgePullState,
    ForgeReviewDecision,
)

_DEFAULT_LIST_LIMIT: Final[int] = 30
_MAX_LIST_LIMIT: Final[int] = 100

_CONTROL_CHAR_THRESHOLD: Final[int] = 0x20
# Characters that must never appear in a value destined for a forge REST
# URL path segment: the backslash separator plus the URL-structure
# characters (query / fragment / userinfo) that could smuggle a different
# request shape. ``/`` is added for non-path segments. Values are also
# percent-quoted at the client, so this is defence-in-depth.
_SEGMENT_FORBIDDEN: Final[frozenset[str]] = frozenset({"\\", "?", "#", "@"})


def _reject_traversal(value: str, *, field: str, allow_slash: bool) -> str:
    """Reject path-traversal / separator / URL-structure / control chars.

    Returns:
        The validated value.

    Raises:
        ValueError: When ``value`` contains a ``..`` segment, a leading
            slash, a disallowed separator / URL-structure character, or a
            control char.
    """
    if ".." in value:
        msg = f"{field} must not contain '..'"
        raise ValueError(msg)
    if value.startswith("/"):
        msg = f"{field} must not start with '/'"
        raise ValueError(msg)
    # owner / repo (no slash) additionally reject ``/`` and ``%``: a raw
    # ``%`` would let a pre-percent-encoded ``%2e%2e`` smuggle traversal
    # (httpx does not re-encode an already-encoded segment). ``path`` is
    # percent-quoted at the client, so it may legitimately carry ``%``.
    forbidden = _SEGMENT_FORBIDDEN if allow_slash else _SEGMENT_FORBIDDEN | {"/", "%"}
    if any(ch in value for ch in forbidden):
        msg = f"{field} contains a disallowed character"
        raise ValueError(msg)
    if any(ord(ch) < _CONTROL_CHAR_THRESHOLD for ch in value):
        msg = f"{field} contains a control character"
        raise ValueError(msg)
    return value


class _ForgeArgsBase(BaseModel):
    """Shared config + owner/repo validation for the forge tool args."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    owner: NotBlankStr
    repo: NotBlankStr

    @model_validator(mode="after")
    def _validate_owner_repo(self) -> _ForgeArgsBase:
        _reject_traversal(str(self.owner), field="owner", allow_slash=False)
        _reject_traversal(str(self.repo), field="repo", allow_slash=False)
        return self


class ForgeRepoArgs(_ForgeArgsBase):
    """Arguments for the ``forge_repo`` tool (read-only)."""

    action: Literal["get_repo", "read_file", "list_dir"]
    path: str = ""
    ref: str = ""

    @property
    def is_write(self) -> bool:
        """Repo reads never mutate forge state."""
        return False

    @model_validator(mode="after")
    def _validate_action(self) -> ForgeRepoArgs:
        if self.path:
            _reject_traversal(self.path, field="path", allow_slash=True)
        if self.action == "read_file" and not self.path:
            msg = "read_file requires a 'path'"
            raise ValueError(msg)
        return self


class ForgeIssueArgs(_ForgeArgsBase):
    """Arguments for the ``forge_issue`` tool."""

    action: Literal["get", "list", "open", "comment"]
    number: int = Field(default=0, ge=0)
    title: str = ""
    body: str = ""
    labels: tuple[str, ...] = ()
    state: ForgeIssueState = "open"
    limit: int = Field(default=_DEFAULT_LIST_LIMIT, ge=1, le=_MAX_LIST_LIMIT)

    @property
    def is_write(self) -> bool:
        """Whether this action mutates forge state."""
        return self.action in {"open", "comment"}

    @model_validator(mode="after")
    def _validate_action(self) -> ForgeIssueArgs:
        if self.action in {"get", "comment"} and self.number <= 0:
            msg = f"{self.action} requires a positive 'number'"
            raise ValueError(msg)
        if self.action == "open" and not self.title.strip():
            msg = "open requires a non-blank 'title'"
            raise ValueError(msg)
        if self.action == "comment" and not self.body.strip():
            msg = "comment requires a non-blank 'body'"
            raise ValueError(msg)
        return self


class ForgeReviewCommentArg(BaseModel):
    """One inline, diff-anchored review comment supplied by the agent."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    path: NotBlankStr
    line: int = Field(gt=0)
    body: NotBlankStr
    side: ForgeDiffSide = "RIGHT"

    @model_validator(mode="after")
    def _validate_path(self) -> ForgeReviewCommentArg:
        _reject_traversal(str(self.path), field="path", allow_slash=True)
        return self


class ForgePullRequestArgs(_ForgeArgsBase):
    """Arguments for the ``forge_pull_request`` tool."""

    action: Literal["get", "list", "open", "comment", "review", "merge"]
    number: int = Field(default=0, ge=0)
    title: str = ""
    body: str = ""
    source_branch: str = ""
    target_branch: str = ""
    draft: bool = False
    decision: ForgeReviewDecision = "comment"
    comments: tuple[ForgeReviewCommentArg, ...] = ()
    method: ForgeMergeMethod = "merge"
    commit_title: str = ""
    state: ForgePullState = "open"
    limit: int = Field(default=_DEFAULT_LIST_LIMIT, ge=1, le=_MAX_LIST_LIMIT)

    @property
    def is_write(self) -> bool:
        """Whether this action mutates forge state."""
        return self.action in {"open", "comment", "review", "merge"}

    @model_validator(mode="after")
    def _validate_action(self) -> ForgePullRequestArgs:
        if self.action in {"get", "comment", "review", "merge"} and self.number <= 0:
            msg = f"{self.action} requires a positive 'number'"
            raise ValueError(msg)
        if self.action == "open" and not (
            self.title.strip() and self.source_branch and self.target_branch
        ):
            msg = "open requires 'title', 'source_branch', and 'target_branch'"
            raise ValueError(msg)
        if self.action == "comment" and not self.body.strip():
            msg = "comment requires a non-blank 'body'"
            raise ValueError(msg)
        return self


class ForgeCiArgs(_ForgeArgsBase):
    """Arguments for the ``forge_ci`` tool (reads + triggers)."""

    action: Literal["list_runs", "get_run", "trigger", "rerun"]
    branch: str = ""
    workflow: str = ""
    run_id: int = Field(default=0, ge=0)
    limit: int = Field(default=_DEFAULT_LIST_LIMIT, ge=1, le=_MAX_LIST_LIMIT)

    @property
    def is_write(self) -> bool:
        """Triggering/re-running CI mutates forge state; reads do not."""
        return self.action in {"trigger", "rerun"}

    @model_validator(mode="after")
    def _validate_action(self) -> ForgeCiArgs:
        if self.action in {"get_run", "rerun"} and self.run_id <= 0:
            msg = f"{self.action} requires a positive 'run_id'"
            raise ValueError(msg)
        if self.action == "trigger" and not (self.workflow and self.branch):
            msg = "trigger requires a 'workflow' and a 'branch'"
            raise ValueError(msg)
        return self


class ForgePushArgs(_ForgeArgsBase):
    """Arguments for the ``forge_push`` tool (branch + file writes).

    ``create_branch`` opens ``new_branch`` from ``from_ref``; ``write_file``
    creates or updates ``path`` on ``branch`` with a commit ``message``.
    Both mutate the remote, so the tool binds ``ActionType.VCS_PUSH`` and
    is governed by the git-access sub-constraint.

    ``sha`` is optional and best-effort: pass the current blob sha to update
    an existing file (some forges require it), omit it to create a new one.
    Whether the target already exists is not knowable at args-construction
    time, so the create/update distinction is not enforced here; the forge
    is the authority and surfaces a conflict if the sha is wrong or missing.
    """

    action: Literal["create_branch", "write_file"]
    new_branch: str = ""
    from_ref: str = ""
    branch: str = ""
    path: str = ""
    content: str = ""
    message: str = ""
    sha: str = ""

    @property
    def is_write(self) -> bool:
        """Every push action mutates remote state."""
        return True

    @model_validator(mode="after")
    def _validate_action(self) -> ForgePushArgs:
        if self.path:
            _reject_traversal(self.path, field="path", allow_slash=True)
        if self.action == "create_branch" and not (self.new_branch and self.from_ref):
            msg = "create_branch requires 'new_branch' and 'from_ref'"
            raise ValueError(msg)
        if self.action == "write_file" and not (
            self.path and self.branch and self.message.strip()
        ):
            msg = "write_file requires 'path', 'branch', and a non-blank 'message'"
            raise ValueError(msg)
        return self


__all__ = [
    "ForgeCiArgs",
    "ForgeIssueArgs",
    "ForgePullRequestArgs",
    "ForgePushArgs",
    "ForgeRepoArgs",
    "ForgeReviewCommentArg",
]
