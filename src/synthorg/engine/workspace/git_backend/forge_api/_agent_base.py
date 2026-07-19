"""Shared base for the agent-operations forge clients.

GitHub and the Gitea family (Gitea + Forgejo) expose a GitHub-compatible
*read* surface (repository contents, issues, pull requests share the
same endpoints and response shapes), so those operations live here once,
parameterised only by the list pagination key (``per_page`` vs
``limit``). The *write* surface diverges (review event vocabulary, merge
payload + response, label handling, CI availability), so those are
abstract and implemented per forge.
"""

from abc import ABC, abstractmethod
from typing import ClassVar
from urllib.parse import quote

import synthorg.engine.workspace.git_backend.forge_api._github_agent_parse as gh
from synthorg.core.types import NotBlankStr
from synthorg.engine.errors import GitBackendForgeApiError
from synthorg.engine.workspace.git_backend.forge_api._base import BaseForgeClient
from synthorg.engine.workspace.git_backend.forge_api._http import raise_for_forge_status
from synthorg.engine.workspace.git_backend.forge_api.agent_models import (
    ForgeCiRun,
    ForgeComment,
    ForgeDirEntry,
    ForgeFileContent,
    ForgeIssue,
    ForgeIssueState,
    ForgeMergeMethod,
    ForgeMergeResult,
    ForgePullRequest,
    ForgePullState,
    ForgeReview,
    ForgeReviewDecision,
)
from synthorg.engine.workspace.git_backend.forge_api.protocol import ForgeRepo
from synthorg.observability import get_logger
from synthorg.observability.events.workspace import (
    FORGE_API_ISSUE_COMMENTED,
    FORGE_API_PULL_REQUEST_COMMENTED,
)

logger = get_logger(__name__)


