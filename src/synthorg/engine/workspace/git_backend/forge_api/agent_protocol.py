"""Agent-operations forge client protocol.

Separate from the repository-provisioning :class:`ForgeApiClient` (which
the external-remote git backend uses): this is the richer read/write
surface the agent-facing forge tools drive (files, issues, pull
requests, reviews, CI). Concrete clients live beside it
(``github_agent`` / ``gitea_agent``) and are selected by
:class:`ConnectionType` via ``build_forge_agent_api_client``. Forges
that do not implement an operation raise
:class:`~synthorg.core.domain_errors.FeatureNotImplementedError` rather
than returning a fabricated result.
"""

from typing import Protocol, runtime_checkable

from synthorg.core.types import NotBlankStr
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
from synthorg.engine.workspace.git_backend.forge_api.protocol import ForgeRepo


@runtime_checkable
class ForgeAgentApiClient(Protocol):
    """Read/write forge REST surface for the agent-facing forge tools.

    Every method raises the typed forge errors
    (``GitBackendForgeAuthError`` on 401/403,
    ``GitBackendRateLimitError`` on rate limit,
    ``GitBackendForgeApiError`` on any other non-2xx / transport
    failure). Tokens travel in the ``Authorization`` header only and are
    never logged.
    """

    async def get_repo(self, *, owner: NotBlankStr, repo: NotBlankStr) -> ForgeRepo:
        """Return the descriptor for ``owner/repo``."""
        ...

    async def read_file(
        self,
        *,
        owner: NotBlankStr,
        repo: NotBlankStr,
        path: NotBlankStr,
        ref: str | None = None,
    ) -> ForgeFileContent:
        """Return the decoded contents of ``path`` at ``ref``."""
        ...

    async def list_dir(
        self,
        *,
        owner: NotBlankStr,
        repo: NotBlankStr,
        path: str = "",
        ref: str | None = None,
    ) -> tuple[ForgeDirEntry, ...]:
        """List the entries under ``path`` at ``ref``."""
        ...

    async def list_accessible_repos(
        self, *, limit: int
    ) -> tuple[ForgeAccessibleRepo, ...]:
        """List repositories the bound token can reach.

        Used to populate the operator's repo-scope selection: the caller
        presents these and persists the chosen subset as the connection's
        allowed repositories. ``limit`` bounds the page size.
        """
        ...

    async def create_branch(
        self,
        *,
        owner: NotBlankStr,
        repo: NotBlankStr,
        new_branch: NotBlankStr,
        from_ref: NotBlankStr,
    ) -> ForgeBranchRef:
        """Create ``new_branch`` pointing at ``from_ref`` and return it."""
        ...

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

        ``sha`` is the blob sha of the file being replaced; omit it to
        create a new file (supplying it for an existing file, or omitting
        it for one that exists, is a forge-side conflict).
        """
        ...

    async def get_issue(
        self, *, owner: NotBlankStr, repo: NotBlankStr, number: int
    ) -> ForgeIssue:
        """Return a single issue by number."""
        ...

    async def list_issues(
        self,
        *,
        owner: NotBlankStr,
        repo: NotBlankStr,
        state: ForgeIssueState = "open",
        limit: int,
    ) -> tuple[ForgeIssue, ...]:
        """List issues (ordered as the forge returns them by default)."""
        ...

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
        ...

    async def comment_issue(
        self,
        *,
        owner: NotBlankStr,
        repo: NotBlankStr,
        number: int,
        body: NotBlankStr,
    ) -> ForgeComment:
        """Add a comment to an issue and return it."""
        ...

    async def get_pull_request(
        self, *, owner: NotBlankStr, repo: NotBlankStr, number: int
    ) -> ForgePullRequest:
        """Return a single pull request by number."""
        ...

    async def list_pull_requests(
        self,
        *,
        owner: NotBlankStr,
        repo: NotBlankStr,
        state: ForgePullState = "open",
        limit: int,
    ) -> tuple[ForgePullRequest, ...]:
        """List pull requests (ordered as the forge returns them by default)."""
        ...

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
        ...

    async def comment_pull_request(
        self,
        *,
        owner: NotBlankStr,
        repo: NotBlankStr,
        number: int,
        body: NotBlankStr,
    ) -> ForgeComment:
        """Add a discussion comment to a pull request and return it."""
        ...

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
        """Submit a review (approve / request changes / comment).

        ``comments`` are inline, diff-anchored comments the forge attaches
        to the review at the given ``path``/``line``/``side``.
        """
        ...

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
        ...

    async def list_ci_runs(
        self,
        *,
        owner: NotBlankStr,
        repo: NotBlankStr,
        branch: str | None = None,
        limit: int,
    ) -> tuple[ForgeCiRun, ...]:
        """List CI runs, most-recent first.

        Raises:
            FeatureNotImplementedError: When the forge does not expose a
                CI-run surface this client supports.
        """
        ...

    async def get_ci_run(
        self, *, owner: NotBlankStr, repo: NotBlankStr, run_id: int
    ) -> ForgeCiRun:
        """Return a single CI run by id.

        Raises:
            FeatureNotImplementedError: When the forge does not expose a
                CI-run surface this client supports.
        """
        ...

    async def trigger_ci_run(
        self,
        *,
        owner: NotBlankStr,
        repo: NotBlankStr,
        workflow: NotBlankStr,
        branch: NotBlankStr,
    ) -> ForgeCiTrigger:
        """Trigger a CI workflow/pipeline on ``branch``.

        Raises:
            FeatureNotImplementedError: When the forge does not expose a
                CI-trigger surface this client supports.
        """
        ...

    async def rerun_ci_run(
        self, *, owner: NotBlankStr, repo: NotBlankStr, run_id: int
    ) -> ForgeCiTrigger:
        """Re-run an existing CI run by id.

        Raises:
            FeatureNotImplementedError: When the forge does not expose a
                CI-rerun surface this client supports.
        """
        ...

    async def aclose(self) -> None:
        """Release the underlying HTTP client."""
        ...


__all__ = ["ForgeAgentApiClient"]
