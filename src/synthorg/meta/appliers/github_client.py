"""GitHub REST API client for code modification proposals.

Pushes file changes, creates branches and draft PRs via the GitHub
Contents and Git Refs APIs.  No local ``git`` or ``gh`` CLI required,
making this safe to run inside Docker containers.
"""

import base64
import re
from collections.abc import Awaitable, Callable
from typing import ClassVar, Final, Self

import httpx

from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode
from synthorg.core.resilience import (
    GeneralRetryHandler,
    coerce_finite_nonneg_seconds,
    parse_retry_after_seconds,
)
from synthorg.integrations.errors import IntegrationError
from synthorg.meta.models import CodeChange, CodeOperation
from synthorg.observability import get_logger
from synthorg.observability.events.meta import (
    META_CODE_BRANCH_CREATED,
    META_CODE_FILE_WRITTEN,
    META_CODE_GITHUB_API_FAILED,
    META_CODE_GITHUB_RATE_LIMIT_RETRY,
    META_CODE_PR_CREATED,
)

# ── Custom exception types ───────────────────────────────────────


class GitHubAPIError(IntegrationError):
    """Raised on non-auth GitHub API failures.

    Attributes:
        github_status_code: HTTP status code from the upstream response.
        action: Human-readable description of the attempted action.
        body: Sanitized response body snippet.
    """

    default_message: ClassVar[str] = "GitHub API request failed"

    def __init__(self, *, status_code: int, action: str, body: str) -> None:
        self.github_status_code = status_code
        self.action = action
        self.body = body
        super().__init__(
            f"GitHub API failed to {action}: {status_code} {body}",
        )


class GitHubAuthError(GitHubAPIError):
    """Raised on 401/403 GitHub API responses.

    Indicates invalid, expired, or insufficiently scoped credentials.
    """

    default_message: ClassVar[str] = "GitHub API authentication failed"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.AUTH
    error_code: ClassVar[ErrorCode] = ErrorCode.UNAUTHORIZED
    status_code: ClassVar[int] = 401


class GitHubRateLimitError(GitHubAPIError):
    """Raised on a 429 (or secondary-limit) GitHub API response.

    A retryable failure distinct from the generic ``GitHubAPIError`` so
    callers (and any retry wrapper) can detect throttling and honour the
    ``Retry-After`` hint instead of treating it as a terminal API error.
    Inherits ``GitHubAPIError``'s error code (an inheritance alias) so
    the error-code-uniqueness gate is satisfied.
    """

    default_message: ClassVar[str] = "GitHub API rate limited"
    # Throttling is transient: mark retryable (the base ``GitHubAPIError``
    # defaults both flags to ``False``) so a retry wrapper branching on
    # ``is_retryable`` / ``retryable`` actually backs off and retries
    # rather than treating a 429 as a terminal API failure.
    is_retryable: bool = True
    retryable: ClassVar[bool] = True

    def __init__(
        self,
        *,
        status_code: int,
        action: str,
        body: str,
        retry_after_seconds: float | None,
    ) -> None:
        self.retry_after_seconds = retry_after_seconds
        super().__init__(status_code=status_code, action=action, body=body)


logger = get_logger(__name__)

_DEFAULT_TIMEOUT: Final[int] = 30
_HTTP_UNAUTHORIZED: Final[int] = 401
_HTTP_FORBIDDEN: Final[int] = 403
_HTTP_TOO_MANY_REQUESTS: Final[int] = 429
# Bounded retry for GitHub throttling. A 429 means the request was
# rejected (not executed), so retrying any verb is idempotent-safe.
_RATE_LIMIT_MAX_ATTEMPTS: Final[int] = 4
_RATE_LIMIT_BASE_SECONDS: Final[float] = 1.0
_RATE_LIMIT_CAP_SECONDS: Final[float] = 30.0


