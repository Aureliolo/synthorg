"""Planner-worktrees workspace isolation strategy.

Uses git worktrees to provide each agent with an isolated working
directory backed by its own branch.
"""

import asyncio
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from synthorg.core.enums import ConflictType
from synthorg.engine.errors import (
    WorkspaceCleanupError,
    WorkspaceError,
    WorkspaceLimitError,
    WorkspaceMergeError,
    WorkspaceSetupError,
)
from synthorg.engine.workspace.config import PlannerWorktreesConfig  # noqa: TC001
from synthorg.engine.workspace.models import (
    MergeConflict,
    MergeResult,
    Workspace,
    WorkspaceRequest,
)
from synthorg.observability import get_logger
from synthorg.observability.events.workspace import (
    WORKSPACE_LIMIT_REACHED,
    WORKSPACE_MERGE_ABORT_FAILED,
    WORKSPACE_MERGE_COMPLETE,
    WORKSPACE_MERGE_CONFLICT,
    WORKSPACE_MERGE_FAILED,
    WORKSPACE_MERGE_START,
    WORKSPACE_SETUP_COMPLETE,
    WORKSPACE_SETUP_FAILED,
    WORKSPACE_SETUP_START,
    WORKSPACE_TEARDOWN_COMPLETE,
    WORKSPACE_TEARDOWN_FAILED,
    WORKSPACE_TEARDOWN_START,
)

logger = get_logger(__name__)

_SAFE_REF_RE = re.compile(r"^[A-Za-z0-9._/-]+$")


def _validate_git_ref(
    value: str,
    label: str,
    *,
    error_cls: type[WorkspaceError] = WorkspaceSetupError,
    event: str = WORKSPACE_SETUP_FAILED,
) -> None:
    """Validate that a string is safe for use as a git command argument.

    Prevents argument injection and path traversal. Does not fully
    validate git ref format rules (e.g. consecutive slashes).

    Args:
        value: The string to validate.
        label: Human-readable label for error messages.
        error_cls: ``WorkspaceError`` subclass to raise on failure.
            Defaults to ``WorkspaceSetupError``; callers in
            merge/teardown contexts pass the appropriate type.
        event: Log event constant for the failure message.

    Raises:
        WorkspaceError: The subclass specified by ``error_cls``.
            Defaults to ``WorkspaceSetupError``; merge contexts
            use ``WorkspaceMergeError``, teardown contexts use
            ``WorkspaceCleanupError``.
    """
    if (
        not value
        or value.startswith("-")
        or ".." in value
        or not _SAFE_REF_RE.match(value)
    ):
        msg = f"Unsafe {label} for git: {value!r}"
        logger.warning(
            event,
            label=label,
            value=value,
            error=msg,
        )
        raise error_cls(msg)


