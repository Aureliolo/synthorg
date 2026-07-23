"""GitHub agent-operations forge client.

Implements the diverging write + CI surface for GitHub on top of the
shared :class:`ForgeAgentBase` read surface, reusing the GitHub auth +
accept headers from the provisioning :class:`GitHubForgeClient`. Payload
parsing lives in :mod:`_github_agent_parse`.
"""

from collections.abc import Mapping
from typing import Final, override
from urllib.parse import quote

import synthorg.engine.workspace.git_backend.forge_api._github_agent_parse as gh
from synthorg.core.types import NotBlankStr
from synthorg.engine.workspace.git_backend.forge_api._agent_base import ForgeAgentBase
from synthorg.engine.workspace.git_backend.forge_api._http import raise_for_forge_status
from synthorg.engine.workspace.git_backend.forge_api.agent_models import (
    ForgeBranchRef,
    ForgeCiRun,
    ForgeCiTrigger,
    ForgeIssue,
    ForgeMergeMethod,
    ForgeMergeResult,
    ForgePullRequest,
    ForgeReview,
    ForgeReviewComment,
    ForgeReviewDecision,
)
from synthorg.engine.workspace.git_backend.forge_api.github import GitHubForgeClient
from synthorg.observability import get_logger
from synthorg.observability.events.workspace import (
    FORGE_API_BRANCH_CREATED,
    FORGE_API_CI_TRIGGERED,
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


def _review_comment_payload(comment: ForgeReviewComment) -> dict[str, object]:
    """Project an inline review comment onto the GitHub reviews payload.

    Returns:
        The ``comments[]`` entry for the reviews API.
    """
    return {
        "path": str(comment.path),
        "line": comment.line,
        "side": comment.side,
        "body": str(comment.body),
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
    async def create_branch(
        self,
        *,
        owner: NotBlankStr,
        repo: NotBlankStr,
        new_branch: NotBlankStr,
        from_ref: NotBlankStr,
    ) -> ForgeBranchRef:
        """Create ``new_branch`` at ``from_ref``'s commit and return it.

        GitHub has no one-shot branch create: resolve the source ref's
        commit sha, then create the new ref pointing at it.

        Returns:
            The created branch ref.
        """
        resolve = f"resolve ref {owner}/{repo}@{from_ref}"
        ref_resp = await self._request(
            "GET",
            f"/repos/{owner}/{repo}/git/ref/heads/{quote(str(from_ref), safe='')}",
            action=resolve,
        )
        raise_for_forge_status(ref_resp, action=resolve)
        source = gh.parse_github(ref_resp.json(), gh.GhGitRef, what="git ref")
        source_sha = source.object.sha if source.object is not None else ""
        action = f"create branch {owner}/{repo}@{new_branch}"
        resp = await self._request(
            "POST",
            f"/repos/{owner}/{repo}/git/refs",
            action=action,
            json={"ref": f"refs/heads/{new_branch}", "sha": source_sha},
        )
        raise_for_forge_status(resp, action=action)
        branch = gh.branch_ref_from(
            gh.parse_github(resp.json(), gh.GhGitRef, what="git ref"),
            name=str(new_branch),
        )
        logger.info(FORGE_API_BRANCH_CREATED, branch=str(new_branch))
        return branch

    @override
    async def review_pull_request(
        self,
        *,
        owner: NotBlankStr,
        repo: NotBlankStr,
        number: int,
        decision: ForgeReviewDecision,
        body: str = "",
        comments: tuple[ForgeReviewComment, ...] = (),
    ) -> ForgeReview:
        """Submit a review (approve / request changes / comment).

        Returns:
            The submitted review.
        """
        action = f"review pull request {owner}/{repo}#{number}"
        payload: dict[str, object] = {
            "event": _REVIEW_EVENTS[decision],
            "body": body,
        }
        if comments:
            payload["comments"] = [_review_comment_payload(c) for c in comments]
        resp = await self._request(
            "POST",
            f"/repos/{owner}/{repo}/pulls/{number}/reviews",
            action=action,
            json=payload,
        )
        raise_for_forge_status(resp, action=action)
        review = gh.review_from(
            gh.parse_github(resp.json(), gh.GhReview, what="review"),
            comment_count=len(comments),
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

    @override
    async def trigger_ci_run(
        self,
        *,
        owner: NotBlankStr,
        repo: NotBlankStr,
        workflow: NotBlankStr,
        branch: NotBlankStr,
    ) -> ForgeCiTrigger:
        """Dispatch a workflow on ``branch``.

        GitHub's ``workflow_dispatch`` accepts the trigger with a 204 and
        does not return the created run, so ``run`` stays ``None``.

        Returns:
            The trigger outcome.
        """
        action = f"trigger workflow {owner}/{repo}:{workflow}"
        resp = await self._request(
            "POST",
            f"/repos/{owner}/{repo}/actions/workflows/"
            f"{quote(str(workflow), safe='')}/dispatches",
            action=action,
            json={"ref": str(branch)},
        )
        raise_for_forge_status(resp, action=action)
        logger.info(FORGE_API_CI_TRIGGERED, workflow=str(workflow), rerun=False)
        return ForgeCiTrigger(
            triggered=True,
            message=f"dispatched {workflow} on {branch}",
        )

    @override
    async def rerun_ci_run(
        self, *, owner: NotBlankStr, repo: NotBlankStr, run_id: int
    ) -> ForgeCiTrigger:
        """Re-run an existing workflow run.

        Returns:
            The re-run trigger outcome.
        """
        action = f"rerun CI run {owner}/{repo}#{run_id}"
        resp = await self._request(
            "POST",
            f"/repos/{owner}/{repo}/actions/runs/{run_id}/rerun",
            action=action,
        )
        raise_for_forge_status(resp, action=action)
        logger.info(FORGE_API_CI_TRIGGERED, run_id=run_id, rerun=True)
        return ForgeCiTrigger(triggered=True, message=f"re-running run {run_id}")


__all__ = ["GitHubAgentForgeClient"]
