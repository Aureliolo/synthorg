"""Writes serialised :class:`BrainEntry` snapshots to the project workspace.

A brain snapshot write follows this sequence:

1. Resolve the persistent workspace via :class:`ProjectWorkspaceService`.
2. Ensure the dedicated docs branch (:data:`BRAIN_BRANCH_NAME`) exists locally,
   branching from the project's default branch on first use.
3. Write the deterministic JSON at
   ``<workspace>/<BRAIN_WORKSPACE_SUBDIR>/<kind>/<entry_id>.json``.
4. Stage + commit on the branch with a stable subject carrying the revision.
5. Push via the configured :class:`GitBackend`.

The git snapshot is a versioned, human-readable copy that travels with the
project; the authoritative structured store is the SQL append. The writer is the
only brain-engine module that talks to git directly. Concurrent writes on the
same project sequence through one ``asyncio.Lock`` here (and the service holds a
coarser per-project lock over the whole append).
"""

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.engine.workspace._git_subprocess import run_git_subprocess
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.project_brain import (
    BRAIN_ENTRY_COMMIT_PUSHED,
    BRAIN_SNAPSHOT_FAILED,
    BRAIN_SNAPSHOT_WRITTEN,
)
from synthorg.project_brain.constants import (
    BRAIN_BRANCH_NAME,
    BRAIN_WORKSPACE_SUBDIR,
)
from synthorg.project_brain.errors import BrainCommitError
from synthorg.project_brain.serializer import serialize_entry

if TYPE_CHECKING:
    from synthorg.engine.workspace.git_backend import GitBackend
    from synthorg.engine.workspace.project_workspace_service import (
        ProjectWorkspaceService,
    )
    from synthorg.project_brain.models import BrainEntry

logger = get_logger(__name__)

_GIT_CMD_TIMEOUT_SECONDS: float = 30.0