class ForgeAgentBase(BaseForgeClient, ABC):
    """Read surface shared by the GitHub + Gitea-family agent clients.

    Subclasses set the auth headers (via their provisioning-client base)
    and ``_LIST_PARAM``, and implement the diverging write + CI surface.
    """

    _LIST_PARAM: ClassVar[str]

    async def get_repo(self, *, owner: NotBlankStr, repo: NotBlankStr) -> ForgeRepo:
        action = f"read repo {owner}/{repo}"
        resp = await self._request("GET", f"/repos/{owner}/{repo}", action=action)
        raise_for_forge_status(resp, action=action)
        return gh.repo_from(gh.parse_github(resp.json(), gh.GhRepo, what="repo"))

    async def read_file(
        self,
        *,
        owner: NotBlankStr,
        repo: NotBlankStr,
        path: NotBlankStr,
        ref: str | None = None,
    ) -> ForgeFileContent:
        action = f"read file {owner}/{repo}/{path}"
        resp = await self._request(
            "GET",
            f"/repos/{owner}/{repo}/contents/{quote(str(path), safe='/')}",
            action=action,
            params={"ref": ref} if ref else None,
        )
        raise_for_forge_status(resp, action=action)
        payload = gh.parse_github(resp.json(), gh.GhContentFile, what="file")
        return gh.file_content_from(payload, ref=ref or "")

    async def list_dir(
        self,
        *,
        owner: NotBlankStr,
        repo: NotBlankStr,
        path: str = "",
        ref: str | None = None,
    ) -> tuple[ForgeDirEntry, ...]:
        action = f"list dir {owner}/{repo}/{path}"
        resp = await self._request(
            "GET",
            f"/repos/{owner}/{repo}/contents/{quote(path, safe='/')}",
            action=action,
            params={"ref": ref} if ref else None,
        )
        raise_for_forge_status(resp, action=action)
        return tuple(
            gh.dir_entry_from(
                gh.parse_github(item, gh.GhContentEntry, what="dir entry")
            )
            for item in _as_list(resp.json())
        )

    async def get_issue(
        self, *, owner: NotBlankStr, repo: NotBlankStr, number: int
    ) -> ForgeIssue:
        action = f"read issue {owner}/{repo}#{number}"
        resp = await self._request(
            "GET", f"/repos/{owner}/{repo}/issues/{number}", action=action
        )
        raise_for_forge_status(resp, action=action)
        return gh.issue_from(gh.parse_github(resp.json(), gh.GhIssue, what="issue"))

    async def list_issues(
        self,
        *,
        owner: NotBlankStr,
        repo: NotBlankStr,
        state: ForgeIssueState = "open",
        limit: int,
    ) -> tuple[ForgeIssue, ...]:
        action = f"list issues {owner}/{repo}"
        resp = await self._request(
            "GET",
            f"/repos/{owner}/{repo}/issues",
            action=action,
            params={"state": state, self._LIST_PARAM: limit, "type": "issues"},
        )
        raise_for_forge_status(resp, action=action)
        parsed = (
            gh.parse_github(item, gh.GhIssue, what="issue")
            for item in _as_list(resp.json())
        )
        # GitHub's issues endpoint returns pull requests too; drop them.
        return tuple(gh.issue_from(m) for m in parsed if m.pull_request is None)

    async def comment_issue(
        self, *, owner: NotBlankStr, repo: NotBlankStr, number: int, body: NotBlankStr
    ) -> ForgeComment:
        comment = await self._post_comment(
            owner=owner, repo=repo, number=number, body=body
        )
        logger.info(FORGE_API_ISSUE_COMMENTED, number=number)
        return comment

    async def get_pull_request(
        self, *, owner: NotBlankStr, repo: NotBlankStr, number: int
    ) -> ForgePullRequest:
        action = f"read pull request {owner}/{repo}#{number}"
        resp = await self._request(
            "GET", f"/repos/{owner}/{repo}/pulls/{number}", action=action
        )
        raise_for_forge_status(resp, action=action)
        return gh.pull_from(
            gh.parse_github(resp.json(), gh.GhPull, what="pull request")
        )

    async def list_pull_requests(
        self,
        *,
        owner: NotBlankStr,
        repo: NotBlankStr,
        state: ForgePullState = "open",
        limit: int,
    ) -> tuple[ForgePullRequest, ...]:
        action = f"list pull requests {owner}/{repo}"
        resp = await self._request(
            "GET",
            f"/repos/{owner}/{repo}/pulls",
            action=action,
            params={"state": state, self._LIST_PARAM: limit},
        )
        raise_for_forge_status(resp, action=action)
        return tuple(
            gh.pull_from(gh.parse_github(item, gh.GhPull, what="pull request"))
            for item in _as_list(resp.json())
        )

    async def comment_pull_request(
        self, *, owner: NotBlankStr, repo: NotBlankStr, number: int, body: NotBlankStr
    ) -> ForgeComment:
        # A pull request shares the issue-comment endpoint on both forges.
        comment = await self._post_comment(
            owner=owner, repo=repo, number=number, body=body
        )
        logger.info(FORGE_API_PULL_REQUEST_COMMENTED, number=number)
        return comment

    async def _post_comment(
        self, *, owner: NotBlankStr, repo: NotBlankStr, number: int, body: NotBlankStr
    ) -> ForgeComment:
        action = f"comment on {owner}/{repo}#{number}"
        resp = await self._request(
            "POST",
            f"/repos/{owner}/{repo}/issues/{number}/comments",
            action=action,
            json={"body": str(body)},
        )
        raise_for_forge_status(resp, action=action)
        return gh.comment_from(
            gh.parse_github(resp.json(), gh.GhComment, what="comment")
        )

    @abstractmethod
    async def create_issue(
        self,
        *,
        owner: NotBlankStr,
        repo: NotBlankStr,
        title: NotBlankStr,
        body: str = "",
        labels: tuple[str, ...] = (),
    ) -> ForgeIssue:
        """Open a new issue and return it."""

    @abstractmethod
    async def create_pull_request(  # noqa: PLR0913 -- forge PR fields
        self,
        *,
        owner: NotBlankStr,
        repo: NotBlankStr,
        title: NotBlankStr,
        source_branch: NotBlankStr,
        target_branch: NotBlankStr,
        body: str = "",
        draft: bool = False,
    ) -> ForgePullRequest:
        """Open a pull request from ``source_branch`` into ``target_branch``."""

    @abstractmethod
    async def review_pull_request(
        self,
        *,
        owner: NotBlankStr,
        repo: NotBlankStr,
        number: int,
        decision: ForgeReviewDecision,
        body: str = "",
    ) -> ForgeReview:
        """Submit a review (approve / request changes / comment)."""

    @abstractmethod
    async def merge_pull_request(
        self,
        *,
        owner: NotBlankStr,
        repo: NotBlankStr,
        number: int,
        method: ForgeMergeMethod = "merge",
        commit_title: str = "",
    ) -> ForgeMergeResult:
        """Merge a pull request and return the outcome."""

    @abstractmethod
    async def list_ci_runs(
        self,
        *,
        owner: NotBlankStr,
        repo: NotBlankStr,
        branch: str | None = None,
        limit: int,
    ) -> tuple[ForgeCiRun, ...]:
        """List CI runs, most-recent first."""

    @abstractmethod
    async def get_ci_run(
        self, *, owner: NotBlankStr, repo: NotBlankStr, run_id: int
    ) -> ForgeCiRun:
        """Return a single CI run by id."""


def _as_list(data: object) -> list[object]:
    """Return ``data`` as a list or raise a typed forge error.

    Raises:
        GitBackendForgeApiError: When the forge returned a non-array
            where a collection was expected (e.g. a file path passed to
            a directory listing).
    """
    if isinstance(data, list):
        return data
    msg = "expected a forge collection response but got a single object"
    raise GitBackendForgeApiError(msg)


__all__ = ["ForgeAgentBase"]
