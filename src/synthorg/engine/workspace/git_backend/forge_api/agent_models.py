"""Vendor-neutral result models for the forge agent-operations client.

These are the domain shapes the agent-facing forge tools return, mapped
by each per-forge client from its native REST payload. They are frozen
and ``extra="forbid"`` because they are SynthOrg's own contract, unlike
the ``extra="ignore"`` payload-parsing models internal to each client.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict

from synthorg.core.types import NotBlankStr

# Request-side closed sets shared by the protocol and the tool args.
ForgeIssueState = Literal["open", "closed", "all"]
ForgePullState = Literal["open", "closed", "all"]
ForgeReviewDecision = Literal["approve", "request_changes", "comment"]
ForgeMergeMethod = Literal["merge", "squash", "rebase"]
ForgeEntryKind = Literal["file", "dir"]


class _ForgeResult(BaseModel):
    """Base config for the vendor-neutral forge result models."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")


class ForgeFileContent(_ForgeResult):
    """Decoded contents of a single file at a ref."""

    path: NotBlankStr
    ref: str
    content: str
    size: int
    sha: str


class ForgeDirEntry(_ForgeResult):
    """One entry in a repository directory listing."""

    name: NotBlankStr
    path: NotBlankStr
    kind: ForgeEntryKind
    size: int
    sha: str


class ForgeIssue(_ForgeResult):
    """A repository issue."""

    number: int
    title: str
    state: str
    body: str
    author: str
    url: str
    labels: tuple[str, ...] = ()


class ForgeComment(_ForgeResult):
    """A comment on an issue or pull request."""

    id: int
    author: str
    body: str
    url: str


class ForgePullRequest(_ForgeResult):
    """A pull / merge request."""

    number: int
    title: str
    state: str
    body: str
    author: str
    url: str
    source_branch: str
    target_branch: str
    draft: bool = False
    merged: bool = False


class ForgeReview(_ForgeResult):
    """A submitted pull-request review."""

    id: int
    state: str
    author: str
    body: str
    url: str


class ForgeMergeResult(_ForgeResult):
    """Outcome of a merge attempt."""

    merged: bool
    sha: str
    message: str


class ForgeCiRun(_ForgeResult):
    """A continuous-integration run for a repository."""

    id: int
    name: str
    status: str
    conclusion: str
    branch: str
    commit_sha: str
    url: str


__all__ = [
    "ForgeCiRun",
    "ForgeComment",
    "ForgeDirEntry",
    "ForgeEntryKind",
    "ForgeFileContent",
    "ForgeIssue",
    "ForgeIssueState",
    "ForgeMergeMethod",
    "ForgeMergeResult",
    "ForgePullRequest",
    "ForgePullState",
    "ForgeReview",
    "ForgeReviewDecision",
]
