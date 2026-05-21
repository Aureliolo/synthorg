"""External-remote git backend: GitHub/GitLab/Gitea/Forgejo via catalog.

Resolves the forge connection from the connection catalog, injects a
token for HTTPS auth, and hardens the clone/push/fetch path:

- Transient push/fetch failures (and forge rate-limits) retry with
  exponential backoff via :class:`GeneralRetryHandler` (Pattern A in
  ``docs/reference/retry-patterns.md``); auth failures do not retry.
- A push to a not-yet-created forge repo is provisioned lazily: when a
  push fails and the forge REST API confirms the repo is missing, the
  repo is created (per-forge :class:`ForgeApiClient`) and the push is
  retried once. ``provision`` mirrors this by initialising a local
  working tree when the remote does not exist yet, so the first push
  creates it.

Tokens travel in the ``Authorization`` header (forge API) or as
percent-encoded HTTPS userinfo (git); both paths are redacted before
any log line.
"""

import asyncio
import re
from pathlib import Path  # noqa: TC003 -- runtime annotation (PEP 649)
from typing import TYPE_CHECKING, Final
from urllib.parse import quote, urlsplit, urlunsplit

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.enums import GitBackendType
from synthorg.core.resilience import GeneralRetryHandler
from synthorg.core.types import NotBlankStr
from synthorg.engine.errors import (
    GitBackendConfigError,
    GitBackendFetchError,
    GitBackendForgeApiError,
    GitBackendForgeAuthError,
    GitBackendProvisionError,
    GitBackendPushError,
    GitBackendRateLimitError,
    GitBackendRemoteMissingError,
)
from synthorg.engine.workspace._git_subprocess import _redact_args, run_git_subprocess
from synthorg.engine.workspace.git_backend._git_ops import (
    REMOTE_NAME,
    git,
    init_working_tree_with_remote,
    is_git_repo,
)
from synthorg.engine.workspace.git_backend.config import GitBackendResilienceConfig
from synthorg.engine.workspace.git_backend.forge_api import build_forge_api_client
from synthorg.engine.workspace.git_backend.protocol import (
    FetchResult,
    ProvisionResult,
    PushResult,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.workspace import (
    GIT_BACKEND_FETCH_COMPLETE,
    GIT_BACKEND_FETCH_FAILED,
    GIT_BACKEND_PROVISION_COMPLETE,
    GIT_BACKEND_PROVISION_FAILED,
    GIT_BACKEND_PROVISION_START,
    GIT_BACKEND_PUSH_COMPLETE,
    GIT_BACKEND_PUSH_FAILED,
    GIT_BACKEND_PUSH_RETRY,
    GIT_BACKEND_REMOTE_PROVISIONED,
)

if TYPE_CHECKING:
    from synthorg.integrations.connections.catalog import ConnectionCatalog
    from synthorg.integrations.connections.models import Connection

logger = get_logger(__name__)

_TOKEN_USER: Final[str] = "x-access-token"  # noqa: S105 -- username, not a secret
# Bare-construction fallback for the forge REST timeout; the factory
# passes the operator-tuned ``GitBackendConfig.forge_api_timeout_seconds``.
_DEFAULT_FORGE_API_TIMEOUT_SECONDS: Final[float] = 30.0

# git stderr markers that classify a failed push without a forge-API
# round-trip. Auth is non-retryable; rate-limit retries with backoff.
# Ambiguous failures fall through to a forge-API existence check.
_AUTH_MARKERS: Final[tuple[str, ...]] = (
    "authentication failed",
    "invalid username or password",
    "permission denied",
    "403 forbidden",
    "401 unauthorized",
)
_RATE_LIMIT_MARKERS: Final[tuple[str, ...]] = (
    "rate limit",
    "too many requests",
    "429",
)


def _is_retryable_git_op(exc: Exception) -> bool:
    """Predicate for the transient-I/O retry handler.

    Retries transient push/fetch failures, forge rate-limits, and
    transient forge-API errors. Never retries auth failures or a
    confirmed-missing remote (the latter is handled by lazy creation,
    not backoff).
    """
    if isinstance(exc, GitBackendRemoteMissingError | GitBackendForgeAuthError):
        return False
    return isinstance(
        exc,
        GitBackendPushError
        | GitBackendFetchError
        | GitBackendRateLimitError
        | GitBackendForgeApiError,
    )


def _matches(haystack: str, markers: tuple[str, ...]) -> bool:
    return any(marker in haystack for marker in markers)


# git echoes the failing remote URL in clone/push stderr, and that URL
# carries the percent-encoded token as HTTPS userinfo. Mask the
# userinfo before any stderr text reaches a log line so the token is
# never persisted.
_URL_USERINFO: Final[re.Pattern[str]] = re.compile(r"(\w+://)[^/@\s]+@")


def _redact_stderr(text: str) -> str:
    """Strip ``user:token@`` userinfo from any URL in git stderr."""
    return _URL_USERINFO.sub(r"\1[REDACTED]@", text)


class ExternalRemoteGitBackend:
    """Forge-remote git backend resolved via the connection catalog."""

    def __init__(  # noqa: PLR0913 -- forge + retry config is the boundary surface
        self,
        *,
        connection_name: str,
        connection_catalog: ConnectionCatalog,
        cmd_timeout: float,
        resilience: GitBackendResilienceConfig | None = None,
        forge_provisioning_enabled: bool = True,
        forge_repo_private: bool = True,
        forge_api_timeout: float = _DEFAULT_FORGE_API_TIMEOUT_SECONDS,
        clock: Clock | None = None,
    ) -> None:
        self._connection_name = connection_name
        self._catalog = connection_catalog
        self._cmd_timeout = cmd_timeout
        self._forge_provisioning_enabled = forge_provisioning_enabled
        self._forge_repo_private = forge_repo_private
        self._forge_api_timeout = forge_api_timeout
        self._clock: Clock = clock if clock is not None else SystemClock()
        cfg = resilience if resilience is not None else GitBackendResilienceConfig()
        self._retry = GeneralRetryHandler(
            retryable=_is_retryable_git_op,
            max_attempts=cfg.max_attempts,
            base=cfg.base_delay_seconds,
            cap=cfg.cap_delay_seconds,
            event=GIT_BACKEND_PUSH_RETRY,
            jitter=cfg.jitter,
            clock=self._clock,
        )

    def get_backend_type(self) -> GitBackendType:
        """Return the ``EXTERNAL_REMOTE`` discriminator."""
        return GitBackendType.EXTERNAL_REMOTE

    async def _connection(self) -> Connection:
        connection = await self._catalog.get(self._connection_name)
        if connection is None or not connection.base_url:
            msg = (
                f"external git connection {self._connection_name!r} is not "
                "registered or has no base_url"
            )
            raise GitBackendConfigError(msg)
        return connection

    async def _token(self) -> str:
        # ``get_credentials`` returns ``dict[str, str]`` per its protocol
        # (never None); a missing ``token`` key surfaces as the same
        # typed config error a None return would have taken.
        credentials = await self._catalog.get_credentials(self._connection_name)
        token = credentials.get("token")
        if not token:
            msg = (
                f"external git connection {self._connection_name!r} has no "
                "'token' credential"
            )
            raise GitBackendConfigError(msg)
        return token

    def _split_base(self, connection: Connection) -> tuple[str, str, str, str]:
        """Return ``(scheme, host_with_port, path, owner)`` from base_url."""
        split = urlsplit(str(connection.base_url).rstrip("/"))
        if split.scheme != "https":
            msg = "external git remote must be an https URL"
            raise GitBackendConfigError(msg)
        host = split.hostname
        if not host:
            msg = (
                f"external git connection {self._connection_name!r} has "
                "invalid base_url (no host component)"
            )
            raise GitBackendConfigError(msg)
        host_with_port = f"{host}:{split.port}" if split.port is not None else host
        owner = split.path.strip("/")
        return split.scheme, host_with_port, split.path, owner

    async def _authenticated_remote_url(self, project_id: str) -> str:
        """Resolve ``<base_url>/<project_id>.git`` with a token injected."""
        connection = await self._connection()
        token = await self._token()
        scheme, host_with_port, path, _owner = self._split_base(connection)
        # Use hostname/port instead of raw netloc so any pre-existing
        # userinfo on the configured base_url cannot collide with the
        # token; percent-encode the token so reserved characters survive
        # URL parsing rather than breaking the netloc.
        netloc = f"{_TOKEN_USER}:{quote(token, safe='')}@{host_with_port}"
        repo_segment = quote(project_id, safe="")
        return urlunsplit((scheme, netloc, f"{path}/{repo_segment}.git", "", ""))

    async def provision(
        self,
        *,
        project_id: NotBlankStr,
        workspace_path: Path,
        default_branch: NotBlankStr,
    ) -> ProvisionResult:
        """Clone the forge remote, or init locally if it does not exist."""
        pid = str(project_id)
        logger.info(
            GIT_BACKEND_PROVISION_START,
            project_id=pid,
            backend=GitBackendType.EXTERNAL_REMOTE.value,
        )
        if await is_git_repo(workspace_path, cmd_timeout=self._cmd_timeout):
            return ProvisionResult(
                repo_root=NotBlankStr(str(workspace_path)),
                default_branch=default_branch,
                newly_created=False,
            )
        url = await self._authenticated_remote_url(pid)
        try:
            await asyncio.to_thread(workspace_path.mkdir, parents=True, exist_ok=True)
        except OSError as exc:
            msg = f"failed to create workspace dir for {pid!r}"
            raise GitBackendProvisionError(msg) from exc
        rc, _stdout, stderr = await run_git_subprocess(
            workspace_path,
            "clone",
            url,
            ".",
            cmd_timeout=self._cmd_timeout,
            log_event=GIT_BACKEND_PROVISION_FAILED,
        )
        if rc == 0:
            logger.info(
                GIT_BACKEND_PROVISION_COMPLETE,
                project_id=pid,
                backend=GitBackendType.EXTERNAL_REMOTE.value,
                cloned=True,
            )
            return ProvisionResult(
                repo_root=NotBlankStr(str(workspace_path)),
                default_branch=default_branch,
                newly_created=True,
            )
        await self._handle_clone_failure(pid, workspace_path, stderr, default_branch)
        logger.info(
            GIT_BACKEND_PROVISION_COMPLETE,
            project_id=pid,
            backend=GitBackendType.EXTERNAL_REMOTE.value,
            cloned=False,
        )
        return ProvisionResult(
            repo_root=NotBlankStr(str(workspace_path)),
            default_branch=default_branch,
            newly_created=True,
        )

    async def _handle_clone_failure(
        self,
        pid: str,
        workspace_path: Path,
        stderr: str,
        default_branch: NotBlankStr,
    ) -> None:
        """Init a local tree when the remote is missing; else fail."""
        lowered = stderr.lower()
        # Surface the clone stderr before any classification branch so a
        # clone failure that is neither auth nor missing-remote (the
        # branch that re-raises a generic provision error) still leaves
        # the operator the git diagnostic instead of an opaque message.
        logger.warning(
            GIT_BACKEND_PROVISION_FAILED,
            project_id=pid,
            reason="clone_failed",
            stderr=_redact_stderr(stderr),
        )
        if _matches(lowered, _AUTH_MARKERS):
            logger.warning(
                GIT_BACKEND_PROVISION_FAILED,
                project_id=pid,
                reason="clone_auth_failed",
            )
            msg = f"forge authentication failed cloning project {pid!r}"
            raise GitBackendProvisionError(msg)
        if self._forge_provisioning_enabled and not await self._remote_repo_exists(pid):
            await init_working_tree_with_remote(
                workspace_path,
                default_branch=str(default_branch),
                remote_url=await self._authenticated_remote_url(pid),
                cmd_timeout=self._cmd_timeout,
                fail_exc=GitBackendProvisionError,
                project_id=pid,
            )
            return
        msg = f"failed to clone forge remote for project {pid!r}"
        raise GitBackendProvisionError(msg)

    async def push(
        self,
        *,
        project_id: NotBlankStr,
        repo_root: Path,
        branch: NotBlankStr,
        base_branch: NotBlankStr,  # noqa: ARG002 -- remote tracks its own base
    ) -> PushResult:
        """Push *branch*, lazily creating the forge repo if it is missing."""
        pid = str(project_id)

        async def _attempt() -> None:
            await self._do_push(repo_root, str(branch), pid)

        try:
            await self._retry.execute(_attempt, project_id=pid, branch=str(branch))
        except GitBackendRemoteMissingError:

            async def _create() -> None:
                await self._provision_remote_repo(pid)

            # Run lazy creation under the same retry policy so a transient
            # forge rate-limit / 5xx during create_repo is retried rather
            # than aborting the push.
            await self._retry.execute(_create, project_id=pid)
            await self._retry.execute(_attempt, project_id=pid, branch=str(branch))
        head = await git(
            repo_root,
            "rev-parse",
            str(branch),
            cmd_timeout=self._cmd_timeout,
            fail_exc=GitBackendPushError,
            project_id=pid,
            event=GIT_BACKEND_PUSH_FAILED,
        )
        logger.info(GIT_BACKEND_PUSH_COMPLETE, project_id=pid, branch=str(branch))
        return PushResult(branch=branch, head_sha=NotBlankStr(head))

    async def _do_push(self, repo_root: Path, branch: str, pid: str) -> None:
        """Run one push attempt; classify a failure into a typed error."""
        rc, _stdout, stderr = await run_git_subprocess(
            repo_root,
            "push",
            REMOTE_NAME,
            branch,
            cmd_timeout=self._cmd_timeout,
            log_event=GIT_BACKEND_PUSH_FAILED,
        )
        if rc == 0:
            return
        lowered = stderr.lower()
        logger.warning(
            GIT_BACKEND_PUSH_FAILED,
            project_id=pid,
            git_args=_redact_args(("push", REMOTE_NAME)),
            return_code=rc,
        )
        if _matches(lowered, _AUTH_MARKERS):
            msg = f"forge authentication failed pushing project {pid!r}"
            raise GitBackendForgeAuthError(msg)
        if _matches(lowered, _RATE_LIMIT_MARKERS):
            msg = f"forge rate-limited pushing project {pid!r}"
            raise GitBackendRateLimitError(msg)
        if self._forge_provisioning_enabled and not await self._remote_repo_exists(pid):
            msg = f"forge repo for project {pid!r} does not exist"
            raise GitBackendRemoteMissingError(msg)
        msg = f"git push failed for project {pid!r} (rc={rc})"
        raise GitBackendPushError(msg)

    async def fetch(
        self,
        *,
        project_id: NotBlankStr,
        repo_root: Path,
        branch: NotBlankStr | None = None,
    ) -> FetchResult:
        """Fetch from the forge remote into *repo_root* (retried)."""
        pid = str(project_id)
        args = ["fetch", REMOTE_NAME]
        if branch is not None:
            args.append(str(branch))

        async def _attempt() -> None:
            await self._do_fetch(repo_root, args, pid=pid)

        await self._retry.execute(_attempt, project_id=pid)
        logger.info(GIT_BACKEND_FETCH_COMPLETE, project_id=pid)
        refs: tuple[NotBlankStr, ...] = (
            (NotBlankStr(str(branch)),) if branch is not None else ()
        )
        return FetchResult(updated_refs=refs)

    async def _do_fetch(self, repo_root: Path, args: list[str], *, pid: str) -> None:
        """Run one fetch attempt; classify a failure into a typed error.

        Mirrors :meth:`_do_push` so an auth failure raises the
        non-retryable :class:`GitBackendForgeAuthError` instead of the
        retryable :class:`GitBackendFetchError` that ``git`` would wrap
        every non-zero exit as -- otherwise the retry handler would burn
        attempts re-running a fetch that can only ever fail.
        """
        rc, _stdout, stderr = await run_git_subprocess(
            repo_root,
            *args,
            cmd_timeout=self._cmd_timeout,
            log_event=GIT_BACKEND_FETCH_FAILED,
        )
        if rc == 0:
            return
        lowered = stderr.lower()
        logger.warning(
            GIT_BACKEND_FETCH_FAILED,
            project_id=pid,
            git_args=_redact_args(("fetch", REMOTE_NAME)),
            return_code=rc,
        )
        if _matches(lowered, _AUTH_MARKERS):
            msg = f"forge authentication failed fetching project {pid!r}"
            raise GitBackendForgeAuthError(msg)
        if _matches(lowered, _RATE_LIMIT_MARKERS):
            msg = f"forge rate-limited fetching project {pid!r}"
            raise GitBackendRateLimitError(msg)
        msg = f"git fetch failed for project {pid!r} (rc={rc})"
        raise GitBackendFetchError(msg)

    async def _remote_repo_exists(self, pid: str) -> bool:
        """Check the forge REST API for ``<owner>/<project_id>``."""
        connection = await self._connection()
        token = await self._token()
        _scheme, _host, _path, owner = self._split_base(connection)
        if not owner:
            # An ownerless base_url cannot name a forge repo; reject the
            # connection as misconfigured rather than silently treating
            # the repo as missing (which would provision local-only).
            logger.warning(
                GIT_BACKEND_PROVISION_FAILED,
                project_id=pid,
                action="forge_repo_exists",
                reason="base_url_missing_owner",
            )
            msg = (
                f"cannot resolve forge repo for project {pid!r}: base_url "
                "has no owner/namespace path component"
            )
            raise GitBackendConfigError(msg)
        client = build_forge_api_client(
            connection_type=connection.connection_type,
            base_url=str(connection.base_url),
            token=token,
            timeout=self._forge_api_timeout,
        )
        try:
            return await client.repo_exists(
                owner=NotBlankStr(owner),
                repo=NotBlankStr(pid),
            )
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            logger.warning(
                GIT_BACKEND_PROVISION_FAILED,
                project_id=pid,
                action="forge_repo_exists",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise
        finally:
            await client.aclose()

    async def _provision_remote_repo(self, pid: str) -> None:
        """Create ``<owner>/<project_id>`` on the forge via its REST API."""
        connection = await self._connection()
        token = await self._token()
        _scheme, _host, _path, owner = self._split_base(connection)
        if not owner:
            logger.warning(
                GIT_BACKEND_PROVISION_FAILED,
                project_id=pid,
                action="forge_create_repo",
                reason="base_url_missing_owner",
            )
            msg = (
                f"cannot provision forge repo for project {pid!r}: base_url "
                "has no owner/namespace path component"
            )
            raise GitBackendConfigError(msg)
        client = build_forge_api_client(
            connection_type=connection.connection_type,
            base_url=str(connection.base_url),
            token=token,
            timeout=self._forge_api_timeout,
        )
        try:
            repo = await client.create_repo(
                owner=NotBlankStr(owner),
                repo=NotBlankStr(pid),
                private=self._forge_repo_private,
            )
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            logger.warning(
                GIT_BACKEND_PROVISION_FAILED,
                project_id=pid,
                action="forge_create_repo",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise
        finally:
            await client.aclose()
        logger.info(
            GIT_BACKEND_REMOTE_PROVISIONED,
            project_id=pid,
            full_name=str(repo.full_name),
            private=repo.private,
        )


__all__ = ["ExternalRemoteGitBackend"]
