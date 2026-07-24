"""GitLab REST payload models + mappers for the agent-operations client.

GitLab's v4 API diverges from the GitHub-compatible surface the other
forges share: repositories are addressed by a URL-encoded
``namespace/project`` path, issues and merge requests are keyed by a
per-project ``iid``, and permissions come as numeric access levels. Kept
separate from ``gitlab_agent`` so the client module stays within its size
budget. Payload models are ``extra="ignore"``; the mappers project them
onto the vendor-neutral :mod:`agent_models` domain shapes. A malformed
payload surfaces as :class:`GitBackendForgeApiError` via
:func:`parse_gitlab`.
"""

import base64
import binascii
from typing import Final, cast

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
    ForgeReviewComment,
)
from synthorg.engine.workspace.git_backend.forge_api.protocol import ForgeRepo

# GitLab numeric access levels: >= 40 (maintainer/owner) is admin,
# >= 30 (developer) can push, anything lower is read-only.
_ACCESS_ADMIN: Final[int] = 40
_ACCESS_WRITE: Final[int] = 30


def parse_gitlab[M: BaseModel](data: object, model: type[M], *, what: str) -> M:
    """Validate ``data`` against ``model`` or raise a typed forge error.

    Returns:
        The validated model instance.

    Raises:
        GitBackendForgeApiError: When ``data`` does not satisfy ``model``.
    """
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        msg = f"malformed GitLab {what} response"
        raise GitBackendForgeApiError(msg) from exc


class _GlBase(BaseModel):  # lint-allow: frozen-extra-forbid -- forge extras
    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="ignore")


class _GlUser(_GlBase):
    username: str = ""


class _GlAccess(_GlBase):
    access_level: int = 0


class _GlPermissions(_GlBase):
    project_access: _GlAccess | None = None
    group_access: _GlAccess | None = None


class GlProject(_GlBase):
    """A project descriptor (repo)."""

    path_with_namespace: str = ""
    default_branch: str = "main"
    visibility: str = ""
    http_url_to_repo: str = ""
    permissions: _GlPermissions | None = None


class GlFile(_GlBase):
    """A ``repository/files`` GET response (base64 content)."""

    file_path: str = ""
    ref: str = ""
    content: str = ""
    encoding: str = ""
    blob_id: str = ""
    size: int = 0


class GlFileWrite(_GlBase):
    """A ``repository/files`` PUT response (create-or-update)."""

    file_path: str = ""
    branch: str = ""


class _GlCommit(_GlBase):
    id: str = ""


class GlBranch(_GlBase):
    """A ``repository/branches`` response ``{name, commit: {id}}``."""

    name: str = ""
    commit: _GlCommit | None = None


class GlIssue(_GlBase):
    iid: int = 0
    title: str = ""
    state: str = ""
    description: str | None = None
    author: _GlUser | None = None
    web_url: str = ""
    labels: tuple[str, ...] = ()


class GlNote(_GlBase):
    id: int = 0
    body: str = ""
    author: _GlUser | None = None


class GlDiffRefs(_GlBase):
    base_sha: str = ""
    head_sha: str = ""
    start_sha: str = ""


class GlMergeRequest(_GlBase):
    iid: int = 0
    title: str = ""
    state: str = ""
    description: str | None = None
    author: _GlUser | None = None
    web_url: str = ""
    source_branch: str = ""
    target_branch: str = ""
    draft: bool = False
    diff_refs: GlDiffRefs | None = None
    merge_commit_sha: str | None = None


class GlPipeline(_GlBase):
    id: int = 0
    status: str = ""
    ref: str = ""
    sha: str = ""
    web_url: str = ""


class GlTreeEntry(_GlBase):
    """One entry in a ``repository/tree`` listing."""

    id: str = ""
    name: str = ""
    type: str = ""
    path: str = ""


_TREE_DIR: Final[str] = "tree"
_FILE_KIND: ForgeEntryKind = "file"
_DIR_KIND: ForgeEntryKind = "dir"