class HttpGitHubClient:
    """GitHub REST API client backed by httpx.

    Uses the Contents API for file operations (one commit per file)
    and the Git Refs API for branch management.

    Args:
        token: GitHub personal access token or app installation token.
        repo: Repository in ``owner/repo`` format.
        base_branch: Default branch to create feature branches from.
        timeout: HTTP request timeout in seconds.
        api_base_url: GitHub API base URL.  Resolve via
            ``ConfigResolver.get_str("integrations", "github_api_url")``
            at the call site to support GitHub Enterprise installations
            (e.g. ``https://github.example.com/api/v3``).
    """

    def __init__(
        self,
        *,
        token: str,
        repo: str,
        api_base_url: str,
        base_branch: str = "main",
        timeout: int = _DEFAULT_TIMEOUT,
    ) -> None:
        self._token = token
        self._repo = repo
        self._base_branch = base_branch
        self._timeout = timeout
        self._api_base_url = api_base_url
        # Lazily created so constructing the client (e.g. at boot-time
        # applier wiring) opens no network resources until first use.
        self.__client: httpx.AsyncClient | None = None
        # Retry GitHub throttling, honouring the server's Retry-After hint
        # over the computed backoff. Only a 429 (GitHubRateLimitError) is
        # retryable; auth / other API errors propagate immediately.
        self._retry = GeneralRetryHandler(
            retryable=lambda exc: isinstance(exc, GitHubRateLimitError),
            max_attempts=_RATE_LIMIT_MAX_ATTEMPTS,
            base=_RATE_LIMIT_BASE_SECONDS,
            cap=_RATE_LIMIT_CAP_SECONDS,
            event=META_CODE_GITHUB_RATE_LIMIT_RETRY,
            delay_override=lambda exc: (
                exc.retry_after_seconds
                if isinstance(exc, GitHubRateLimitError)
                else None
            ),
        )

    @property
    def _client(self) -> httpx.AsyncClient:
        """Lazily create the httpx client on first use."""
        if self.__client is None:
            self.__client = httpx.AsyncClient(
                base_url=self._api_base_url,
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                timeout=self._timeout,
            )
        return self.__client

    async def aclose(self) -> None:
        """Close the underlying httpx client if it was created."""
        if self.__client is not None:
            await self.__client.aclose()
            self.__client = None

    async def __aenter__(self) -> Self:
        """Support ``async with`` usage.

        Returns:
            ``Self`` instance.
        """
        return self

    async def __aexit__(self, *exc: object) -> None:
        """Close on context manager exit."""
        await self.aclose()

    async def _send(
        self,
        request: Callable[[], Awaitable[httpx.Response]],
        action: str,
    ) -> httpx.Response:
        """Issue *request*, retrying GitHub throttling with backoff.

        Classifies throttling INSIDE the retry scope so the handler can
        honour the server's ``Retry-After``. GitHub signals rate limits
        with BOTH 429 and 403 (primary/secondary), so both are routed
        through ``_check_response``: a throttled response raises
        ``GitHubRateLimitError`` (retryable) while a genuine auth 403
        raises ``GitHubAuthError`` (not retryable, propagates). Other
        statuses pass through unchecked so the caller (or a follow-up
        ``_check_response``) can classify them.

        Returns:
            The HTTP response (status unclassified except for the retried
            throttling cases).
        """

        async def _op() -> httpx.Response:
            resp = await request()
            if resp.status_code in {_HTTP_TOO_MANY_REQUESTS, _HTTP_FORBIDDEN}:
                _check_response(resp, action)
            return resp

        return await self._retry.execute(_op, action=action)

    async def create_branch(self, name: str) -> None:
        """Create a branch from the default branch HEAD.

        Args:
            name: Branch name to create.

        Raises:
            GitHubAuthError: On 401/403 responses.
            GitHubAPIError: On other API failures.
        """
        sha = await self._get_branch_sha(self._base_branch)
        resp = await self._send(
            lambda: self._client.post(
                f"/repos/{self._repo}/git/refs",
                json={"ref": f"refs/heads/{name}", "sha": sha},
            ),
            f"create branch '{name}'",
        )
        _check_response(resp, f"create branch '{name}'")
        logger.info(
            META_CODE_BRANCH_CREATED,
            branch=name,
            from_sha=sha[:8],
        )

    async def push_change(
        self,
        *,
        branch: str,
        change: CodeChange,
        message: str,
    ) -> None:
        """Push a single file change to a branch.

        Args:
            branch: Target branch name.
            change: The code change to push.
            message: Commit message.

        Raises:
            GitHubAuthError: On 401/403 responses.
            GitHubAPIError: On other API failures.
        """
        if change.operation == CodeOperation.DELETE:
            await self._delete_file(branch, change.file_path, message)
        else:
            await self._create_or_update_file(
                branch,
                change.file_path,
                change.new_content,
                message,
                modify=change.operation == CodeOperation.MODIFY,
            )
        logger.info(
            META_CODE_FILE_WRITTEN,
            operation=change.operation.value,
            file_path=change.file_path,
        )

    async def create_draft_pr(
        self,
        *,
        head: str,
        title: str,
        body: str,
    ) -> str:
        """Create a draft pull request.

        Args:
            head: Head branch name.
            title: PR title.
            body: PR body (Markdown).

        Returns:
            URL of the created PR.

        Raises:
            GitHubAuthError: On 401/403 responses.
            GitHubAPIError: On other API failures.
        """
        resp = await self._send(
            lambda: self._client.post(
                f"/repos/{self._repo}/pulls",
                json={
                    "title": title,
                    "body": body,
                    "head": head,
                    "base": self._base_branch,
                    "draft": True,
                },
            ),
            "create draft PR",
        )
        _check_response(resp, "create draft PR")
        pr_url: str = resp.json()["html_url"]
        logger.info(
            META_CODE_PR_CREATED,
            pr_url=pr_url,
        )
        return pr_url

    async def verify_token(self) -> None:
        """Verify the GitHub token by calling ``GET /user``.

        Raises:
            GitHubAuthError: If the token is invalid or expired (401/403).
            GitHubAPIError: On other API failures.
        """
        resp = await self._send(
            lambda: self._client.get("/user"), "verify GitHub token"
        )
        _check_response(resp, "verify GitHub token")

    async def delete_branch(self, name: str) -> None:
        """Delete a remote branch.

        Args:
            name: Branch name to delete.

        Raises:
            GitHubAPIError: If the API call fails.
        """
        resp = await self._send(
            lambda: self._client.delete(
                f"/repos/{self._repo}/git/refs/heads/{name}",
            ),
            f"delete branch '{name}'",
        )
        if resp.status_code == 422:  # noqa: PLR2004
            # Only suppress "reference does not exist" -- other 422s
            # (e.g. protected branch) should still raise.
            if not _is_missing_ref(resp):
                _check_response(resp, f"delete branch '{name}'")
        else:
            _check_response(resp, f"delete branch '{name}'")

    # -- Private helpers ---------------------------------------------------

    async def _get_branch_sha(self, branch: str) -> str:
        """Get the HEAD commit SHA of a branch.

        Returns:
            Resulting string.
        """
        resp = await self._send(
            lambda: self._client.get(
                f"/repos/{self._repo}/git/ref/heads/{branch}",
            ),
            f"get SHA for branch '{branch}'",
        )
        _check_response(resp, f"get SHA for branch '{branch}'")
        sha: str = resp.json()["object"]["sha"]
        return sha

    async def _get_file_sha(
        self,
        branch: str,
        path: str,
    ) -> str:
        """Get the blob SHA of a file on a branch.

        Returns:
            Resulting string.
        """
        resp = await self._send(
            lambda: self._client.get(
                f"/repos/{self._repo}/contents/{path}",
                params={"ref": branch},
            ),
            f"get SHA for file '{path}'",
        )
        _check_response(resp, f"get SHA for file '{path}'")
        sha: str = resp.json()["sha"]
        return sha

    async def _create_or_update_file(
        self,
        branch: str,
        path: str,
        content: str,
        message: str,
        *,
        modify: bool,
    ) -> None:
        """Create or update a file on a branch."""
        payload: dict[str, object] = {
            "message": message,
            "content": base64.b64encode(
                content.encode("utf-8"),
            ).decode("ascii"),
            "branch": branch,
        }
        if modify:
            payload["sha"] = await self._get_file_sha(branch, path)
        resp = await self._send(
            lambda: self._client.put(
                f"/repos/{self._repo}/contents/{path}",
                json=payload,
            ),
            f"push file '{path}'",
        )
        _check_response(resp, f"push file '{path}'")

    async def _delete_file(
        self,
        branch: str,
        path: str,
        message: str,
    ) -> None:
        """Delete a file from a branch."""
        sha = await self._get_file_sha(branch, path)
        resp = await self._send(
            lambda: self._client.request(
                "DELETE",
                f"/repos/{self._repo}/contents/{path}",
                json={
                    "message": message,
                    "sha": sha,
                    "branch": branch,
                },
            ),
            f"delete file '{path}'",
        )
        _check_response(resp, f"delete file '{path}'")


