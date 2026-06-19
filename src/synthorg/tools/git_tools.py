"""Built-in git tools for version control operations.

Provides workspace-scoped git tools that agents use to interact with
git repositories.  All tools enforce workspace boundary security -- the
LLM never controls absolute paths.  See ``_git_base._BaseGitTool`` for
the subprocess execution model, environment hardening, and path
validation shared by all tools.
"""

from pathlib import Path
from typing import ClassVar, Final, override

from pydantic import BaseModel

from synthorg.core.boundary import parse_typed
from synthorg.observability import get_logger
from synthorg.observability.events.git import GIT_COMMAND_START
from synthorg.security.autonomy.enums import ActionType
from synthorg.tools._git_args import (
    GitBranchArgs,
    GitCommitArgs,
    GitDiffArgs,
    GitLogArgs,
    GitStatusArgs,
)
from synthorg.tools._git_base import _BaseGitTool
from synthorg.tools._git_clone import GitCloneTool
from synthorg.tools.base import ToolExecutionResult
from synthorg.tools.sandbox.protocol import SandboxBackend

logger = get_logger(__name__)

__all__ = [
    "GitBranchTool",
    "GitCloneTool",
    "GitCommitTool",
    "GitDiffTool",
    "GitLogTool",
    "GitStatusTool",
]


# ── GitStatusTool ─────────────────────────────────────────────────


class GitStatusTool(_BaseGitTool):
    """Show the working tree status of the git repository.

    Returns the output of ``git status`` with optional short or
    porcelain formatting.
    """

    args_model: ClassVar[type[BaseModel] | None] = GitStatusArgs

    def __init__(
        self,
        *,
        workspace: Path,
        sandbox: SandboxBackend | None = None,
    ) -> None:
        """Initialize the git_status tool.

        Args:
            workspace: Absolute path to the workspace root.
            sandbox: Optional sandbox backend for subprocess isolation.
        """
        super().__init__(
            name="git_status",
            description=(
                "Show the working tree status. Returns modified, staged, "
                "and untracked files in the workspace repository."
            ),
            parameters_schema=GitStatusArgs.model_json_schema(),
            workspace=workspace,
            sandbox=sandbox,
            action_type=ActionType.VCS_READ,
        )

    @override
    async def execute(
        self,
        *,
        arguments: dict[str, object],
    ) -> ToolExecutionResult:
        """Run ``git status``.

        Args:
            arguments: Optional ``short`` and ``porcelain`` flags.

        Returns:
            A ``ToolExecutionResult`` with the status output.
        """
        args = parse_typed("tool.git_status", arguments, GitStatusArgs)
        git_args = ["status"]
        if args.porcelain:
            git_args.append("--porcelain")
        elif args.short:
            git_args.append("--short")
        return await self._run_git(git_args)


# ── GitLogTool ────────────────────────────────────────────────────


