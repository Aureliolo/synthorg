"""GitHub REST payload models + mappers for the agent-operations client.

Kept separate from ``github_agent`` so the client module stays within
its size budget. Payload models are ``extra="ignore"`` (the GitHub
response carries far more than the fields consumed); the mappers project
them onto the vendor-neutral :mod:`agent_models` domain shapes. A
malformed payload surfaces as :class:`GitBackendForgeApiError` via
:func:`parse_github`.
"""

import base64
import binascii
from collections.abc import Mapping
from typing import cast

from pydantic import BaseModel, ConfigDict, ValidationError

from synthorg.core.types import NotBlankStr
from synthorg.engine.errors import GitBackendForgeApiError
from synthorg.engine.workspace.git_backend.forge_api.agent_models import (
    ForgeAccessibleRepo,
    ForgeBranchRef,
    ForgeCiRun,
    ForgeComment,
    ForgeDirEntry,
    ForgeEntryKind,
    ForgeFileCommit,
    ForgeFileContent,
    ForgeIssue,
    ForgeMergeResult,
    ForgeOpenClosedState,
    ForgePullRequest,
    ForgeRepoPermission,
    ForgeReview,
)
from synthorg.engine.workspace.git_backend.forge_api.protocol import ForgeRepo

_FILE_KIND: ForgeEntryKind = "file"
_DIR_KIND: ForgeEntryKind = "dir"


def parse_github[M: BaseModel](data: object, model: type[M], *, what: str) -> M:
    """Validate ``data`` against ``model`` or raise a typed forge error.

    Returns:
        The validated model instance.

    Raises:
        GitBackendForgeApiError: When ``data`` does not satisfy
            ``model`` (the underlying ``ValidationError`` is chained).
    """
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        msg = f"malformed GitHub {what} response"
        raise GitBackendForgeApiError(msg) from exc


class _GhBase(BaseModel):  # lint-allow: frozen-extra-forbid -- forge extras
    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="ignore")


class _GhUser(_GhBase):
    login: str = ""


class _GhLabel(_GhBase):
    name: str = ""


class GhRepo(_GhBase):
    """A repository descriptor."""

    full_name: str = ""
    default_branch: str = "main"
    private: bool = True
    clone_url: str = ""


class GhContentFile(_GhBase):
    """A single-file ``contents`` response."""

    path: str = ""
    content: str = ""
    encoding: str = ""
    sha: str = ""
    size: int = 0


class GhContentEntry(_GhBase):
    """One entry in a directory ``contents`` response."""

    name: str = ""
    path: str = ""
    type: str = ""
    sha: str = ""
    size: int = 0


class GhIssue(_GhBase):
    number: int = 0
    title: str = ""
    state: str = ""
    body: str | None = None
    user: _GhUser | None = None
    html_url: str = ""
    labels: tuple[_GhLabel, ...] = ()
    pull_request: Mapping[str, object] | None = None


class GhComment(_GhBase):
    id: int = 0
    body: str = ""
    user: _GhUser | None = None
    html_url: str = ""


class _GhRef(_GhBase):
    ref: str = ""


class GhPull(_GhBase):
    number: int = 0
    title: str = ""
    state: str = ""
    body: str | None = None
    user: _GhUser | None = None
    html_url: str = ""
    head: _GhRef | None = None
    base: _GhRef | None = None
    draft: bool = False
    merged: bool = False


class GhReview(_GhBase):
    id: int = 0
    state: str = ""
    body: str | None = None
    user: _GhUser | None = None
    html_url: str = ""


class GhMerge(_GhBase):
    sha: str = ""
    merged: bool = False
    message: str = ""


class GhRun(_GhBase):
    id: int = 0
    name: str = ""
    status: str = ""
    conclusion: str | None = None
    head_branch: str = ""
    head_sha: str = ""
    html_url: str = ""


class GhRunList(_GhBase):
    workflow_runs: tuple[GhRun, ...] = ()


class _GhRefObject(_GhBase):
    sha: str = ""


class GhGitRef(_GhBase):
    """A ``git/refs`` response (branch ref resolution + creation)."""

    ref: str = ""
    object: _GhRefObject | None = None