# ── Sanitization ─────────────────────────────────────────────────

_TOKEN_PATTERNS = re.compile(
    r"Bearer\s+[^\s\"]+|"
    r"ghp_[a-zA-Z0-9]+|"
    r"gho_[a-zA-Z0-9]+|"
    r"ghu_[a-zA-Z0-9]+|"
    r"ghs_[a-zA-Z0-9]+|"
    r"ghr_[a-zA-Z0-9]+|"
    r"github_pat_[a-zA-Z0-9_]+|"
    r"Authorization:\s*[^\n]+|"
    r"token\s+[^\s\"]+",
    re.IGNORECASE,
)


def _sanitize_response_body(text: str) -> str:
    """Strip secrets from a GitHub API response body before logging.

    Args:
        text: Raw response text (truncated).

    Returns:
        Text with token-like patterns replaced by ``[REDACTED]``.
    """
    return _TOKEN_PATTERNS.sub("[REDACTED]", text)


# ── Response helpers ─────────────────────────────────────────────


def _is_missing_ref(resp: httpx.Response) -> bool:
    """Check if a 422 response indicates a missing git reference.

    Args:
        resp: The 422 response from GitHub.

    Returns:
        True if the error is "Reference does not exist".
    """
    try:
        data = resp.json()
    except ValueError, TypeError:
        return False
    msg = data.get("message", "")
    if "Reference does not exist" in msg:
        return True
    for err in data.get("errors", []):
        if isinstance(err, dict) and err.get("code") == "missing":
            return True
    return False


