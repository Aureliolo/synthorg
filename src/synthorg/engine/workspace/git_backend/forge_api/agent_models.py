"""Vendor-neutral result models for the forge agent-operations client.

These are the domain shapes the agent-facing forge tools return, mapped
by each per-forge client from its native REST payload. They are frozen
and ``extra="forbid"`` because they are SynthOrg's own contract, unlike
the ``extra="ignore"`` payload-parsing models internal to each client.
"""

from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from synthorg.core.types import NotBlankStr

_REPO_ID_FORBIDDEN: Final[frozenset[str]] = frozenset({"/", "\\", "?", "#", "@", "%"})
_CONTROL_CHAR_THRESHOLD: Final[int] = 0x20


def _reject_unsafe_repo_id(value: str, *, field: str) -> str:
    """Reject a forge-returned owner/repo identifier unsafe as a scope segment.

    A forge response is untrusted here: an embedded ``/``, ``..``, or control
    character in ``owner``/``repo`` would flow into the ``owner/repo`` scope
    string and a REST URL path segment, so it is rejected on the read side
    just as the request-side args reject it.

    Returns:
        The validated identifier.

    Raises:
        ValueError: When the identifier carries a traversal, separator, or
            control character.
    """
    if ".." in value or any(ch in value for ch in _REPO_ID_FORBIDDEN):
        msg = f"forge-returned {field} contains a disallowed character"
        raise ValueError(msg)
    if any(ord(ch) < _CONTROL_CHAR_THRESHOLD for ch in value):
        msg = f"forge-returned {field} contains a control character"
        raise ValueError(msg)
    return value


# Request-side closed sets shared by the protocol and the tool args.
# ``all`` is a list-filter value; a returned issue/PR is only ever open
# or closed (see the result-side ``_OpenClosed`` below).
ForgeIssueState = Literal["open", "closed", "all"]
ForgePullState = Literal["open", "closed", "all"]
ForgeReviewDecision = Literal["approve", "request_changes", "comment"]
ForgeMergeMethod = Literal["merge", "squash", "rebase"]
ForgeEntryKind = Literal["file", "dir"]
ForgeOpenClosedState = Literal["open", "closed"]
# Which side of a unified diff an inline review comment anchors to.
ForgeDiffSide = Literal["LEFT", "RIGHT"]
# The agent's effective permission on an accessible repository.
ForgeRepoPermission = Literal["admin", "write", "read"]


class _ForgeResult(BaseModel):
    """Base config for the vendor-neutral forge result models."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")


class ForgeFileContent(_ForgeResult):
    """Decoded contents of a single file at a ref."""

    path: NotBlankStr
    ref: str
    content: str
    size: int = Field(ge=0)
    sha: str


class ForgeDirEntry(_ForgeResult):
    """One entry in a repository directory listing."""

    name: NotBlankStr
    path: NotBlankStr
    kind: ForgeEntryKind
    size: int = Field(ge=0)
    sha: str


class ForgeIssue(_ForgeResult):
    """A repository issue."""

    number: int = Field(gt=0)
    title: str
    state: ForgeOpenClosedState
    body: str
    author: str
    url: str
    labels: tuple[str, ...] = ()


class ForgeComment(_ForgeResult):
    """A comment on an issue or pull request."""

    id: int = Field(gt=0)
    author: str
    body: str
    url: str


class ForgePullRequest(_ForgeResult):
    """A pull / merge request."""

    number: int = Field(gt=0)
    title: str
    state: ForgeOpenClosedState
    body: str
    author: str
    url: str
    source_branch: str
    target_branch: str
    draft: bool = False
    merged: bool = False


class ForgeReviewComment(_ForgeResult):
    """One inline, diff-anchored comment attached to a pull-request review.

    ``line`` is the 1-based line number in the file at ``path`` on the
    chosen diff ``side`` (``RIGHT`` = the head/proposed version, ``LEFT``
    = the base). Inline comments are the review payload the agent supplies;
    the forge anchors them to the diff on submission.
    """

    path: NotBlankStr
    line: int = Field(gt=0)
    body: NotBlankStr
    side: ForgeDiffSide = "RIGHT"


class ForgeReview(_ForgeResult):
    """A submitted pull-request review."""

    id: int = Field(gt=0)
    state: str
    author: str
    body: str
    url: str
    comment_count: int = Field(default=0, ge=0)


class ForgeBranchRef(_ForgeResult):
    """A git ref (branch) created on the forge."""

    name: NotBlankStr
    sha: str


class ForgeFileCommit(_ForgeResult):
    """Outcome of writing a file to a branch via the contents API."""

    path: NotBlankStr
    branch: NotBlankStr
    content_sha: str
    commit_sha: str


class ForgeAccessibleRepo(_ForgeResult):
    """One repository the bound token can reach, for scope selection."""

    owner: NotBlankStr
    repo: NotBlankStr
    permission: ForgeRepoPermission
    private: bool = False

    @field_validator("owner", "repo")
    @classmethod
    def _validate_identifier(cls, v: str) -> str:
        """Reject an unsafe forge-returned owner/repo identifier.

        Returns:
            The validated identifier.
        """
        return _reject_unsafe_repo_id(str(v), field="identifier")


class ForgeMergeResult(_ForgeResult):
    """Outcome of a merge attempt."""

    merged: bool
    sha: str
    message: str


class ForgeCiRun(_ForgeResult):
    """A continuous-integration run for a repository."""

    id: int = Field(gt=0)
    name: str
    status: str
    conclusion: str
    branch: str
    commit_sha: str
    url: str


class ForgeCiTrigger(_ForgeResult):
    """Outcome of triggering or re-running a CI run.

    Some forges (e.g. GitHub ``workflow_dispatch``) accept the trigger
    without returning the created run synchronously, so ``run`` is
    optional and ``triggered`` records that the request was accepted.
    """

    triggered: bool
    message: str
    run: ForgeCiRun | None = None


__all__ = [
    "ForgeAccessibleRepo",
    "ForgeBranchRef",
    "ForgeCiRun",
    "ForgeCiTrigger",
    "ForgeComment",
    "ForgeDiffSide",
    "ForgeDirEntry",
    "ForgeEntryKind",
    "ForgeFileCommit",
    "ForgeFileContent",
    "ForgeIssue",
    "ForgeIssueState",
    "ForgeMergeMethod",
    "ForgeMergeResult",
    "ForgeOpenClosedState",
    "ForgePullRequest",
    "ForgePullState",
    "ForgeRepoPermission",
    "ForgeReview",
    "ForgeReviewComment",
    "ForgeReviewDecision",
]