def dir_entry_from(model: GlTreeEntry) -> ForgeDirEntry:
    kind: ForgeEntryKind = _DIR_KIND if model.type == _TREE_DIR else _FILE_KIND
    return ForgeDirEntry(
        name=NotBlankStr(model.name or "unknown"),
        path=NotBlankStr(model.path or model.name or "unknown"),
        kind=kind,
        size=0,
        sha=model.id,
    )


def merge_result_from(model: GlMergeRequest) -> ForgeMergeResult:
    sha = model.merge_commit_sha or ""
    return ForgeMergeResult(
        merged=bool(sha) or model.state == "merged",
        sha=sha,
        message="merged" if sha else model.state,
    )


def discussion_position(
    comment: ForgeReviewComment, refs: GlDiffRefs
) -> dict[str, object]:
    """Build a GitLab discussion ``position`` for an inline comment.

    ``RIGHT`` anchors to the head version (``new_path``/``new_line``);
    ``LEFT`` anchors to the base (``old_path``/``old_line``).

    Returns:
        The ``position`` object for a merge-request discussion.
    """
    position: dict[str, object] = {
        "base_sha": refs.base_sha,
        "start_sha": refs.start_sha,
        "head_sha": refs.head_sha,
        "position_type": "text",
    }
    if comment.side == "RIGHT":
        position["new_path"] = str(comment.path)
        position["new_line"] = comment.line
    else:
        position["old_path"] = str(comment.path)
        position["old_line"] = comment.line
    return position


def _login(user: _GlUser | None) -> str:
    return user.username if user is not None else ""


def _open_closed(state: str) -> str:
    # GitLab uses "opened"; every other state (closed, merged, locked) folds
    # to "closed" in the vendor-neutral open/closed view.
    return "open" if state == "opened" else "closed"


def repo_from(model: GlProject) -> ForgeRepo:
    """Project a project payload onto the domain repo descriptor.

    Returns:
        The vendor-neutral :class:`ForgeRepo` descriptor.

    Raises:
        GitBackendForgeApiError: When the payload lacks the identifying
            path / clone url.
    """
    if not model.path_with_namespace or not model.http_url_to_repo:
        msg = "GitLab project response missing path/clone url"
        raise GitBackendForgeApiError(msg)
    return ForgeRepo(
        full_name=NotBlankStr(model.path_with_namespace),
        default_branch=NotBlankStr(model.default_branch or "main"),
        private=model.visibility != "public",
        clone_url=NotBlankStr(model.http_url_to_repo),
    )


def _permission_of(model: GlProject) -> ForgeRepoPermission:
    perms = model.permissions
    level = 0
    if perms is not None:
        project = perms.project_access.access_level if perms.project_access else 0
        group = perms.group_access.access_level if perms.group_access else 0
        level = max(project, group)
    if level >= _ACCESS_ADMIN:
        return "admin"
    if level >= _ACCESS_WRITE:
        return "write"
    return "read"


def accessible_repo_from(model: GlProject) -> ForgeAccessibleRepo | None:
    """Project a project entry onto the domain accessible-repo model.

    Returns:
        The :class:`ForgeAccessibleRepo`, or ``None`` when the entry
        carries no parseable ``owner/repo`` path (skipped, not fatal).
    """
    owner, _, repo = model.path_with_namespace.rpartition("/")
    if not owner or not repo:
        return None
    return ForgeAccessibleRepo(
        owner=NotBlankStr(owner),
        repo=NotBlankStr(repo),
        permission=_permission_of(model),
        private=model.visibility != "public",
    )


def file_content_from(model: GlFile, *, ref: str) -> ForgeFileContent:
    """Decode a base64 file payload into a domain model.

    Returns:
        The decoded :class:`ForgeFileContent`.

    Raises:
        GitBackendForgeApiError: When the payload is not decodable base64.
    """
    if model.encoding != "base64":
        msg = f"GitLab content at {model.file_path!r} is not a readable file"
        raise GitBackendForgeApiError(msg)
    try:
        raw = base64.b64decode(model.content)
    except (binascii.Error, ValueError) as exc:
        msg = f"GitLab content at {model.file_path!r} is not valid base64"
        raise GitBackendForgeApiError(msg) from exc
    return ForgeFileContent(
        path=NotBlankStr(model.file_path or "unknown"),
        ref=ref or model.ref,
        content=raw.decode("utf-8", errors="replace"),
        size=model.size,
        sha=model.blob_id,
    )