class GitLogTool(_BaseGitTool):
    """Show commit log history.

    Returns recent commits with optional filtering by count, author,
    date range, ref, and paths.
    """

    args_model: ClassVar[type[BaseModel] | None] = GitLogArgs

    _MAX_COUNT_LIMIT: Final[int] = 100

    def __init__(
        self,
        *,
        workspace: Path,
        sandbox: SandboxBackend | None = None,
        max_count_limit: int = _MAX_COUNT_LIMIT,
    ) -> None:
        """Initialize the git_log tool.

        Args:
            workspace: Absolute path to the workspace root.
            sandbox: Optional sandbox backend for subprocess isolation.
            max_count_limit: Upper bound on returned commits; a per-call
                ``max_count`` above this is clamped down. Resolved from
                the ``tools.git_log_max_count`` setting at the wiring
                boundary, defaulting to ``_MAX_COUNT_LIMIT``.
        """
        self._max_count_limit = max_count_limit
        super().__init__(
            name="git_log",
            description=(
                "Show commit log. Returns recent commits with optional "
                "filtering by count, author, date range, ref, and paths."
            ),
            parameters_schema=GitLogArgs.model_json_schema(),
            workspace=workspace,
            sandbox=sandbox,
            action_type=ActionType.VCS_READ,
        )

    def _build_filter_args(
        self,
        args: GitLogArgs,
    ) -> list[str] | ToolExecutionResult:
        """Validate and build ``--author``, ``--since``, ``--until`` args.

        Returns the argument list on success, or an error result if any
        filter value fails the flag-injection check.

        Returns:
            Result of type ``list[str] | ToolExecutionResult``.
        """
        filter_args: list[str] = []
        for value, flag, param in (
            (args.author, "--author", "author"),
            (args.since, "--since", "since"),
            (args.until, "--until", "until"),
        ):
            if value:
                if err := self._check_git_arg(value, param=param):
                    return err
                filter_args.append(f"{flag}={value}")
        return filter_args

    @override
    async def execute(
        self,
        *,
        arguments: dict[str, object],
    ) -> ToolExecutionResult:
        """Run ``git log``.

        Args:
            arguments: Log options (max_count, oneline, ref, author,
                since, until, paths).

        Returns:
            A ``ToolExecutionResult`` with the log output.
        """
        args = parse_typed("tool.git_log", arguments, GitLogArgs)
        max_count = min(args.max_count, self._max_count_limit)
        git_args = ["log", f"--max-count={max_count}"]

        if args.oneline:
            git_args.append("--oneline")

        filter_args = self._build_filter_args(args)
        if isinstance(filter_args, ToolExecutionResult):
            return filter_args
        git_args.extend(filter_args)

        if args.ref:
            if err := self._check_git_arg(args.ref, param="ref"):
                return err
            git_args.append(args.ref)

        paths = list(args.paths)
        if paths:
            if err := self._check_paths(paths):
                return err
            git_args.append("--")
            git_args.extend(paths)

        result = await self._run_git(git_args)
        if not result.is_error and not result.content:
            return ToolExecutionResult(content="No commits found")
        return result


# ── GitDiffTool ───────────────────────────────────────────────────


class GitDiffTool(_BaseGitTool):
    """Show changes between commits, the index, and the working tree.

    Returns the output of ``git diff`` with optional ref comparison,
    staged changes view, stat summary, and path filtering.
    """

    args_model: ClassVar[type[BaseModel] | None] = GitDiffArgs

    def __init__(
        self,
        *,
        workspace: Path,
        sandbox: SandboxBackend | None = None,
    ) -> None:
        """Initialize the git_diff tool.

        Args:
            workspace: Absolute path to the workspace root.
            sandbox: Optional sandbox backend for subprocess isolation.
        """
        super().__init__(
            name="git_diff",
            description=(
                "Show changes between commits, index, and working tree. "
                "Supports staged changes, ref comparison, and path "
                "filtering."
            ),
            action_type=ActionType.VCS_READ,
            parameters_schema=GitDiffArgs.model_json_schema(),
            workspace=workspace,
            sandbox=sandbox,
        )

    @override
    async def execute(
        self,
        *,
        arguments: dict[str, object],
    ) -> ToolExecutionResult:
        """Run ``git diff``.

        Args:
            arguments: Diff options (staged, ref1, ref2, stat, paths).

        Returns:
            A ``ToolExecutionResult`` with the diff output. Empty diff
            returns "No changes" (not an error).
        """
        # ``GitDiffArgs`` enforces the ``ref2`` requires ``ref1``
        # cross-field rule at the typed boundary.
        args = parse_typed("tool.git_diff", arguments, GitDiffArgs)
        git_args = ["diff"]

        if args.staged:
            git_args.append("--cached")

        if args.stat:
            git_args.append("--stat")

        if args.ref1:
            if err := self._check_git_arg(args.ref1, param="ref1"):
                return err
            git_args.append(args.ref1)
        if args.ref2:
            if err := self._check_git_arg(args.ref2, param="ref2"):
                return err
            git_args.append(args.ref2)

        paths = list(args.paths)
        if paths:
            if err := self._check_paths(paths):
                return err
            git_args.append("--")
            git_args.extend(paths)

        result = await self._run_git(git_args)
        if not result.is_error and not result.content:
            return ToolExecutionResult(content="No changes")
        return result


# ── GitBranchTool ─────────────────────────────────────────────────