def _check_response(resp: httpx.Response, action: str) -> None:
    """Raise on non-2xx responses with sanitized error details.

    Args:
        resp: The httpx response.
        action: Human-readable action description for the error message.

    Raises:
        GitHubAuthError: On 401, or 403 without rate-limit indicators.
        GitHubRateLimitError: On 429, or 403 carrying a rate-limit
            signal (Retry-After / x-ratelimit-remaining: 0 / secondary
            rate-limit body). Carries Retry-After when present.
        GitHubAPIError: On other non-2xx responses.
    """
    if resp.is_success:
        return
    raw = resp.text[:500] if resp.text else "(empty)"
    body = _sanitize_response_body(raw)
    logger.error(
        META_CODE_GITHUB_API_FAILED,
        action=action,
        status_code=resp.status_code,
        response_body=body,
    )
    # GitHub signals both primary and secondary rate limits with 403 (not
    # only 429): a ``Retry-After`` header, ``x-ratelimit-remaining: 0``, or
    # secondary-rate-limit body text. Classify those as rate limits BEFORE
    # the plain auth 403 check so ``_send``'s retry handler (which only
    # retries ``GitHubRateLimitError``) honours ``Retry-After`` instead of
    # aborting permanently as an auth failure.
    retry_after_header = resp.headers.get("retry-after")
    is_rate_limited = resp.status_code == _HTTP_TOO_MANY_REQUESTS or (
        resp.status_code == _HTTP_FORBIDDEN
        and (
            retry_after_header is not None
            or resp.headers.get("x-ratelimit-remaining") == "0"
            or "secondary rate limit" in raw.lower()
        )
    )
    if is_rate_limited:
        retry_after = coerce_finite_nonneg_seconds(
            parse_retry_after_seconds(retry_after_header)
        )
        raise GitHubRateLimitError(
            status_code=resp.status_code,
            action=action,
            body=body,
            retry_after_seconds=retry_after,
        )
    if resp.status_code in {_HTTP_UNAUTHORIZED, _HTTP_FORBIDDEN}:
        raise GitHubAuthError(
            status_code=resp.status_code,
            action=action,
            body=body,
        )
    raise GitHubAPIError(
        status_code=resp.status_code,
        action=action,
        body=body,
    )
