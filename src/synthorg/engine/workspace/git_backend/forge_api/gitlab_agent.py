# module-kind: adapter
"""GitLab agent-operations forge client.

GitLab's v4 API diverges too far from the GitHub-compatible surface to
share :class:`ForgeAgentBase`: repositories are a URL-encoded
``namespace/project`` path, issues and merge requests are keyed by a
per-project ``iid``, reviews are approvals + discussions rather than a
single review object, and CI is pipelines. This client therefore
implements the :class:`ForgeAgentApiClient` protocol directly on top of
the :class:`GitLabForgeClient` transport (auth + ``_request``). Payload
parsing lives in :mod:`_gitlab_agent_parse`.
"""

from typing import Final
from urllib.parse import quote

import synthorg.engine.workspace.git_backend.forge_api._gitlab_agent_parse as gl
from synthorg.core.domain_errors import FeatureNotImplementedError
from synthorg.core.types import NotBlankStr
from synthorg.engine.errors import GitBackendForgeApiError
from synthorg.engine.workspace.git_backend.forge_api._http import raise_for_forge_status
from synthorg.engine.workspace.git_backend.forge_api.agent_models import (
    ForgeAccessibleRepo,
    ForgeBranchRef,
    ForgeCiRun,
    ForgeCiTrigger,
    ForgeComment,
    ForgeDirEntry,
    ForgeFileCommit,
    ForgeFileContent,
    ForgeIssue,
    ForgeIssueState,
    ForgeMergeMethod,
    ForgeMergeResult,
    ForgePullRequest,
    ForgePullState,
    ForgeReview,
    ForgeReviewComment,
    ForgeReviewDecision,
)
from synthorg.engine.workspace.git_backend.forge_api.gitlab import GitLabForgeClient
from synthorg.engine.workspace.git_backend.forge_api.protocol import ForgeRepo
from synthorg.observability import get_logger
from synthorg.observability.events.workspace import (
    FORGE_API_BRANCH_CREATED,
    FORGE_API_CI_TRIGGERED,
    FORGE_API_FILE_WRITTEN,
    FORGE_API_ISSUE_COMMENTED,
    FORGE_API_ISSUE_OPENED,
    FORGE_API_PULL_REQUEST_COMMENTED,
    FORGE_API_PULL_REQUEST_MERGED,
    FORGE_API_PULL_REQUEST_OPENED,
    FORGE_API_PULL_REQUEST_REVIEWED,
)

logger = get_logger(__name__)

_DRAFT_PREFIX: Final[str] = "Draft: "
# GitLab caps a list page at 100 regardless of a larger requested size, so
# a wider scan has to page until the API returns a short page.
_MAX_PAGE_SIZE: Final[int] = 100


def _gl_state(state: str) -> str:
    """Map a vendor-neutral list state onto GitLab's vocabulary.

    Returns:
        The GitLab state name (``open`` becomes ``opened``).
    """
    return "opened" if state == "open" else state


def _project(owner: NotBlankStr, repo: NotBlankStr) -> str:
    """URL-encode ``owner/repo`` into a GitLab project path segment.

    Returns:
        The percent-encoded ``owner/repo`` path.
    """
    return quote(f"{owner}/{repo}", safe="")


def _pipeline_of(resp_json: object) -> ForgeCiRun:
    """Parse and project a pipeline payload onto the domain CI run.

    Returns:
        The vendor-neutral :class:`ForgeCiRun`.
    """
    return gl.pipeline_from(gl.parse_gitlab(resp_json, gl.GlPipeline, what="pipeline"))


