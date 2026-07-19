"""GitHub agent-operations forge client.

Implements the diverging write + CI surface for GitHub on top of the
shared :class:`ForgeAgentBase` read surface, reusing the GitHub auth +
accept headers from the provisioning :class:`GitHubForgeClient`. Payload
parsing lives in :mod:`_github_agent_parse`.
"""

from collections.abc import Mapping
from typing import Final, override

import synthorg.engine.workspace.git_backend.forge_api._github_agent_parse as gh
from synthorg.core.types import NotBlankStr
from synthorg.engine.workspace.git_backend.forge_api._agent_base import ForgeAgentBase
from synthorg.engine.workspace.git_backend.forge_api._http import raise_for_forge_status
from synthorg.engine.workspace.git_backend.forge_api.agent_models import (
    ForgeCiRun,
    ForgeIssue,
    ForgeMergeMethod,
    ForgeMergeResult,
    ForgePullRequest,
    ForgeReview,
    ForgeReviewDecision,
)
from synthorg.engine.workspace.git_backend.forge_api.github import GitHubForgeClient
from synthorg.observability import get_logger
from synthorg.observability.events.workspace import (
    FORGE_API_ISSUE_OPENED,
    FORGE_API_PULL_REQUEST_MERGED,
    FORGE_API_PULL_REQUEST_OPENED,
    FORGE_API_PULL_REQUEST_REVIEWED,
)

logger = get_logger(__name__)

_REVIEW_EVENTS: Final[Mapping[str, str]] = {
    "approve": "APPROVE",
    "request_changes": "REQUEST_CHANGES",
    "comment": "COMMENT",
}


class GitHubAgentForgeClient(GitHubForgeClient, ForgeAgentBase):
    """Read/write GitHub REST client for the agent-facing forge tools."""

    _LIST_PARAM = "per_page"

    @override
    async def create_issue(
        self,
        *,
        owner: NotBlankStr,
        repo: NotBlankStr,
        title: NotBlankStr,
        body: str = "",
        labels: tuple[str, ...] = (),
    ) -> ForgeIssue:
        """Open a new issue and return it.

        Returns:
            The created issue.
        """
        action = f"open issue in {owner}/{repo}"
        resp = await self._request(
            "POST",
            f"/repos/{owner}/{repo}/issues",
            action=action,
            json={"title": str(title), "body": body, "labels": list(labels)},
        )
        raise_for_forge_status(resp, action=action)
        issue = gh.issue_from(gh.parse_github(resp.json(), gh.GhIssue, what="issue"))
        logger.info(FORGE_API_ISSUE_OPENED, number=issue.number)
        return issue

    @override
    async def create_pull_request(
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
        """Open a pull request from ``source_branch`` into ``target_branch``.

        Returns:
            The created pull request.
        """
        action = f"open pull request in {owner}/{repo}"
        resp = await self._request(
            "POST",
            f"/repos/{owner}/{repo}/pulls",
            action=action,
            json={
                "title": str(title),
                "head": str(source_branch),
                "base": str(target_branch),
                "body": body,
                "draft": draft,
            },
        )
        raise_for_forge_status(resp, action=action)
        pull = gh.pull_from(
            gh.parse_github(resp.json(), gh.GhPull, what="pull request")
        )
        logger.info(FORGE_API_PULL_REQUEST_OPENED, number=pull.number)
        return pull

    @override
    async def review_pull_request(
        self,
        *,
        owner: NotBlankStr,
        repo: NotBlankStr,
        number: int,
        decision: ForgeReviewDecision,
        body: str = "",
    ) -> ForgeReview:
        """Submit a review (approve / request changes / comment).

        Returns:
            The submitted review.
        """
        action = f"review pull request {owner}/{repo}#{number}"
        resp = await self._request(
            "POST",
            f"/repos/{owner}/{repo}/pulls/{number}/reviews",
            action=action,
            json={"event": _REVIEW_EVENTS[decision], "body": body},
        )
        raise_for_forge_status(resp, action=action)
        review = gh.review_from(
            gh.parse_github(resp.json(), gh.GhReview, what="review")
        )
        logger.info(FORGE_API_PULL_REQUEST_REVIEWED, number=number, decision=decision)
        return review

    @override
    async def merge_pull_request(
        self,
        *,
        owner: NotBlankStr,
        repo: NotBlankStr,
        number: int,
        method: ForgeMergeMethod = "merge",
        commit_title: str = "",
    ) -> ForgeMergeResult:
        """Merge a pull request and return the outcome.

        Returns:
            The merge outcome (merged flag + resulting commit sha).
        """
        action = f"merge pull request {owner}/{repo}#{number}"
        payload: dict[str, object] = {"merge_method": method}
        if commit_title:
            payload["commit_title"] = commit_title
        resp = await self._request(
            "PUT",
            f"/repos/{owner}/{repo}/pulls/{number}/merge",
            action=action,
            json=payload,
        )
        raise_for_forge_status(resp, action=action)
        result = gh.merge_from(gh.parse_github(resp.json(), gh.GhMerge, what="merge"))
        logger.info(FORGE_API_PULL_REQUEST_MERGED, number=number, merged=result.merged)
        return result

    @override
    async def list_ci_runs(
        self,
        *,
        owner: NotBlankStr,
        repo: NotBlankStr,
        branch: str | None = None,
        limit: int,
    ) -> tuple[ForgeCiRun, ...]:
        """List CI runs, most-recent first.

        Returns:
            The matching CI runs.
        """
        action = f"list CI runs {owner}/{repo}"
        params: dict[str, str | int] = {"per_page": limit}
        if branch:
            params["branch"] = branch
        resp = await self._request(
            "GET", f"/repos/{owner}/{repo}/actions/runs", action=action, params=params
        )
        raise_for_forge_status(resp, action=action)
        runs = gh.parse_github(resp.json(), gh.GhRunList, what="CI runs")
        return tuple(gh.run_from(run) for run in runs.workflow_runs)

    @override
    async def get_ci_run(
        self, *, owner: NotBlankStr, repo: NotBlankStr, run_id: int
    ) -> ForgeCiRun:
        """Return a single CI run by id.

        Returns:
            The CI run.
        """
        action = f"read CI run {owner}/{repo}#{run_id}"
        resp = await self._request(
            "GET", f"/repos/{owner}/{repo}/actions/runs/{run_id}", action=action
        )
        raise_for_forge_status(resp, action=action)
        return gh.run_from(gh.parse_github(resp.json(), gh.GhRun, what="CI run"))


__all__ = ["GitHubAgentForgeClient"]
