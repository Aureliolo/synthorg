# module-kind: complex_service
"""Planner-worktrees workspace isolation strategy.

Uses git worktrees to provide each agent with an isolated working
directory backed by its own branch.
"""

import asyncio
import math
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Final
from uuid import uuid4

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.enums import ConflictType
from synthorg.engine.errors import (
    WorkspaceCleanupError,
    WorkspaceError,
    WorkspaceLimitError,
    WorkspaceMergeError,
    WorkspaceSetupError,
)
from synthorg.engine.workspace._git_subprocess import run_git_subprocess
from synthorg.engine.workspace.config import PlannerWorktreesConfig
from synthorg.engine.workspace.models import (
    MergeConflict,
    MergeResult,
    Workspace,
    WorkspaceRequest,
)
from synthorg.engine.workspace.semantic_git_ops import run_semantic_analysis
from synthorg.observability import get_logger
from synthorg.observability.events.workspace import (
    WORKSPACE_CONFIG_INVALID,
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

if TYPE_CHECKING:
    from synthorg.engine.workspace.semantic_analyzer import SemanticAnalyzer

logger = get_logger(__name__)

_SAFE_REF_RE = re.compile(r"^[A-Za-z0-9._/-]+$")
_PROJECTS_SUBDIR: Final[str] = "projects"


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
        "_clock",
        "_cmd_timeout",
        "_config",
        "_lock",
        "_repo_root",
        "_semantic_analyzer",
    )

    def __init__(
        self,
        *,
        config: PlannerWorktreesConfig,
        repo_root: Path,
        cmd_timeout: float,
        semantic_analyzer: SemanticAnalyzer | None = None,
        clock: Clock | None = None,
    ) -> None:
        """Initialize the strategy.

        Args:
            config: Strategy configuration.
            repo_root: Root of the parent git repository.
            cmd_timeout: Maximum seconds any git subprocess invocation
                may run before it is cancelled.  Resolve via
                ``ConfigResolver.get_float("tools",
                "git_command_timeout_seconds")`` at the call site.
            semantic_analyzer: Optional semantic conflict analyzer.
            clock: Injectable time source; defaults to ``SystemClock``.

        Raises:
            ValueError: When ``cmd_timeout`` is not a finite positive
                float; ``asyncio.wait_for`` would otherwise behave
                indeterminately or downstream ``timedelta`` math would
                overflow.
        """
        if not math.isfinite(cmd_timeout) or cmd_timeout <= 0:
            # Reject NaN / +Inf / -Inf alongside zero and negatives:
            # ``asyncio.wait_for(..., timeout=nan)`` would silently
            # produce undefined wait behaviour, and a non-finite
            # ``timedelta`` constructed downstream raises an opaque
            # ``OverflowError`` mid-request.  Fail at the boundary.
            msg = f"cmd_timeout must be a finite positive float, got {cmd_timeout!r}"
            logger.warning(
                WORKSPACE_CONFIG_INVALID,
                error=msg,
                param="cmd_timeout",
                value=cmd_timeout,
            )
            raise ValueError(msg)
        self._clock: Clock = clock if clock is not None else SystemClock()
        self._config = config
        self._repo_root = repo_root
        self._cmd_timeout = cmd_timeout
        self._active_workspaces: dict[str, Workspace] = {}
        self._lock = asyncio.Lock()
        self._semantic_analyzer = semantic_analyzer

    def _effective_root(
        self,
        project_id: str | None,
        *,
        event: str = WORKSPACE_SETUP_FAILED,
        error_cls: type[WorkspaceError] = WorkspaceSetupError,
    ) -> Path:
        """Resolve the repo root for a project.

        ``None`` returns the strategy's singleton root (non-project
        path); a set ``project_id`` returns ``<root>/projects/<id>`` so a
        worktree branches from, and merges back into, the project's own
        persistent repo. The separator/empty guards block traversal out
        of the projects subtree, and the resolved path is re-anchored
        under it so a symlinked entry cannot escape. ``event`` /
        ``error_cls`` let merge/teardown callers classify a bad id as a
        merge/cleanup failure rather than a setup failure.

        Returns:
            The strategy's singleton root when ``project_id`` is
            ``None``; otherwise ``<root>/projects/<project_id>``.
        """
        if project_id is None:
            return self._repo_root
        if (
            not project_id.strip()
            or project_id == "."
            or "/" in project_id
            or "\\" in project_id
            or ".." in project_id
        ):
            msg = f"refusing path-separator-bearing project_id {project_id!r}"
            logger.warning(event, error=msg, project_id=project_id)
            raise error_cls(msg)
        projects_root = (self._repo_root / _PROJECTS_SUBDIR).resolve()
        candidate = self._repo_root / _PROJECTS_SUBDIR / project_id
        if not candidate.resolve().is_relative_to(projects_root):
            msg = f"project_id {project_id!r} escapes the projects subtree"
            logger.warning(event, error=msg, project_id=project_id)
            raise error_cls(msg)
        return candidate

    async def _run_git(
        self,
        *args: str,
        cwd: Path | None = None,
        log_event: str = WORKSPACE_SETUP_FAILED,
    ) -> tuple[int, str, str]:
        """Run a git command in *cwd* (defaults to the singleton root).

        Thin wrapper around :func:`run_git_subprocess` so this class
        stays focused on workflow orchestration rather than subprocess
        plumbing.  The wall-clock cap comes from ``self._cmd_timeout``,
        which is resolved from the
        ``tools.git_command_timeout_seconds`` setting at construction.

        Args:
            *args: Git command arguments.
            cwd: Repository the command runs in; defaults to the
                strategy's singleton ``repo_root`` when ``None``.
            log_event: Event constant for timeout error logging.

        Returns:
            Tuple of (return_code, stdout, stderr).
        """
        return await run_git_subprocess(
            cwd if cwd is not None else self._repo_root,
            *args,
            cmd_timeout=self._cmd_timeout,
            log_event=log_event,
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

        effective_root = self._effective_root(request.project_id)

        async with self._lock:
            self._check_workspace_limit()

            workspace_id = str(uuid4())
            branch_name = f"workspace/{request.task_id}/{workspace_id}"
            worktree_dir = self._resolve_worktree_path(workspace_id, effective_root)

            logger.info(
                WORKSPACE_SETUP_START,
                workspace_id=workspace_id,
                task_id=request.task_id,
                agent_id=request.agent_id,
            )

            await self._create_worktree_and_branch(
                workspace_id,
                branch_name,
                request.base_branch,
                worktree_dir,
                effective_root,
            )

            workspace = Workspace(
                workspace_id=workspace_id,
                task_id=request.task_id,
                agent_id=request.agent_id,
                branch_name=branch_name,
                worktree_path=str(worktree_dir),
                base_branch=request.base_branch,
                created_at=datetime.now(UTC),
                project_id=request.project_id,
            )
            self._active_workspaces[workspace_id] = workspace

            logger.info(
                WORKSPACE_SETUP_COMPLETE,
                workspace_id=workspace_id,
                branch_name=branch_name,
            )
            return workspace

    def _check_workspace_limit(self) -> None:
        """Raise if max concurrent worktrees reached.

        Raises:
            WorkspaceLimitError: When the active workspaces count is at
                or above ``config.max_concurrent_worktrees``.
        """
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

    async def _create_worktree_and_branch(
        self,
        workspace_id: str,
        branch_name: str,
        base_branch: str,
        worktree_dir: Path,
        effective_root: Path,
    ) -> None:
        """Create a git branch and worktree, cleaning up on failure.

        Raises:
            WorkspaceSetupError: When ``git branch`` or ``git worktree
                add`` fails; the branch is best-effort deleted before
                re-raising so a failed setup does not leak refs.
        """
        rc, _, stderr = await self._run_git(
            "branch",
            branch_name,
            base_branch,
            cwd=effective_root,
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
            cwd=effective_root,
        )
        if rc != 0:
            cleanup_rc, _, cleanup_stderr = await self._run_git(
                "branch",
                "-D",
                branch_name,
                cwd=effective_root,
            )
            if cleanup_rc != 0:
                logger.warning(
                    WORKSPACE_SETUP_FAILED,
                    workspace_id=workspace_id,
                    error=f"Branch cleanup after worktree failure: {cleanup_stderr}",
                )
            logger.warning(
                WORKSPACE_SETUP_FAILED,
                workspace_id=workspace_id,
                error=stderr,
            )
            msg = f"Failed to create worktree at '{worktree_dir}': {stderr}"
            raise WorkspaceSetupError(msg)

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

        Semantic analysis runs *after* the lock is released because
        it only performs read-only git operations against explicit
        SHA references.

        Args:
            workspace: The workspace to merge.

        Returns:
            Merge result with conflict details if any.

        Raises:
            WorkspaceMergeError: When checkout of base branch fails
                or when ``merge --abort`` fails after a conflict.
        """
        effective_root = self._effective_root(
            workspace.project_id,
            event=WORKSPACE_MERGE_FAILED,
            error_cls=WorkspaceMergeError,
        )
        # Merge under lock.
        result, pre_merge_sha = await self._merge_under_lock(
            workspace=workspace,
            effective_root=effective_root,
        )

        # Semantic analysis outside lock (read-only SHA ops).
        if not result.success or not result.merged_commit_sha:
            return result

        semantic_conflicts = await self._run_semantic_analysis(
            workspace=workspace,
            pre_merge_sha=pre_merge_sha,
            merge_sha=result.merged_commit_sha,
        )
        if semantic_conflicts:
            return result.model_copy(
                update={"semantic_conflicts": semantic_conflicts},
            )
        return result

    async def _merge_under_lock(
        self,
        *,
        workspace: Workspace,
        effective_root: Path,
    ) -> tuple[MergeResult, str]:
        """Execute the merge operation under the serialization lock.

        Returns:
            Tuple of (MergeResult, pre_merge_sha). The pre_merge_sha
            is the main-branch tip before the merge, needed by
            semantic analysis to find the branch point.
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

            start = self._clock.monotonic()
            pre_merge_sha = await self._checkout_and_capture_sha(
                workspace,
                effective_root,
            )

            rc, _, stderr = await self._run_git(
                "merge",
                "--no-ff",
                workspace.branch_name,
                cwd=effective_root,
                log_event=WORKSPACE_MERGE_FAILED,
            )
            elapsed = self._clock.monotonic() - start

            if rc == 0:
                return await self._finalize_successful_merge(
                    workspace=workspace,
                    elapsed=elapsed,
                    pre_merge_sha=pre_merge_sha,
                    effective_root=effective_root,
                )

            return await self._handle_merge_conflict(
                workspace=workspace,
                stderr=stderr,
                start=start,
                effective_root=effective_root,
            ), pre_merge_sha

    async def _checkout_and_capture_sha(
        self,
        workspace: Workspace,
        effective_root: Path,
    ) -> str:
        """Checkout the base branch and capture HEAD SHA.

        Returns:
            The base-branch HEAD SHA after checkout, or an empty
            string when ``git rev-parse`` failed (the merge can
            still proceed; semantic analysis just degrades).

        Raises:
            WorkspaceMergeError: When ``git checkout`` of the base
                branch fails.
        """
        logger.info(
            WORKSPACE_MERGE_START,
            workspace_id=workspace.workspace_id,
            branch_name=workspace.branch_name,
        )

        rc, _, stderr = await self._run_git(
            "checkout",
            workspace.base_branch,
            cwd=effective_root,
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

        pre_rc, pre_sha_out, pre_stderr = await self._run_git(
            "rev-parse",
            "HEAD",
            cwd=effective_root,
            log_event=WORKSPACE_MERGE_FAILED,
        )
        if pre_rc != 0:
            logger.warning(
                WORKSPACE_MERGE_FAILED,
                workspace_id=workspace.workspace_id,
                operation="rev-parse",
                error=f"Failed to capture pre-merge SHA: {pre_stderr}",
            )
            return ""
        return pre_sha_out.strip()

    async def _finalize_successful_merge(
        self,
        *,
        workspace: Workspace,
        elapsed: float,
        pre_merge_sha: str,
        effective_root: Path,
    ) -> tuple[MergeResult, str]:
        """Finalize a successful merge by capturing the commit SHA.

        Returns:
            Tuple of (MergeResult, pre_merge_sha).

        Raises:
            WorkspaceMergeError: When ``git rev-parse HEAD`` fails
                after a successful merge (the merge landed but the
                resulting commit SHA could not be recorded).
        """
        rc_sha, sha_out, sha_err = await self._run_git(
            "rev-parse",
            "HEAD",
            cwd=effective_root,
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
        result = MergeResult(
            workspace_id=workspace.workspace_id,
            branch_name=workspace.branch_name,
            success=True,
            merged_commit_sha=sha_out,
            duration_seconds=elapsed,
        )
        return result, pre_merge_sha

    async def _handle_merge_conflict(
        self,
        *,
        workspace: Workspace,
        stderr: str,
        start: float,
        effective_root: Path,
    ) -> MergeResult:
        """Handle a merge conflict: collect files and abort.

        Returns:
            MergeResult with ``success=False``.

        Raises:
            WorkspaceMergeError: When ``merge --abort`` fails.
        """
        logger.warning(
            WORKSPACE_MERGE_CONFLICT,
            workspace_id=workspace.workspace_id,
            error=stderr,
        )
        conflicts = await self._collect_conflicts(effective_root)

        # Abort the failed merge
        abort_rc, _, abort_stderr = await self._run_git(
            "merge",
            "--abort",
            cwd=effective_root,
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
                f"Repository may be in an inconsistent "
                f"state."
            )
            raise WorkspaceMergeError(msg)

        return MergeResult(
            workspace_id=workspace.workspace_id,
            branch_name=workspace.branch_name,
            success=False,
            conflicts=conflicts,
            duration_seconds=self._clock.monotonic() - start,
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

            errors = await self._remove_worktree_and_branch(
                workspace,
                self._effective_root(
                    workspace.project_id,
                    event=WORKSPACE_TEARDOWN_FAILED,
                    error_cls=WorkspaceCleanupError,
                ),
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

    async def _remove_worktree_and_branch(
        self,
        workspace: Workspace,
        effective_root: Path,
    ) -> list[str]:
        """Remove worktree and branch, returning error messages.

        Returns:
            List of error strings (one per failed git operation);
            empty when both removals succeed.
        """
        errors: list[str] = []

        rc, _, stderr = await self._run_git(
            "worktree",
            "remove",
            workspace.worktree_path,
            "--force",
            cwd=effective_root,
            log_event=WORKSPACE_TEARDOWN_FAILED,
        )
        if rc != 0:
            errors.append(f"worktree remove: {stderr}")
            logger.warning(
                WORKSPACE_TEARDOWN_FAILED,
                workspace_id=workspace.workspace_id,
                error=f"worktree remove: {stderr}",
            )

        rc, _, stderr = await self._run_git(
            "branch",
            "-D",
            workspace.branch_name,
            cwd=effective_root,
            log_event=WORKSPACE_TEARDOWN_FAILED,
        )
        if rc != 0:
            errors.append(f"branch delete: {stderr}")
            logger.warning(
                WORKSPACE_TEARDOWN_FAILED,
                workspace_id=workspace.workspace_id,
                error=f"branch delete: {stderr}",
            )

        return errors

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

    def _resolve_worktree_path(
        self,
        workspace_id: str,
        effective_root: Path | None = None,
    ) -> Path:
        """Resolve the filesystem path for a new worktree.

        Args:
            workspace_id: Unique workspace identifier.
            effective_root: Per-project repo root the worktree links to;
                its parent hosts the ``.worktrees`` directory. Defaults
                to the strategy's singleton root.

        Returns:
            Path where the worktree will be created.
        """
        root = effective_root if effective_root is not None else self._repo_root
        if self._config.worktree_base_dir:
            base = Path(self._config.worktree_base_dir)
        elif root != self._repo_root:
            # Project-scoped: keep the worktree *inside* the project's own
            # repo tree. Its sandbox mount only exposes
            # ``<repo_root>/projects/<project_id>``, so a sibling
            # ``projects/.worktrees`` would be invisible (and shared across
            # projects) -- breaking project-scoped execution.
            base = root / ".worktrees"
        else:
            base = root.parent / ".worktrees"
        return base / workspace_id

    async def _collect_conflicts(
        self,
        effective_root: Path,
    ) -> tuple[MergeConflict, ...]:
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
            cwd=effective_root,
            log_event=WORKSPACE_MERGE_FAILED,
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

        # Git diff --diff-filter=U detects textual conflicts only;
        # semantic conflict detection runs in _run_semantic_analysis
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

    async def _run_semantic_analysis(
        self,
        *,
        workspace: Workspace,
        pre_merge_sha: str,
        merge_sha: str,
    ) -> tuple[MergeConflict, ...]:
        """Delegate to ``semantic_git_ops.run_semantic_analysis``.

        Returns:
            Tuple of semantic :class:`MergeConflict` instances; ``()``
            when no analyzer is configured or analysis is disabled.
        """
        return await run_semantic_analysis(
            run_git=self._run_git,
            config=self._config.semantic_analysis,
            analyzer=self._semantic_analyzer,
            workspace=workspace,
            pre_merge_sha=pre_merge_sha,
            merge_sha=merge_sha,
        )