class GitLabAgentForgeClient(GitLabForgeClient):
    """Read/write GitLab REST client for the agent-facing forge tools."""

    async def get_repo(self, *, owner: NotBlankStr, repo: NotBlankStr) -> ForgeRepo:
        """Return the descriptor for ``owner/repo``.

        Returns:
            The vendor-neutral repository descriptor.
        """
        action = f"read repo {owner}/{repo}"
        resp = await self._request(
            "GET", f"/projects/{_project(owner, repo)}", action=action
        )
        raise_for_forge_status(resp, action=action)
        return gl.repo_from(gl.parse_gitlab(resp.json(), gl.GlProject, what="project"))

    async def read_file(
        self,
        *,
        owner: NotBlankStr,
        repo: NotBlankStr,
        path: NotBlankStr,
        ref: str | None = None,
    ) -> ForgeFileContent:
        """Return the decoded contents of ``path`` at ``ref``.

        Returns:
            The decoded file contents.
        """
        # GitLab's files API requires an explicit ref; resolve the default
        # branch when the caller did not pin one.
        if ref:
            resolved = ref
        else:
            repo_info = await self.get_repo(owner=owner, repo=repo)
            resolved = str(repo_info.default_branch)
        action = f"read file {owner}/{repo}/{path}"
        resp = await self._request(
            "GET",
            f"/projects/{_project(owner, repo)}/repository/files/"
            f"{quote(str(path), safe='')}",
            action=action,
            params={"ref": resolved},
        )
        raise_for_forge_status(resp, action=action)
        return gl.file_content_from(
            gl.parse_gitlab(resp.json(), gl.GlFile, what="file"), ref=resolved
        )

    async def list_dir(
        self,
        *,
        owner: NotBlankStr,
        repo: NotBlankStr,
        path: str = "",
        ref: str | None = None,
    ) -> tuple[ForgeDirEntry, ...]:
        """List the entries under ``path`` at ``ref``.

        Returns:
            The directory entries.
        """
        action = f"list dir {owner}/{repo}/{path}"
        params: dict[str, str | int] = {"path": path} if path else {}
        if ref:
            params["ref"] = ref
        resp = await self._request(
            "GET",
            f"/projects/{_project(owner, repo)}/repository/tree",
            action=action,
            params=params,
        )
        raise_for_forge_status(resp, action=action)
        return tuple(
            gl.dir_entry_from(gl.parse_gitlab(item, gl.GlTreeEntry, what="tree entry"))
            for item in _as_list(resp.json())
        )

    async def list_accessible_repos(
        self, *, limit: int
    ) -> tuple[ForgeAccessibleRepo, ...]:
        """List projects the bound token can reach.

        Returns:
            The accessible repositories with the token's permission.
        """
        action = "list accessible repos"
        collected: list[ForgeAccessibleRepo] = []
        # Fixed for the whole walk: ``page`` is an offset in units of
        # ``per_page``, so shrinking it mid-walk would move where the next
        # page starts and skip projects.
        page_size = min(_MAX_PAGE_SIZE, limit)
        page = 1
        while len(collected) < limit:
            resp = await self._request(
                "GET",
                "/projects",
                action=action,
                params={
                    "membership": "true",
                    "per_page": page_size,
                    "page": page,
                },
            )
            raise_for_forge_status(resp, action=action)
            items = _as_list(resp.json())
            if not items:
                break
            parsed = (
                gl.parse_gitlab(item, gl.GlProject, what="project") for item in items
            )
            mapped = (gl.accessible_repo_from(model) for model in parsed)
            collected.extend(entry for entry in mapped if entry is not None)
            if len(items) < page_size:
                break
            page += 1
        return tuple(collected[:limit])

    async def create_branch(
        self,
        *,
        owner: NotBlankStr,
        repo: NotBlankStr,
        new_branch: NotBlankStr,
        from_ref: NotBlankStr,
    ) -> ForgeBranchRef:
        """Create ``new_branch`` from ``from_ref`` and return it.

        Returns:
            The created branch ref.
        """
        action = f"create branch {owner}/{repo}@{new_branch}"
        resp = await self._request(
            "POST",
            f"/projects/{_project(owner, repo)}/repository/branches",
            action=action,
            params={"branch": str(new_branch), "ref": str(from_ref)},
        )
        raise_for_forge_status(resp, action=action)
        branch = gl.branch_ref_from(
            gl.parse_gitlab(resp.json(), gl.GlBranch, what="branch"),
            name=str(new_branch),
        )
        logger.info(FORGE_API_BRANCH_CREATED, branch=str(new_branch))
        return branch

    async def write_file(  # noqa: PLR0913 -- forge contents-API fields
        self,
        *,
        owner: NotBlankStr,
        repo: NotBlankStr,
        path: NotBlankStr,
        content: str,
        branch: NotBlankStr,
        message: NotBlankStr,
        sha: str | None = None,
    ) -> ForgeFileCommit:
        """Create or update ``path`` on ``branch`` and return the commit.

        Returns:
            The resulting file commit (head sha resolved from the branch).
        """
        action = f"write file {owner}/{repo}/{path}"
        # GitLab distinguishes create (POST) from update (PUT); the caller
        # signals an update by supplying the replaced blob's sha.
        method = "PUT" if sha else "POST"
        resp = await self._request(
            method,
            f"/projects/{_project(owner, repo)}/repository/files/"
            f"{quote(str(path), safe='')}",
            action=action,
            json={
                "branch": str(branch),
                "content": content,
                "commit_message": str(message),
                "encoding": "text",
            },
        )
        raise_for_forge_status(resp, action=action)
        write = gl.parse_gitlab(resp.json(), gl.GlFileWrite, what="file write")
        # The files PUT/POST returns no commit sha, so resolve the branch head.
        commit_sha = await self._branch_head_sha(owner=owner, repo=repo, branch=branch)
        logger.info(FORGE_API_FILE_WRITTEN, path=str(path))
        return gl.file_commit_from(write, commit_sha=commit_sha)

    async def _branch_head_sha(
        self, *, owner: NotBlankStr, repo: NotBlankStr, branch: NotBlankStr
    ) -> str:
        """Resolve the head commit sha of ``branch``.

        Returns:
            The head commit sha, or the empty string if absent.
        """
        action = f"resolve branch head {owner}/{repo}@{branch}"
        resp = await self._request(
            "GET",
            f"/projects/{_project(owner, repo)}/repository/branches/"
            f"{quote(str(branch), safe='')}",
            action=action,
        )
        raise_for_forge_status(resp, action=action)
        parsed = gl.parse_gitlab(resp.json(), gl.GlBranch, what="branch")
        return parsed.commit.id if parsed.commit is not None else ""

    async def get_issue(
        self, *, owner: NotBlankStr, repo: NotBlankStr, number: int
    ) -> ForgeIssue:
        """Return a single issue by iid.

        Returns:
            The issue.
        """
        action = f"read issue {owner}/{repo}!{number}"
        resp = await self._request(
            "GET",
            f"/projects/{_project(owner, repo)}/issues/{number}",
            action=action,
        )
        raise_for_forge_status(resp, action=action)
        return gl.issue_from(gl.parse_gitlab(resp.json(), gl.GlIssue, what="issue"))

    async def list_issues(
        self,
        *,
        owner: NotBlankStr,
        repo: NotBlankStr,
        state: ForgeIssueState = "open",
        limit: int,
    ) -> tuple[ForgeIssue, ...]:
        """List issues.

        Returns:
            The matching issues.
        """
        action = f"list issues {owner}/{repo}"
        params: dict[str, str | int] = {"per_page": limit}
        if state != "all":
            params["state"] = _gl_state(state)
        resp = await self._request(
            "GET",
            f"/projects/{_project(owner, repo)}/issues",
            action=action,
            params=params,
        )
        raise_for_forge_status(resp, action=action)
        return tuple(
            gl.issue_from(gl.parse_gitlab(item, gl.GlIssue, what="issue"))
            for item in _as_list(resp.json())
        )

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
        payload: dict[str, object] = {"title": str(title), "description": body}
        if labels:
            payload["labels"] = ",".join(labels)
        resp = await self._request(
            "POST",
            f"/projects/{_project(owner, repo)}/issues",
            action=action,
            json=payload,
        )
        raise_for_forge_status(resp, action=action)
        issue = gl.issue_from(gl.parse_gitlab(resp.json(), gl.GlIssue, what="issue"))
        logger.info(FORGE_API_ISSUE_OPENED, number=issue.number)
        return issue

    async def comment_issue(
        self, *, owner: NotBlankStr, repo: NotBlankStr, number: int, body: NotBlankStr
    ) -> ForgeComment:
        """Add a comment to an issue and return it.

        Returns:
            The created comment.
        """
        comment = await self._post_note(
            owner=owner, repo=repo, kind="issues", number=number, body=body
        )
        logger.info(FORGE_API_ISSUE_COMMENTED, number=number)
        return comment

    async def get_pull_request(
        self, *, owner: NotBlankStr, repo: NotBlankStr, number: int
    ) -> ForgePullRequest:
        """Return a single merge request by iid.

        Returns:
            The merge request as a vendor-neutral pull request.
        """
        model = await self._fetch_mr(owner=owner, repo=repo, number=number)
        return gl.pull_from(model)

    async def _fetch_mr(
        self, *, owner: NotBlankStr, repo: NotBlankStr, number: int
    ) -> gl.GlMergeRequest:
        """Fetch a raw merge-request payload (carries diff refs).

        Returns:
            The parsed GitLab merge request model.
        """
        action = f"read merge request {owner}/{repo}!{number}"
        resp = await self._request(
            "GET",
            f"/projects/{_project(owner, repo)}/merge_requests/{number}",
            action=action,
        )
        raise_for_forge_status(resp, action=action)
        return gl.parse_gitlab(resp.json(), gl.GlMergeRequest, what="merge request")

    async def list_pull_requests(
        self,
        *,
        owner: NotBlankStr,
        repo: NotBlankStr,
        state: ForgePullState = "open",
        limit: int,
    ) -> tuple[ForgePullRequest, ...]:
        """List merge requests.

        Returns:
            The matching merge requests.
        """
        action = f"list merge requests {owner}/{repo}"
        params: dict[str, str | int] = {"per_page": limit}
        if state != "all":
            params["state"] = _gl_state(state)
        resp = await self._request(
            "GET",
            f"/projects/{_project(owner, repo)}/merge_requests",
            action=action,
            params=params,
        )
        raise_for_forge_status(resp, action=action)
        return tuple(
            gl.pull_from(gl.parse_gitlab(item, gl.GlMergeRequest, what="merge request"))
            for item in _as_list(resp.json())
        )

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
        """Open a merge request from ``source_branch`` into ``target_branch``.

        Returns:
            The created merge request.
        """
        action = f"open merge request in {owner}/{repo}"
        # GitLab has no draft flag; the ``Draft:`` title prefix is the
        # documented equivalent.
        mr_title = f"{_DRAFT_PREFIX}{title}" if draft else str(title)
        resp = await self._request(
            "POST",
            f"/projects/{_project(owner, repo)}/merge_requests",
            action=action,
            json={
                "title": mr_title,
                "source_branch": str(source_branch),
                "target_branch": str(target_branch),
                "description": body,
            },
        )
        raise_for_forge_status(resp, action=action)
        pull = gl.pull_from(
            gl.parse_gitlab(resp.json(), gl.GlMergeRequest, what="merge request")
        )
        logger.info(FORGE_API_PULL_REQUEST_OPENED, number=pull.number)
        return pull

    async def comment_pull_request(
        self, *, owner: NotBlankStr, repo: NotBlankStr, number: int, body: NotBlankStr
    ) -> ForgeComment:
        """Add a discussion comment to a merge request and return it.

        Returns:
            The created comment.
        """
        comment = await self._post_note(
            owner=owner, repo=repo, kind="merge_requests", number=number, body=body
        )
        logger.info(FORGE_API_PULL_REQUEST_COMMENTED, number=number)
        return comment

    async def _post_note(
        self,
        *,
        owner: NotBlankStr,
        repo: NotBlankStr,
        kind: str,
        number: int,
        body: NotBlankStr,
    ) -> ForgeComment:
        """Post a note on an issue or merge request.

        Returns:
            The created comment.
        """
        action = f"comment on {owner}/{repo} {kind}!{number}"
        resp = await self._request(
            "POST",
            f"/projects/{_project(owner, repo)}/{kind}/{number}/notes",
            action=action,
            json={"body": str(body)},
        )
        raise_for_forge_status(resp, action=action)
        return gl.comment_from(gl.parse_gitlab(resp.json(), gl.GlNote, what="note"))

    async def review_pull_request(  # noqa: PLR0913 -- forge review fields
        self,
        *,
        owner: NotBlankStr,
        repo: NotBlankStr,
        number: int,
        decision: ForgeReviewDecision,
        body: str = "",
        comments: tuple[ForgeReviewComment, ...] = (),
    ) -> ForgeReview:
        """Submit a review as an approval plus notes/discussions.

        GitLab has no single review object: inline comments become
        diff-anchored discussions, the summary becomes a note, and an
        ``approve`` decision hits the approve endpoint.

        Returns:
            A synthesised review record.
        """
        project = _project(owner, repo)
        if comments:
            await self._post_inline_comments(
                owner=owner, repo=repo, number=number, comments=comments
            )
        summary = await self._post_note(
            owner=owner,
            repo=repo,
            kind="merge_requests",
            number=number,
            body=NotBlankStr(body or f"Review: {decision}"),
        )
        if decision == "approve":
            approve = f"approve merge request {owner}/{repo}!{number}"
            resp = await self._request(
                "POST",
                f"/projects/{project}/merge_requests/{number}/approve",
                action=approve,
            )
            raise_for_forge_status(resp, action=approve)
        logger.info(FORGE_API_PULL_REQUEST_REVIEWED, number=number, decision=decision)
        return ForgeReview(
            id=summary.id,
            state=decision,
            author=summary.author,
            body=body,
            url=summary.url,
            comment_count=len(comments),
        )

    async def _post_inline_comments(
        self,
        *,
        owner: NotBlankStr,
        repo: NotBlankStr,
        number: int,
        comments: tuple[ForgeReviewComment, ...],
    ) -> None:
        """Post each inline comment as a diff-anchored discussion.

        Raises:
            FeatureNotImplementedError: When the merge request exposes no
                diff refs (inline anchoring is impossible without them).
        """
        mr = await self._fetch_mr(owner=owner, repo=repo, number=number)
        if mr.diff_refs is None:
            msg = f"GitLab merge request !{number} exposed no diff refs for comments"
            raise FeatureNotImplementedError(msg)
        project = _project(owner, repo)
        for comment in comments:
            action = f"comment on {owner}/{repo}!{number}:{comment.path}"
            resp = await self._request(
                "POST",
                f"/projects/{project}/merge_requests/{number}/discussions",
                action=action,
                json={
                    "body": str(comment.body),
                    "position": gl.discussion_position(comment, mr.diff_refs),
                },
            )
            raise_for_forge_status(resp, action=action)

    async def merge_pull_request(
        self,
        *,
        owner: NotBlankStr,
        repo: NotBlankStr,
        number: int,
        method: ForgeMergeMethod = "merge",
        commit_title: str = "",
    ) -> ForgeMergeResult:
        """Merge a merge request and return the outcome.

        Returns:
            The merge outcome (merged flag + resulting commit sha).

        Raises:
            FeatureNotImplementedError: When ``method`` is ``"rebase"``.
                GitLab's merge endpoint takes only ``squash``; rebasing is a
                separate asynchronous ``/rebase`` call. Accepting ``rebase``
                here would silently produce a merge commit instead, so the
                caller is told rather than quietly given the wrong history.
        """
        if method == "rebase":
            msg = (
                "GitLab merge requests cannot be rebase-merged through the "
                "merge endpoint; use 'merge' or 'squash'"
            )
            raise FeatureNotImplementedError(msg)
        action = f"merge merge request {owner}/{repo}!{number}"
        payload: dict[str, object] = {"squash": method == "squash"}
        if commit_title:
            payload["merge_commit_message"] = commit_title
        resp = await self._request(
            "PUT",
            f"/projects/{_project(owner, repo)}/merge_requests/{number}/merge",
            action=action,
            json=payload,
        )
        raise_for_forge_status(resp, action=action)
        result = gl.merge_result_from(
            gl.parse_gitlab(resp.json(), gl.GlMergeRequest, what="merge request")
        )
        logger.info(FORGE_API_PULL_REQUEST_MERGED, number=number, merged=result.merged)
        return result

    async def list_ci_runs(
        self,
        *,
        owner: NotBlankStr,
        repo: NotBlankStr,
        branch: str | None = None,
        limit: int,
    ) -> tuple[ForgeCiRun, ...]:
        """List pipelines, most-recent first.

        Returns:
            The matching pipelines as CI runs.
        """
        action = f"list pipelines {owner}/{repo}"
        params: dict[str, str | int] = {"per_page": limit}
        if branch:
            params["ref"] = branch
        resp = await self._request(
            "GET",
            f"/projects/{_project(owner, repo)}/pipelines",
            action=action,
            params=params,
        )
        raise_for_forge_status(resp, action=action)
        return tuple(
            gl.pipeline_from(gl.parse_gitlab(item, gl.GlPipeline, what="pipeline"))
            for item in _as_list(resp.json())
        )

    async def get_ci_run(
        self, *, owner: NotBlankStr, repo: NotBlankStr, run_id: int
    ) -> ForgeCiRun:
        """Return a single pipeline by id.

        Returns:
            The pipeline as a CI run.
        """
        action = f"read pipeline {owner}/{repo}#{run_id}"
        resp = await self._request(
            "GET",
            f"/projects/{_project(owner, repo)}/pipelines/{run_id}",
            action=action,
        )
        raise_for_forge_status(resp, action=action)
        return _pipeline_of(resp.json())

    async def trigger_ci_run(
        self,
        *,
        owner: NotBlankStr,
        repo: NotBlankStr,
        workflow: NotBlankStr,  # noqa: ARG002 -- GitLab triggers per-ref, not per-workflow
        branch: NotBlankStr,
    ) -> ForgeCiTrigger:
        """Trigger a pipeline on ``branch``.

        GitLab triggers a whole pipeline for a ref, so ``workflow`` is not
        addressable here (accepted for protocol parity).

        Returns:
            The trigger outcome with the created pipeline.
        """
        action = f"trigger pipeline {owner}/{repo}@{branch}"
        resp = await self._request(
            "POST",
            f"/projects/{_project(owner, repo)}/pipeline",
            action=action,
            params={"ref": str(branch)},
        )
        raise_for_forge_status(resp, action=action)
        run = _pipeline_of(resp.json())
        logger.info(FORGE_API_CI_TRIGGERED, branch=str(branch), rerun=False)
        return ForgeCiTrigger(triggered=True, message=f"pipeline for {branch}", run=run)

    async def rerun_ci_run(
        self, *, owner: NotBlankStr, repo: NotBlankStr, run_id: int
    ) -> ForgeCiTrigger:
        """Retry an existing pipeline by id.

        Returns:
            The re-run trigger outcome with the retried pipeline.
        """
        action = f"retry pipeline {owner}/{repo}#{run_id}"
        resp = await self._request(
            "POST",
            f"/projects/{_project(owner, repo)}/pipelines/{run_id}/retry",
            action=action,
        )
        raise_for_forge_status(resp, action=action)
        run = _pipeline_of(resp.json())
        logger.info(FORGE_API_CI_TRIGGERED, run_id=run_id, rerun=True)
        return ForgeCiTrigger(
            triggered=True, message=f"retrying pipeline {run_id}", run=run
        )


def _as_list(data: object) -> list[object]:
    """Return ``data`` as a list or raise a typed forge error.

    Returns:
        The response as a list.

    Raises:
        GitBackendForgeApiError: When GitLab returned a non-array where a
            collection was expected.
    """
    if isinstance(data, list):
        return data
    msg = "expected a GitLab collection response but got a single object"
    raise GitBackendForgeApiError(msg)


__all__ = ["GitLabAgentForgeClient"]