class GitBranchTool(_BaseGitTool):
    """Manage branches -- list, create, switch, or delete.

    Supports listing all branches, creating new branches (optionally
    from a start point), switching between branches, and deleting
    branches.
    """

    args_model: ClassVar[type[BaseModel] | None] = GitBranchArgs

    def __init__(
        self,
        *,
        workspace: Path,
        sandbox: SandboxBackend | None = None,
    ) -> None:
        """Initialize the git_branch tool.

        Args:
            workspace: Absolute path to the workspace root.
            sandbox: Optional sandbox backend for subprocess isolation.
        """
        super().__init__(
            name="git_branch",
            description=(
                "Manage branches: list, create, switch, or delete. "
                "Provide an action and branch name as needed."
            ),
            action_type=ActionType.VCS_BRANCH,
            parameters_schema=GitBranchArgs.model_json_schema(),
            workspace=workspace,
            sandbox=sandbox,
        )

    async def _list_branches(self) -> ToolExecutionResult:
        """List all branches.

        Returns:
            Result of type ``ToolExecutionResult``.
        """
        result = await self._run_git(["branch", "-a"])
        if not result.is_error and not result.content:
            return ToolExecutionResult(content="No branches found")
        return result

    async def _create_branch(
        self,
        name: str,
        start_point: str | None,
    ) -> ToolExecutionResult:
        """Create a branch, optionally from a start point.

        Returns:
            Result of type ``ToolExecutionResult``.
        """
        git_args = ["branch", name]
        if start_point:
            if err := self._check_git_arg(start_point, param="start_point"):
                return err
            git_args.append(start_point)
        return await self._run_git(git_args)

    @override
    async def execute(
        self,
        *,
        arguments: dict[str, object],
    ) -> ToolExecutionResult:
        """Run a branch operation.

        Args:
            arguments: Branch action, name, start_point, force.

        Returns:
            A ``ToolExecutionResult`` with the operation output.
        """
        # ``GitBranchArgs`` enforces the create/switch/delete actions
        # require a branch ``name`` cross-field rule at the typed
        # boundary, so ``list`` is the only action reachable here with a
        # ``None`` name.
        args = parse_typed("tool.git_branch", arguments, GitBranchArgs)

        if args.action == "list":
            return await self._list_branches()

        # Narrowing: guaranteed non-None by the args-model validator.
        branch_name = args.name
        if branch_name is None:
            return ToolExecutionResult(
                content=(f"Branch name is required for '{args.action}' action"),
                is_error=True,
            )

        if err := self._check_git_arg(branch_name, param="name"):
            return err

        if args.action == "create":
            return await self._create_branch(branch_name, args.start_point)

        if args.action == "switch":
            return await self._run_git(["switch", branch_name])

        flag = "-D" if args.force else "-d"
        return await self._run_git(["branch", flag, branch_name])


# ── GitCommitTool ─────────────────────────────────────────────────


class GitCommitTool(_BaseGitTool):
    """Stage and commit changes.

    Stages specified paths (or all changes with ``all=True``), then
    creates a commit with the provided message.
    """

    args_model: ClassVar[type[BaseModel] | None] = GitCommitArgs

    def __init__(
        self,
        *,
        workspace: Path,
        sandbox: SandboxBackend | None = None,
    ) -> None:
        """Initialize the git_commit tool.

        Args:
            workspace: Absolute path to the workspace root.
            sandbox: Optional sandbox backend for subprocess isolation.
        """
        super().__init__(
            name="git_commit",
            action_type=ActionType.VCS_COMMIT,
            description=(
                "Stage and commit changes. Provide a commit message and "
                "optionally specify paths to stage or use 'all' to stage "
                "everything."
            ),
            parameters_schema=GitCommitArgs.model_json_schema(),
            workspace=workspace,
            sandbox=sandbox,
        )

    @override
    async def execute(
        self,
        *,
        arguments: dict[str, object],
    ) -> ToolExecutionResult:
        """Stage and commit changes.

        Args:
            arguments: Commit message, optional paths, optional all flag.

        Returns:
            A ``ToolExecutionResult`` with the commit output.
        """
        args = parse_typed("tool.git_commit", arguments, GitCommitArgs)
        message = args.message
        paths = list(args.paths)
        stage_all = args.all

        if paths:
            if err := self._check_paths(paths):
                return err
            add_result = await self._run_git(["add", "--", *paths])
            if add_result.is_error:
                return add_result
        elif stage_all:
            add_result = await self._run_git(["add", "-A"])
            if add_result.is_error:
                return add_result
        else:
            logger.debug(
                GIT_COMMAND_START,
                command=["git", "commit"],
                note="no staging requested; committing already staged",
            )

        return await self._run_git(["commit", "-m", message])
