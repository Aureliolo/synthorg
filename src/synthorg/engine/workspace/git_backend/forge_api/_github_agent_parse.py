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

from pydantic import BaseModel, ConfigDict, ValidationError

from synthorg.core.types import NotBlankStr
from synthorg.engine.errors import GitBackendForgeApiError
from synthorg.engine.workspace.git_backend.forge_api.agent_models import (
    ForgeCiRun,
    ForgeComment,
    ForgeDirEntry,
    ForgeEntryKind,
    ForgeFileContent,
    ForgeIssue,
    ForgeMergeResult,
    ForgePullRequest,
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


def _login(user: _GhUser | None) -> str:
    return user.login if user is not None else ""


def repo_from(model: GhRepo) -> ForgeRepo:
    """Project a repository payload onto the domain descriptor.

    Returns:
        The vendor-neutral :class:`ForgeRepo` descriptor.

    Raises:
        GitBackendForgeApiError: When the payload lacks the identifying
            ``full_name`` / ``clone_url`` fields.
    """
    if not model.full_name or not model.clone_url:
        msg = "GitHub repo response missing 'full_name'/'clone_url'"
        raise GitBackendForgeApiError(msg)
    return ForgeRepo(
        full_name=NotBlankStr(model.full_name),
        default_branch=NotBlankStr(model.default_branch or "main"),
        private=model.private,
        clone_url=NotBlankStr(model.clone_url),
    )


def file_content_from(model: GhContentFile, *, ref: str) -> ForgeFileContent:
    """Decode a base64 file payload into a domain model.

    Returns:
        The decoded :class:`ForgeFileContent`.

    Raises:
        GitBackendForgeApiError: When the payload is not a decodable
            base64 file (e.g. the path is a directory or a submodule).
    """
    if model.encoding != "base64":
        msg = f"GitHub content at {model.path!r} is not a readable file"
        raise GitBackendForgeApiError(msg)
    try:
        raw = base64.b64decode(model.content)
    except (binascii.Error, ValueError) as exc:
        msg = f"GitHub content at {model.path!r} is not valid base64"
        raise GitBackendForgeApiError(msg) from exc
    return ForgeFileContent(
        path=NotBlankStr(model.path or "unknown"),
        ref=ref,
        content=raw.decode("utf-8", errors="replace"),
        size=model.size,
        sha=model.sha,
    )


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
    return ForgeIssue(
        number=model.number,
        title=model.title,
        state=model.state,
        body=model.body or "",
        author=_login(model.user),
        url=model.html_url,
        labels=tuple(label.name for label in model.labels if label.name),
    )


def comment_from(model: GhComment) -> ForgeComment:
    return ForgeComment(
        id=model.id,
        author=_login(model.user),
        body=model.body,
        url=model.html_url,
    )


def pull_from(model: GhPull) -> ForgePullRequest:
    return ForgePullRequest(
        number=model.number,
        title=model.title,
        state=model.state,
        body=model.body or "",
        author=_login(model.user),
        url=model.html_url,
        source_branch=model.head.ref if model.head is not None else "",
        target_branch=model.base.ref if model.base is not None else "",
        draft=model.draft,
        merged=model.merged,
    )


def review_from(model: GhReview) -> ForgeReview:
    return ForgeReview(
        id=model.id,
        state=model.state,
        author=_login(model.user),
        body=model.body or "",
        url=model.html_url,
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
    "GhIssue",
    "GhMerge",
    "GhPull",
    "GhRepo",
    "GhReview",
    "GhRun",
    "GhRunList",
    "comment_from",
    "dir_entry_from",
    "file_content_from",
    "issue_from",
    "merge_from",
    "parse_github",
    "pull_from",
    "repo_from",
    "review_from",
    "run_from",
]