class _GhWriteContent(_GhBase):
    path: str = ""
    sha: str = ""


class _GhWriteCommit(_GhBase):
    sha: str = ""


class GhContentWrite(_GhBase):
    """A ``PUT /contents`` (create-or-update file) response."""

    content: _GhWriteContent | None = None
    commit: _GhWriteCommit | None = None


class _GhRepoPermissions(_GhBase):
    admin: bool = False
    push: bool = False
    pull: bool = False


class GhRepoPerm(_GhBase):
    """A repository list entry carrying the token's effective permission."""

    full_name: str = ""
    private: bool = False
    permissions: _GhRepoPermissions | None = None


def _login(user: _GhUser | None) -> str:
    return user.login if user is not None else ""


def repo_from(model: GhRepo) -> ForgeRepo:
    """Project a repository payload onto the domain descriptor.

    Returns:
        The vendor-neutral :class:`ForgeRepo` descriptor.

    Raises:
        GitBackendForgeApiError: When the payload lacks the identifying
            ``full_name`` / ``clone_url`` fields, or carries blank fields
            that fail the ``NotBlankStr`` constraint.
    """
    if not model.full_name or not model.clone_url:
        msg = "GitHub repo response missing 'full_name'/'clone_url'"
        raise GitBackendForgeApiError(msg)
    try:
        return ForgeRepo(
            full_name=NotBlankStr(model.full_name),
            default_branch=NotBlankStr(model.default_branch or "main"),
            private=model.private,
            clone_url=NotBlankStr(model.clone_url),
        )
    except ValidationError as exc:
        msg = "malformed GitHub repo response fields"
        raise GitBackendForgeApiError(msg) from exc


def file_content_from(model: GhContentFile, *, ref: str) -> ForgeFileContent:
    """Decode a base64 file payload into a domain model.

    Returns:
        The decoded :class:`ForgeFileContent`.

    Raises:
        GitBackendForgeApiError: When the payload is not a decodable
            base64 file (e.g. the path is a directory or a submodule), or
            carries fields that fail the ``ForgeFileContent`` constraints
            (e.g. a negative ``size``).
    """
    if model.encoding != "base64":
        msg = f"GitHub content at {model.path!r} is not a readable file"
        raise GitBackendForgeApiError(msg)
    try:
        raw = base64.b64decode(model.content)
    except (binascii.Error, ValueError) as exc:
        msg = f"GitHub content at {model.path!r} is not valid base64"
        raise GitBackendForgeApiError(msg) from exc
    try:
        return ForgeFileContent(
            path=NotBlankStr(model.path or "unknown"),
            ref=ref,
            content=raw.decode("utf-8", errors="replace"),
            size=model.size,
            sha=model.sha,
        )
    except ValidationError as exc:
        msg = f"GitHub content at {model.path!r} has malformed fields"
        raise GitBackendForgeApiError(msg) from exc


def dir_entry_from(model: GhContentEntry) -> ForgeDirEntry:
    kind: ForgeEntryKind = _DIR_KIND if model.type == _DIR_KIND else _FILE_KIND
    return ForgeDirEntry(
        name=NotBlankStr(model.name or "unknown"),
        path=NotBlankStr(model.path or model.name or "unknown"),
        kind=kind,
        size=model.size,
        sha=model.sha,
    )


def issue_from(model: GhIssue) -> ForgeIssue:
    # ``state`` is a documented open/closed enum on GitHub; the Literal on
    # ForgeIssue makes Pydantic validate it, so an impossible value fails
    # loud at construction. Map that to the typed forge error the client
    # contract promises rather than leaking a raw ValidationError.
    try:
        return ForgeIssue(
            number=model.number,
            title=model.title,
            state=cast("ForgeOpenClosedState", model.state),
            body=model.body or "",
            author=_login(model.user),
            url=model.html_url,
            labels=tuple(label.name for label in model.labels if label.name),
        )
    except ValidationError as exc:
        msg = f"GitHub issue #{model.number} has an unexpected state {model.state!r}"
        raise GitBackendForgeApiError(msg) from exc


def comment_from(model: GhComment) -> ForgeComment:
    return ForgeComment(
        id=model.id,
        author=_login(model.user),
        body=model.body,
        url=model.html_url,
    )


