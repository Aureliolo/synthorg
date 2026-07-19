"""Gitea-family (Gitea + Forgejo) agent-operations forge client.

Forgejo is a Gitea fork sharing the ``/api/v1`` REST surface, so
:class:`ForgejoAgentForgeClient` is a thin subclass. The read surface is
GitHub-compatible (inherited from :class:`ForgeAgentBase`); the write
surface diverges: reviews use past-tense event names, merges post a
``Do`` field and return no body, issue labels are addressed by id (so
names are resolved first), pull-request drafts and Actions/CI reads are
not exposed by this client and fail loud.
"""

from collections.abc import Mapping
from typing import Final, override

from pydantic import BaseModel, ConfigDict

import synthorg.engine.workspace.git_backend.forge_api._github_agent_parse as gh
from synthorg.core.domain_errors import FeatureNotImplementedError
from synthorg.core.types import NotBlankStr
from synthorg.engine.errors import GitBackendForgeApiError
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
from synthorg.engine.workspace.git_backend.forge_api.gitea import GiteaForgeClient
from synthorg.observability import get_logger
from synthorg.observability.events.workspace import (
    FORGE_API_ISSUE_OPENED,
    FORGE_API_PULL_REQUEST_MERGED,
    FORGE_API_PULL_REQUEST_OPENED,
    FORGE_API_PULL_REQUEST_REVIEWED,
)

logger = get_logger(__name__)

_LABEL_PAGE_LIMIT: Final[int] = 100
_REVIEW_EVENTS: Final[Mapping[str, str]] = {
    "approve": "APPROVED",
    "request_changes": "REQUEST_CHANGES",
    "comment": "COMMENT",
}
_CI_UNSUPPORTED: Final[str] = (
    "CI-run reads are not available for the Gitea/Forgejo forge client"
)


class _GiteaLabel(BaseModel):  # lint-allow: frozen-extra-forbid -- forge extras
    """A repository label ``{id, name}`` (Gitea addresses labels by id)."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="ignore")

    id: int = 0
    name: str = ""


class GiteaAgentForgeClient(GiteaForgeClient, ForgeAgentBase):
    """Read/write Gitea REST client for the agent-facing forge tools."""

    _LIST_PARAM = "limit"

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
        """Open a new issue and return it (labels resolved by name to id).

        Returns:
            The created issue.
        """
        label_ids = await self._resolve_label_ids(owner=owner, repo=repo, labels=labels)
        action = f"open issue in {owner}/{repo}"
        resp = await self._request(
            "POST",
            f"/repos/{owner}/{repo}/issues",
            action=action,
            json={"title": str(title), "body": body, "labels": label_ids},
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
        """Open a pull request (drafts are not supported by this forge).

        Returns:
            The created pull request.

        Raises:
            FeatureNotImplementedError: When ``draft`` is requested.
        """
        if draft:
            msg = "draft pull requests are not supported by the Gitea/Forgejo client"
            raise FeatureNotImplementedError(msg)
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
            The merge outcome (Gitea returns an empty body, so the sha
            is not populated).
        """
        action = f"merge pull request {owner}/{repo}#{number}"
        payload: dict[str, object] = {"Do": method}
        if commit_title:
            payload["MergeTitleField"] = commit_title
        resp = await self._request(
            "POST",
            f"/repos/{owner}/{repo}/pulls/{number}/merge",
            action=action,
            json=payload,
        )
        raise_for_forge_status(resp, action=action)
        # Gitea's merge endpoint returns an empty 200 body on success.
        logger.info(FORGE_API_PULL_REQUEST_MERGED, number=number, merged=True)
        return ForgeMergeResult(merged=True, sha="", message="merged")

    @override
    async def list_ci_runs(
        self,
        *,
        owner: NotBlankStr,
        repo: NotBlankStr,
        branch: str | None = None,
        limit: int,
    ) -> tuple[ForgeCiRun, ...]:
        """Reject: this forge client does not expose CI-run reads.

        Raises:
            FeatureNotImplementedError: Always (CI reads are GitHub-only).
        """
        raise FeatureNotImplementedError(_CI_UNSUPPORTED)

    @override
    async def get_ci_run(
        self,
        *,
        owner: NotBlankStr,
        repo: NotBlankStr,
        run_id: int,
    ) -> ForgeCiRun:
        """Reject: this forge client does not expose CI-run reads.

        Raises:
            FeatureNotImplementedError: Always (CI reads are GitHub-only).
        """
        raise FeatureNotImplementedError(_CI_UNSUPPORTED)

    async def _resolve_label_ids(
        self, *, owner: NotBlankStr, repo: NotBlankStr, labels: tuple[str, ...]
    ) -> list[int]:
        """Map label names to Gitea numeric ids, failing on unknown names.

        Returns:
            The resolved label ids, in the order the names were given.

        Raises:
            GitBackendForgeApiError: When a requested label does not
                exist on the repository (never silently dropped).
        """
        if not labels:
            return []
        by_name = await self._fetch_label_ids(
            owner=owner, repo=repo, wanted=set(labels)
        )
        resolved: list[int] = []
        for name in labels:
            label_id = by_name.get(name)
            if label_id is None:
                msg = f"label {name!r} does not exist on {owner}/{repo}"
                raise GitBackendForgeApiError(msg)
            resolved.append(label_id)
        return resolved

    async def _fetch_label_ids(
        self, *, owner: NotBlankStr, repo: NotBlankStr, wanted: set[str]
    ) -> dict[str, int]:
        """Page the repo's labels into a name->id map, stopping when done.

        Pages until every wanted name is found or the forge runs out of
        labels, so a repo with more than one page of labels still resolves
        (a single fixed-page request would silently miss later-page names).

        Returns:
            The name->id map covering (at least) every found wanted label.

        Raises:
            GitBackendForgeApiError: On a malformed labels response.
        """
        action = f"resolve labels for {owner}/{repo}"
        by_name: dict[str, int] = {}
        page = 1
        # Bounded label pagination: stops on a short page or once every wanted
        # label is resolved, never an unbounded consumer.
        # lint-allow: long-running-loop-kill-switch -- bounded label pagination
        while True:
            resp = await self._request(
                "GET",
                f"/repos/{owner}/{repo}/labels",
                action=action,
                params={"limit": _LABEL_PAGE_LIMIT, "page": page},
            )
            raise_for_forge_status(resp, action=action)
            data = resp.json()
            if not isinstance(data, list):
                msg = "malformed Gitea labels response"
                raise GitBackendForgeApiError(msg)
            for item in data:
                label = gh.parse_github(item, _GiteaLabel, what="label")
                by_name[label.name] = label.id
            if len(data) < _LABEL_PAGE_LIMIT or wanted <= by_name.keys():
                return by_name
            page += 1


class ForgejoAgentForgeClient(GiteaAgentForgeClient):
    """Forgejo agent client; shares the Gitea ``/api/v1`` surface."""


__all__ = ["ForgejoAgentForgeClient", "GiteaAgentForgeClient"]