def file_commit_from(model: GlFileWrite, *, commit_sha: str) -> ForgeFileCommit:
    """Project a ``files`` PUT payload + resolved commit sha to the domain.

    GitLab's files PUT returns only ``{file_path, branch}``, so the head
    commit sha is resolved by the caller and passed in.

    Returns:
        The resulting :class:`ForgeFileCommit`.

    Raises:
        GitBackendForgeApiError: When the payload lacks path or branch.
    """
    if not model.file_path or not model.branch:
        msg = "GitLab file-write response missing path/branch"
        raise GitBackendForgeApiError(msg)
    return ForgeFileCommit(
        path=NotBlankStr(model.file_path),
        branch=NotBlankStr(model.branch),
        content_sha="",
        commit_sha=commit_sha,
    )


def branch_ref_from(model: GlBranch, *, name: str) -> ForgeBranchRef:
    """Project a ``branches`` payload onto the domain branch ref.

    Returns:
        The created :class:`ForgeBranchRef`.

    Raises:
        GitBackendForgeApiError: When the payload carries no commit id.
    """
    sha = model.commit.id if model.commit is not None else ""
    if not sha:
        msg = "GitLab branch response carried no commit id"
        raise GitBackendForgeApiError(msg)
    return ForgeBranchRef(name=NotBlankStr(name or model.name or "unknown"), sha=sha)


def issue_from(model: GlIssue) -> ForgeIssue:
    """Project a GitLab issue payload onto the domain issue.

    Returns:
        The vendor-neutral :class:`ForgeIssue`.

    Raises:
        GitBackendForgeApiError: When the issue carries fields that fail
            the domain constraints (e.g. a non-positive iid).
    """
    try:
        return ForgeIssue(
            number=model.iid,
            title=model.title,
            state=cast("ForgeOpenClosedState", _open_closed(model.state)),
            body=model.description or "",
            author=_login(model.author),
            url=model.web_url,
            labels=tuple(model.labels),
        )
    except ValidationError as exc:
        msg = f"GitLab issue !{model.iid} has malformed fields"
        raise GitBackendForgeApiError(msg) from exc


def comment_from(model: GlNote) -> ForgeComment:
    return ForgeComment(
        id=model.id,
        author=_login(model.author),
        body=model.body,
        url="",
    )


def pull_from(model: GlMergeRequest, *, merged: bool = False) -> ForgePullRequest:
    """Project a GitLab merge request onto the domain pull request.

    Returns:
        The vendor-neutral :class:`ForgePullRequest`.

    Raises:
        GitBackendForgeApiError: When the MR carries malformed fields.
    """
    try:
        return ForgePullRequest(
            number=model.iid,
            title=model.title,
            state=cast("ForgeOpenClosedState", _open_closed(model.state)),
            body=model.description or "",
            author=_login(model.author),
            url=model.web_url,
            source_branch=model.source_branch,
            target_branch=model.target_branch,
            draft=model.draft,
            merged=merged or model.state == "merged",
        )
    except ValidationError as exc:
        msg = f"GitLab merge request !{model.iid} has malformed fields"
        raise GitBackendForgeApiError(msg) from exc


def pipeline_from(model: GlPipeline) -> ForgeCiRun:
    return ForgeCiRun(
        id=model.id,
        name=f"pipeline {model.id}",
        status=model.status,
        conclusion=model.status,
        branch=model.ref,
        commit_sha=model.sha,
        url=model.web_url,
    )


__all__ = [
    "GlBranch",
    "GlDiffRefs",
    "GlFile",
    "GlFileWrite",
    "GlIssue",
    "GlMergeRequest",
    "GlNote",
    "GlPipeline",
    "GlProject",
    "GlTreeEntry",
    "accessible_repo_from",
    "branch_ref_from",
    "comment_from",
    "dir_entry_from",
    "discussion_position",
    "file_commit_from",
    "file_content_from",
    "issue_from",
    "merge_result_from",
    "parse_gitlab",
    "pipeline_from",
    "pull_from",
    "repo_from",
]