def pull_from(model: GhPull) -> ForgePullRequest:
    try:
        return ForgePullRequest(
            number=model.number,
            title=model.title,
            state=cast("ForgeOpenClosedState", model.state),
            body=model.body or "",
            author=_login(model.user),
            url=model.html_url,
            source_branch=model.head.ref if model.head is not None else "",
            target_branch=model.base.ref if model.base is not None else "",
            draft=model.draft,
            merged=model.merged,
        )
    except ValidationError as exc:
        msg = f"GitHub pull #{model.number} has an unexpected state {model.state!r}"
        raise GitBackendForgeApiError(msg) from exc


def review_from(model: GhReview, *, comment_count: int = 0) -> ForgeReview:
    return ForgeReview(
        id=model.id,
        state=model.state,
        author=_login(model.user),
        body=model.body or "",
        url=model.html_url,
        comment_count=comment_count,
    )


def branch_ref_from(model: GhGitRef, *, name: str) -> ForgeBranchRef:
    """Project a ``git/refs`` payload onto the domain branch ref.

    Returns:
        The created :class:`ForgeBranchRef`.

    Raises:
        GitBackendForgeApiError: When the payload carries no object sha.
    """
    sha = model.object.sha if model.object is not None else ""
    if not sha:
        msg = "GitHub git-ref response carried no object sha"
        raise GitBackendForgeApiError(msg)
    return ForgeBranchRef(name=NotBlankStr(name), sha=sha)


def file_commit_from(model: GhContentWrite, *, branch: str) -> ForgeFileCommit:
    """Project a ``PUT /contents`` payload onto the domain file commit.

    Returns:
        The resulting :class:`ForgeFileCommit`.

    Raises:
        GitBackendForgeApiError: When the payload lacks the content path
            or the commit sha.
    """
    path = model.content.path if model.content is not None else ""
    content_sha = model.content.sha if model.content is not None else ""
    commit_sha = model.commit.sha if model.commit is not None else ""
    if not path or not commit_sha:
        msg = "GitHub content-write response missing path/commit sha"
        raise GitBackendForgeApiError(msg)
    return ForgeFileCommit(
        path=NotBlankStr(path),
        branch=NotBlankStr(branch),
        content_sha=content_sha,
        commit_sha=commit_sha,
    )


def accessible_repo_from(model: GhRepoPerm) -> ForgeAccessibleRepo | None:
    """Project a repo-list entry onto the domain accessible-repo model.

    Returns:
        The :class:`ForgeAccessibleRepo`, or ``None`` when the entry
        carries no parseable ``owner/repo`` full name (skipped, not
        fatal, so one odd row cannot abort scope discovery).
    """
    owner, _, repo = model.full_name.partition("/")
    if not owner or not repo:
        return None
    perms = model.permissions
    permission: ForgeRepoPermission = "read"
    if perms is not None and perms.admin:
        permission = "admin"
    elif perms is not None and perms.push:
        permission = "write"
    return ForgeAccessibleRepo(
        owner=NotBlankStr(owner),
        repo=NotBlankStr(repo),
        permission=permission,
        private=model.private,
    )


def merge_from(model: GhMerge) -> ForgeMergeResult:
    return ForgeMergeResult(merged=model.merged, sha=model.sha, message=model.message)


def run_from(model: GhRun) -> ForgeCiRun:
    return ForgeCiRun(
        id=model.id,
        name=model.name,
        status=model.status,
        conclusion=model.conclusion or "",
        branch=model.head_branch,
        commit_sha=model.head_sha,
        url=model.html_url,
    )


__all__ = [
    "GhComment",
    "GhContentEntry",
    "GhContentFile",
    "GhContentWrite",
    "GhGitRef",
    "GhIssue",
    "GhMerge",
    "GhPull",
    "GhRepo",
    "GhRepoPerm",
    "GhReview",
    "GhRun",
    "GhRunList",
    "accessible_repo_from",
    "branch_ref_from",
    "comment_from",
    "dir_entry_from",
    "file_commit_from",
    "file_content_from",
    "issue_from",
    "merge_from",
    "parse_github",
    "pull_from",
    "repo_from",
    "review_from",
    "run_from",
]
