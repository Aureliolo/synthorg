"""External-remote git backend: GitHub/GitLab/Gitea/Forgejo via catalog.

Thin glue over the existing connection catalog + secret resolution.
The remote is addressed as ``<connection.base_url>/<project_id>.git``
with a token injected for HTTPS auth.  Deep hardening (OAuth token
refresh, expiry, rate-limit + retry, forge-API repo creation) is the
tracked follow-up issue; this ships the protocol + config surface so
switching the git backend is a config change only.
"""

import asyncio
from pathlib import Path  # noqa: TC003 -- runtime annotation (PEP 649)
from typing import TYPE_CHECKING, Final
from urllib.parse import urlsplit, urlunsplit

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.enums import GitBackendType
from synthorg.core.types import NotBlankStr
from synthorg.engine.errors import (
    GitBackendConfigError,
    GitBackendFetchError,
    GitBackendProvisionError,
    GitBackendPushError,
)
from synthorg.engine.workspace.git_backend._git_ops import git, is_git_repo
from synthorg.engine.workspace.git_backend.protocol import (
    FetchResult,
    ProvisionResult,
    PushResult,
)
from synthorg.observability import get_logger
from synthorg.observability.events.workspace import (
    GIT_BACKEND_FETCH_COMPLETE,
    GIT_BACKEND_FETCH_FAILED,
    GIT_BACKEND_PROVISION_COMPLETE,
    GIT_BACKEND_PROVISION_START,
    GIT_BACKEND_PUSH_COMPLETE,
    GIT_BACKEND_PUSH_FAILED,
)

if TYPE_CHECKING:
    from synthorg.integrations.connections.catalog import ConnectionCatalog

logger = get_logger(__name__)

_REMOTE_NAME: Final[str] = "origin"
_TOKEN_USER: Final[str] = "x-access-token"  # noqa: S105 -- username, not a secret


class ExternalRemoteGitBackend:
    """Forge-remote git backend resolved via the connection catalog."""

    def __init__(
        self,
        *,
        connection_name: str,
        connection_catalog: ConnectionCatalog,
        cmd_timeout: float,
        clock: Clock | None = None,
    ) -> None:
        self._connection_name = connection_name
        self._catalog = connection_catalog
        self._cmd_timeout = cmd_timeout
        self._clock: Clock = clock if clock is not None else SystemClock()

    def get_backend_type(self) -> GitBackendType:
        """Return the ``EXTERNAL_REMOTE`` discriminator."""
        return GitBackendType.EXTERNAL_REMOTE

    async def _authenticated_remote_url(self, project_id: str) -> str:
        """Resolve ``<base_url>/<project_id>.git`` with a token injected."""
        connection = await self._catalog.get(self._connection_name)
        if connection is None or not connection.base_url:
            msg = (
                f"external git connection {self._connection_name!r} is not "
                "registered or has no base_url"
            )
            raise GitBackendConfigError(msg)
        credentials = await self._catalog.get_credentials(self._connection_name)
        token = credentials.get("token")
        if not token:
            msg = (
                f"external git connection {self._connection_name!r} has no "
                "'token' credential"
            )
            raise GitBackendConfigError(msg)
        split = urlsplit(str(connection.base_url).rstrip("/"))
        if split.scheme != "https":
            msg = "external git remote must be an https URL"
            raise GitBackendConfigError(msg)
        netloc = f"{_TOKEN_USER}:{token}@{split.netloc}"
        path = f"{split.path}/{project_id}.git"
        return urlunsplit((split.scheme, netloc, path, "", ""))

    async def provision(
        self,
        *,
        project_id: NotBlankStr,
        workspace_path: Path,
        default_branch: NotBlankStr,
    ) -> ProvisionResult:
        """Clone the resolved forge remote into *workspace_path*."""
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
        await git(
            workspace_path,
            "clone",
            url,
            ".",
            cmd_timeout=self._cmd_timeout,
            fail_exc=GitBackendProvisionError,
            project_id=pid,
        )
        logger.info(
            GIT_BACKEND_PROVISION_COMPLETE,
            project_id=pid,
            backend=GitBackendType.EXTERNAL_REMOTE.value,
        )
        return ProvisionResult(
            repo_root=NotBlankStr(str(workspace_path)),
            default_branch=default_branch,
            newly_created=True,
        )

    async def push(
        self,
        *,
        project_id: NotBlankStr,
        repo_root: Path,
        branch: NotBlankStr,
        base_branch: NotBlankStr,  # noqa: ARG002 -- remote tracks its own base
    ) -> PushResult:
        """Push *branch* to the forge remote; return its head SHA."""
        pid = str(project_id)
        await git(
            repo_root,
            "push",
            _REMOTE_NAME,
            str(branch),
            cmd_timeout=self._cmd_timeout,
            fail_exc=GitBackendPushError,
            project_id=pid,
            event=GIT_BACKEND_PUSH_FAILED,
        )
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

    async def fetch(
        self,
        *,
        project_id: NotBlankStr,
        repo_root: Path,
        branch: NotBlankStr | None = None,
    ) -> FetchResult:
        """Fetch from the forge remote into *repo_root*."""
        pid = str(project_id)
        args = ["fetch", _REMOTE_NAME]
        if branch is not None:
            args.append(str(branch))
        await git(
            repo_root,
            *args,
            cmd_timeout=self._cmd_timeout,
            fail_exc=GitBackendFetchError,
            project_id=pid,
            event=GIT_BACKEND_FETCH_FAILED,
        )
        logger.info(GIT_BACKEND_FETCH_COMPLETE, project_id=pid)
        refs: tuple[NotBlankStr, ...] = (
            (NotBlankStr(str(branch)),) if branch is not None else ()
        )
        return FetchResult(updated_refs=refs)


__all__ = ["ExternalRemoteGitBackend"]