class PlannerWorktreeStrategy:
    """Git-worktree-based workspace isolation strategy.

    Creates a separate git worktree and branch for each agent task,
    allowing concurrent work without interference.

    All mutating git operations on the main repository (setup, merge,
    teardown) are serialized via an internal lock.

    Args:
        config: Planner worktrees configuration.
        repo_root: Path to the main repository root.
    """

    __slots__ = (
        "_active_workspaces",
        "_config",
        "_lock",
        "_repo_root",
    )

    def __init__(
        self,
        *,
        config: PlannerWorktreesConfig,
        repo_root: Path,
    ) -> None:
        self._config = config
        self._repo_root = repo_root
        self._active_workspaces: dict[str, Workspace] = {}
        self._lock = asyncio.Lock()

    async def _run_git(
        self,
        *args: str,
        cmd_timeout: float = 60.0,
        log_event: str = WORKSPACE_SETUP_FAILED,
    ) -> tuple[int, str, str]:
        """Run a git command in the repository root.

        Args:
            *args: Git command arguments.
            cmd_timeout: Maximum seconds to wait for the command.
            log_event: Event constant for timeout error logging.
                Callers in merge/teardown contexts should pass the
                appropriate event.

        Returns:
            Tuple of (return_code, stdout, stderr).
        """
        proc = await asyncio.create_subprocess_exec(
            "git",
            *args,
            cwd=str(self._repo_root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(),
                timeout=cmd_timeout,
            )
        except TimeoutError:
            proc.kill()
            await proc.wait()
            msg = f"git {args[0] if args else ''} timed out after {cmd_timeout}s"
            logger.exception(
                log_event,
                error=msg,
                args=args,
            )
            return (-1, "", msg)
        except asyncio.CancelledError:
            proc.kill()
            await proc.wait()
            raise
        rc = proc.returncode if proc.returncode is not None else -1
        return (
            rc,
            stdout_bytes.decode("utf-8", errors="replace").strip(),
            stderr_bytes.decode("utf-8", errors="replace").strip(),
        )

    async def setup_workspace(
        self,
        *,
        request: WorkspaceRequest,
    ) -> Workspace:
        """Create an isolated workspace via git worktree.

        Args:
            request: Workspace creation request.

        Returns:
            The created workspace.

        Raises:
            WorkspaceLimitError: When max concurrent worktrees reached.
            WorkspaceSetupError: When git operations fail or input
                contains unsafe characters.
        """
        _validate_git_ref(request.task_id, "task_id")
        _validate_git_ref(request.base_branch, "base_branch")

        async with self._lock:
            if len(self._active_workspaces) >= self._config.max_concurrent_worktrees:
                logger.warning(
                    WORKSPACE_LIMIT_REACHED,
                    current=len(self._active_workspaces),
                    limit=self._config.max_concurrent_worktrees,
                )
                msg = (
                    f"Maximum concurrent worktrees "
                    f"({self._config.max_concurrent_worktrees}) "
                    f"reached"
                )
                raise WorkspaceLimitError(msg)

            workspace_id = str(uuid4())
            branch_name = f"workspace/{request.task_id}/{workspace_id}"
            worktree_dir = self._resolve_worktree_path(workspace_id)

            logger.info(
                WORKSPACE_SETUP_START,
                workspace_id=workspace_id,
                task_id=request.task_id,
                agent_id=request.agent_id,
            )

            rc, _, stderr = await self._run_git(
                "branch",
                branch_name,
                request.base_branch,
            )
            if rc != 0:
                logger.warning(
                    WORKSPACE_SETUP_FAILED,
                    workspace_id=workspace_id,
                    error=stderr,
                )
                msg = f"Failed to create branch '{branch_name}': {stderr}"
                raise WorkspaceSetupError(msg)

            rc, _, stderr = await self._run_git(
                "worktree",
                "add",
                str(worktree_dir),
                branch_name,
            )
            if rc != 0:
                # Clean up the branch we just created
                cleanup_rc, _, cleanup_stderr = await self._run_git(
                    "branch",
                    "-D",
                    branch_name,
                )
                if cleanup_rc != 0:
                    logger.warning(
                        WORKSPACE_SETUP_FAILED,
                        workspace_id=workspace_id,
                        error="Branch cleanup after worktree"
                        f" failure: {cleanup_stderr}",
                    )
                logger.warning(
                    WORKSPACE_SETUP_FAILED,
                    workspace_id=workspace_id,
                    error=stderr,
                )
                msg = f"Failed to create worktree at '{worktree_dir}': {stderr}"
                raise WorkspaceSetupError(msg)

            workspace = Workspace(
                workspace_id=workspace_id,
                task_id=request.task_id,
                agent_id=request.agent_id,
                branch_name=branch_name,
                worktree_path=str(worktree_dir),
                base_branch=request.base_branch,
                created_at=datetime.now(UTC),
            )
            self._active_workspaces[workspace_id] = workspace

            logger.info(
                WORKSPACE_SETUP_COMPLETE,
                workspace_id=workspace_id,
                branch_name=branch_name,
            )
            return workspace

    async def merge_workspace(
        self,
        *,
        workspace: Workspace,
    ) -> MergeResult:
        """Merge workspace branch into base branch.

        Merge operations are serialized via an internal lock to
        prevent concurrent git state corruption. Merge conflicts are
        returned as a ``MergeResult`` with ``success=False`` rather
        than raised as exceptions.

        Args:
            workspace: The workspace to merge.

        Returns:
            Merge result with conflict details if any.

        Raises:
            WorkspaceMergeError: When checkout of base branch fails
                or when ``merge --abort`` fails after a conflict.
        """
        async with self._lock:
            _validate_git_ref(
                workspace.branch_name,
                "branch_name",
                error_cls=WorkspaceMergeError,
                event=WORKSPACE_MERGE_FAILED,
            )
            _validate_git_ref(
                workspace.base_branch,
                "base_branch",
                error_cls=WorkspaceMergeError,
                event=WORKSPACE_MERGE_FAILED,
            )

            start = time.monotonic()
            logger.info(
                WORKSPACE_MERGE_START,
                workspace_id=workspace.workspace_id,
                branch_name=workspace.branch_name,
            )

            rc, _, stderr = await self._run_git(
                "checkout",
                workspace.base_branch,
                log_event=WORKSPACE_MERGE_FAILED,
            )
            if rc != 0:
                logger.warning(
                    WORKSPACE_MERGE_FAILED,
                    workspace_id=workspace.workspace_id,
                    error=stderr,
                )
                msg = f"Failed to checkout '{workspace.base_branch}': {stderr}"
                raise WorkspaceMergeError(msg)

            rc, _, stderr = await self._run_git(
                "merge",
                "--no-ff",
                workspace.branch_name,
                log_event=WORKSPACE_MERGE_FAILED,
            )
            elapsed = time.monotonic() - start

            if rc == 0:
                rc_sha, sha_out, sha_err = await self._run_git(
                    "rev-parse",
                    "HEAD",
                    log_event=WORKSPACE_MERGE_FAILED,
                )
                if rc_sha != 0:
                    logger.error(
                        WORKSPACE_MERGE_FAILED,
                        workspace_id=workspace.workspace_id,
                        error=f"Failed to get merge commit SHA: {sha_err}",
                    )
                    msg = (
                        f"Merge succeeded but could not retrieve "
                        f"commit SHA for workspace "
                        f"'{workspace.workspace_id}': {sha_err}"
                    )
                    raise WorkspaceMergeError(msg)
                sha_out = sha_out.strip()
                logger.info(
                    WORKSPACE_MERGE_COMPLETE,
                    workspace_id=workspace.workspace_id,
                    commit_sha=sha_out,
                )
                return MergeResult(
                    workspace_id=workspace.workspace_id,
                    branch_name=workspace.branch_name,
                    success=True,
                    merged_commit_sha=sha_out,
                    duration_seconds=elapsed,
                )

            # Conflict detected — collect conflicting files
            logger.warning(
                WORKSPACE_MERGE_CONFLICT,
                workspace_id=workspace.workspace_id,
                error=stderr,
            )
            conflicts = await self._collect_conflicts()

            # Abort the failed merge
            abort_rc, _, abort_stderr = await self._run_git(
                "merge",
                "--abort",
                log_event=WORKSPACE_MERGE_FAILED,
            )
            if abort_rc != 0:
                logger.error(
                    WORKSPACE_MERGE_ABORT_FAILED,
                    workspace_id=workspace.workspace_id,
                    error=abort_stderr,
                )
                msg = (
                    f"Failed to abort merge for workspace "
                    f"'{workspace.workspace_id}': {abort_stderr}. "
                    f"Repository may be in an inconsistent state."
                )
                raise WorkspaceMergeError(msg)

            return MergeResult(
                workspace_id=workspace.workspace_id,
                branch_name=workspace.branch_name,
                success=False,
                conflicts=conflicts,
                duration_seconds=time.monotonic() - start,
            )

    async def teardown_workspace(
        self,
        *,
        workspace: Workspace,
    ) -> None:
        """Remove worktree and branch, unregister workspace.

        Uses best-effort cleanup: attempts both worktree removal and
        branch deletion even if one fails. Always unregisters the
        workspace to prevent capacity leaks.

        Args:
            workspace: The workspace to tear down.

        Raises:
            WorkspaceCleanupError: When any git cleanup operation fails.
        """
        async with self._lock:
            _validate_git_ref(
                workspace.branch_name,
                "branch_name",
                error_cls=WorkspaceCleanupError,
                event=WORKSPACE_TEARDOWN_FAILED,
            )

            logger.info(
                WORKSPACE_TEARDOWN_START,
                workspace_id=workspace.workspace_id,
            )

            errors: list[str] = []

            rc, _, stderr = await self._run_git(
                "worktree",
                "remove",
                workspace.worktree_path,
                "--force",
                log_event=WORKSPACE_TEARDOWN_FAILED,
            )
            if rc != 0:
                errors.append(
                    f"worktree remove: {stderr}",
                )
                logger.warning(
                    WORKSPACE_TEARDOWN_FAILED,
                    workspace_id=workspace.workspace_id,
                    error=f"worktree remove: {stderr}",
                )

            rc, _, stderr = await self._run_git(
                "branch",
                "-D",
                workspace.branch_name,
                log_event=WORKSPACE_TEARDOWN_FAILED,
            )
            if rc != 0:
                errors.append(
                    f"branch delete: {stderr}",
                )
                logger.warning(
                    WORKSPACE_TEARDOWN_FAILED,
                    workspace_id=workspace.workspace_id,
                    error=f"branch delete: {stderr}",
                )

            # Always unregister to prevent capacity leaks
            self._active_workspaces.pop(workspace.workspace_id, None)

            if errors:
                msg = (
                    f"Partial cleanup failure for workspace "
                    f"'{workspace.workspace_id}': {'; '.join(errors)}"
                )
                raise WorkspaceCleanupError(msg)

            logger.info(
                WORKSPACE_TEARDOWN_COMPLETE,
                workspace_id=workspace.workspace_id,
            )

    async def list_active_workspaces(self) -> tuple[Workspace, ...]:
        """Return all currently active workspaces.

        Returns:
            Tuple of active workspaces.
        """
        return tuple(self._active_workspaces.values())

    def get_strategy_type(self) -> str:
        """Return the strategy type identifier.

        Returns:
            Strategy type name.
        """
        return "planner_worktrees"

    def _resolve_worktree_path(self, workspace_id: str) -> Path:
        """Resolve the filesystem path for a new worktree.

        Args:
            workspace_id: Unique workspace identifier.

        Returns:
            Path where the worktree will be created.
        """
        if self._config.worktree_base_dir:
            base = Path(self._config.worktree_base_dir)
        else:
            base = self._repo_root.parent / ".worktrees"
        return base / workspace_id

    async def _collect_conflicts(self) -> tuple[MergeConflict, ...]:
        """Collect conflicting file paths after a failed merge.

        Returns:
            Tuple of MergeConflict instances for each conflict.

        Raises:
            WorkspaceMergeError: When conflict collection fails.
        """
        rc, stdout, stderr = await self._run_git(
            "diff",
            "--name-only",
            "--diff-filter=U",
        )
        if rc != 0:
            logger.error(
                WORKSPACE_MERGE_FAILED,
                error=f"Failed to collect conflict info: {stderr}",
            )
            msg = f"Failed to collect merge conflict details: {stderr}"
            raise WorkspaceMergeError(msg)

        if not stdout:
            return ()

        # Git diff --diff-filter=U only detects textual conflicts;
        # semantic conflict detection is not yet implemented
        conflicts: list[MergeConflict] = []
        for line in stdout.splitlines():
            file_path = line.strip()
            if file_path:
                conflicts.append(
                    MergeConflict(
                        file_path=file_path,
                        conflict_type=ConflictType.TEXTUAL,
                    ),
                )
        return tuple(conflicts)