class BrainWriter:
    """Serialises brain entries and commits them on the project docs branch."""

    __slots__ = ("_git_backend", "_locks", "_locks_guard", "_workspace_service")

    def __init__(
        self,
        *,
        workspace_service: ProjectWorkspaceService,
        git_backend: GitBackend,
    ) -> None:
        self._workspace_service = workspace_service
        self._git_backend = git_backend
        self._locks: dict[str, asyncio.Lock] = {}
        self._locks_guard = asyncio.Lock()

    async def _lock_for(self, project_id: str) -> asyncio.Lock:
        """Return the per-project commit lock, creating it on first use.

        Returns:
            The ``asyncio.Lock`` guarding workspace commits for the project.
        """
        async with self._locks_guard:
            return self._locks.setdefault(project_id, asyncio.Lock())

    async def write(
        self,
        *,
        project_id: NotBlankStr,
        entry: BrainEntry,
    ) -> NotBlankStr:
        """Serialise *entry* and commit it on the docs branch.

        Args:
            project_id: Owning project.
            entry: The entry revision to materialise.

        Returns:
            The commit hash of the snapshot write.

        Raises:
            BrainCommitError: If any phase (workspace resolution, file write,
                git add/commit, or push) failed.
        """
        lock = await self._lock_for(project_id)
        async with lock:
            return await self._write_locked(project_id=project_id, entry=entry)

    async def _write_locked(
        self,
        *,
        project_id: NotBlankStr,
        entry: BrainEntry,
    ) -> NotBlankStr:
        try:
            workspace = await self._workspace_service.get_or_provision(project_id)
            repo_root = Path(workspace.workspace_path)
            await self._ensure_branch(
                repo_root=repo_root,
                default_branch=workspace.default_branch,
            )
            entry_path = self._entry_path(repo_root=repo_root, entry=entry)
            await asyncio.to_thread(
                entry_path.parent.mkdir, parents=True, exist_ok=True
            )
            await asyncio.to_thread(entry_path.write_bytes, serialize_entry(entry))
            commit_sha = await self._stage_commit(
                repo_root=repo_root, entry_path=entry_path, entry=entry
            )
            await self._git_backend.push(
                project_id=project_id,
                repo_root=repo_root,
                branch=BRAIN_BRANCH_NAME,
                base_branch=workspace.default_branch,
            )
        except BrainCommitError:
            raise
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                BRAIN_SNAPSHOT_FAILED,
                project_id=project_id,
                entry_id=entry.entry_id,
                revision=entry.revision,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = (
                f"Failed to write brain snapshot {project_id!r}/{entry.entry_id!r} "
                f"on {BRAIN_BRANCH_NAME!r}"
            )
            raise BrainCommitError(msg) from exc
        logger.info(
            BRAIN_SNAPSHOT_WRITTEN,
            project_id=project_id,
            entry_id=entry.entry_id,
            entry_kind=entry.entry_kind.value,
            revision=entry.revision,
            commit_sha=commit_sha,
        )
        logger.info(
            BRAIN_ENTRY_COMMIT_PUSHED,
            project_id=project_id,
            entry_id=entry.entry_id,
            branch=BRAIN_BRANCH_NAME,
            commit_sha=commit_sha,
        )
        return commit_sha

    @staticmethod
    def _entry_path(*, repo_root: Path, entry: BrainEntry) -> Path:
        """Return the canonical on-disk path for *entry*.

        Returns:
            ``<repo_root>/<subdir>/<kind>/<entry_id>.json``.
        """
        return (
            repo_root
            / BRAIN_WORKSPACE_SUBDIR
            / entry.entry_kind.value
            / f"{entry.entry_id}.json"
        )

    async def _ensure_branch(
        self,
        *,
        repo_root: Path,
        default_branch: NotBlankStr,
    ) -> None:
        """Switch to (or create) the brain/docs branch in *repo_root*.

        Raises:
            BrainCommitError: When the checkout or branch-create git command
                fails.
        """
        rc, stdout, _ = await run_git_subprocess(
            repo_root,
            "rev-parse",
            "--verify",
            f"refs/heads/{BRAIN_BRANCH_NAME}",
            cmd_timeout=_GIT_CMD_TIMEOUT_SECONDS,
            log_event=BRAIN_SNAPSHOT_FAILED,
        )
        if rc == 0 and stdout.strip():
            rc_checkout, _, stderr = await run_git_subprocess(
                repo_root,
                "checkout",
                BRAIN_BRANCH_NAME,
                cmd_timeout=_GIT_CMD_TIMEOUT_SECONDS,
                log_event=BRAIN_SNAPSHOT_FAILED,
            )
            if rc_checkout != 0:
                msg = (
                    f"git checkout {BRAIN_BRANCH_NAME} failed: "
                    f"{stderr.strip() or 'unknown error'}"
                )
                raise BrainCommitError(msg)
            return
        rc_branch, _, stderr = await run_git_subprocess(
            repo_root,
            "checkout",
            "-b",
            BRAIN_BRANCH_NAME,
            default_branch,
            cmd_timeout=_GIT_CMD_TIMEOUT_SECONDS,
            log_event=BRAIN_SNAPSHOT_FAILED,
        )
        if rc_branch != 0:
            msg = (
                f"git checkout -b {BRAIN_BRANCH_NAME} from "
                f"{default_branch} failed: {stderr.strip() or 'unknown error'}"
            )
            raise BrainCommitError(msg)

    async def _stage_commit(
        self,
        *,
        repo_root: Path,
        entry_path: Path,
        entry: BrainEntry,
    ) -> NotBlankStr:
        """Stage and commit *entry_path*; return the new HEAD SHA.

        Returns:
            The new HEAD commit SHA after staging and committing the snapshot.

        Raises:
            BrainCommitError: When the ``git add`` or ``git commit`` step fails.
        """
        rel = entry_path.relative_to(repo_root).as_posix()
        rc_add, _, stderr_add = await run_git_subprocess(
            repo_root,
            "add",
            "--",
            rel,
            cmd_timeout=_GIT_CMD_TIMEOUT_SECONDS,
            log_event=BRAIN_SNAPSHOT_FAILED,
        )
        if rc_add != 0:
            msg = f"git add {rel} failed: {stderr_add.strip() or 'unknown error'}"
            raise BrainCommitError(msg)
        message = f"brain({entry.entry_kind.value}): {entry.entry_id} r{entry.revision}"
        rc_commit, _, stderr_commit = await run_git_subprocess(
            repo_root,
            "-c",
            "user.email=brain@synthorg.local",
            "-c",
            "user.name=SynthOrg Project Brain",
            "commit",
            "--allow-empty",
            "-m",
            message,
            cmd_timeout=_GIT_CMD_TIMEOUT_SECONDS,
            log_event=BRAIN_SNAPSHOT_FAILED,
        )
        if rc_commit != 0:
            msg = (
                f"git commit failed for {rel}: "
                f"{stderr_commit.strip() or 'unknown error'}"
            )
            raise BrainCommitError(msg)
        rc_sha, sha_out, stderr_sha = await run_git_subprocess(
            repo_root,
            "rev-parse",
            "HEAD",
            cmd_timeout=_GIT_CMD_TIMEOUT_SECONDS,
            log_event=BRAIN_SNAPSHOT_FAILED,
        )
        if rc_sha != 0 or not sha_out.strip():
            msg = f"git rev-parse HEAD failed: {stderr_sha.strip() or 'no output'}"
            raise BrainCommitError(msg)
        return NotBlankStr(sha_out.strip())
