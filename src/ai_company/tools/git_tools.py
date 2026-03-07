"""Built-in git tools for version control operations.

Provides six workspace-scoped git tools that agents use to interact with
git repositories.  All tools enforce workspace boundary security — the LLM
never controls absolute paths.  Subprocess execution uses
``asyncio.create_subprocess_exec`` (never ``shell=True``) with
``GIT_TERMINAL_PROMPT=0`` to prevent interactive credential prompts.
"""

import asyncio
import os
from abc import ABC
from pathlib import Path  # noqa: TC003 — used at runtime
from typing import Any

from ai_company.core.enums import ToolCategory
from ai_company.observability import get_logger
from ai_company.observability.events.git import (
    GIT_COMMAND_FAILED,
    GIT_COMMAND_START,
    GIT_COMMAND_SUCCESS,
    GIT_COMMAND_TIMEOUT,
    GIT_WORKSPACE_VIOLATION,
)
from ai_company.tools.base import BaseTool, ToolExecutionResult

logger = get_logger(__name__)

_DEFAULT_TIMEOUT: float = 30.0
_CLONE_TIMEOUT: float = 120.0
_ALLOWED_CLONE_SCHEMES = ("https://", "http://", "ssh://", "git://")


def _is_allowed_clone_url(url: str) -> bool:
    """Check if a clone URL uses an allowed remote scheme.

    Allows standard remote schemes and SCP-like syntax.  Rejects
    ``file://``, ``ext::``, and bare local paths.

    Args:
        url: Repository URL string to validate.

    Returns:
        ``True`` if the URL scheme is allowed.
    """
    if any(url.startswith(scheme) for scheme in _ALLOWED_CLONE_SCHEMES):
        return True
    # SCP-like: user@host:path (e.g. git@github.com:user/repo.git)
    return "@" in url and ":" in url and "::" not in url and "://" not in url


# ── Base class ────────────────────────────────────────────────────


class _BaseGitTool(BaseTool, ABC):
    """Shared base for all git tools.

    Holds the ``workspace`` path and provides helper methods for running
    git commands and validating relative paths against the workspace
    boundary.

    Attributes:
        workspace: Absolute path to the agent's workspace directory.
    """

    def __init__(
        self,
        *,
        name: str,
        description: str,
        parameters_schema: dict[str, Any],
        workspace: Path,
    ) -> None:
        """Initialize a git tool bound to a workspace.

        Args:
            name: Tool name.
            description: Human-readable description.
            parameters_schema: JSON Schema for tool parameters.
            workspace: Absolute path to the workspace root.
        """
        super().__init__(
            name=name,
            description=description,
            parameters_schema=parameters_schema,
            category=ToolCategory.VERSION_CONTROL,
        )
        self._workspace = workspace.resolve()

    @property
    def workspace(self) -> Path:
        """Workspace root directory."""
        return self._workspace

    def _validate_path(self, relative: str) -> Path:
        """Resolve a relative path and verify it stays within workspace.

        Args:
            relative: A relative path string from the LLM.

        Returns:
            The resolved absolute ``Path``.

        Raises:
            ValueError: If the path escapes the workspace boundary.
        """
        resolved = (self._workspace / relative).resolve()
        try:
            resolved.relative_to(self._workspace)
        except ValueError:
            logger.warning(
                GIT_WORKSPACE_VIOLATION,
                path=relative,
                workspace=str(self._workspace),
            )
            msg = f"Path '{relative}' is outside workspace"
            raise ValueError(msg) from None
        return resolved

    def _check_paths(self, paths: list[str]) -> ToolExecutionResult | None:
        """Validate a list of paths, returning an error result or None.

        Args:
            paths: Relative path strings to validate.

        Returns:
            A ``ToolExecutionResult`` with ``is_error=True`` if any path
            escapes the workspace, or ``None`` if all paths are valid.
        """
        for p in paths:
            try:
                self._validate_path(p)
            except ValueError as exc:
                return ToolExecutionResult(
                    content=str(exc),
                    is_error=True,
                )
        return None

    def _check_ref(
        self,
        value: str,
        *,
        param: str,
    ) -> ToolExecutionResult | None:
        """Reject ref-like values starting with ``-`` to prevent flag injection.

        Args:
            value: The ref or branch name string to validate.
            param: Parameter name for the error message.

        Returns:
            A ``ToolExecutionResult`` with ``is_error=True`` if the value
            starts with ``-``, or ``None`` if valid.
        """
        if value.startswith("-"):
            return ToolExecutionResult(
                content=f"Invalid {param}: must not start with '-'",
                is_error=True,
            )
        return None

    async def _run_git(
        self,
        args: list[str],
        *,
        cwd: Path | None = None,
        deadline: float = _DEFAULT_TIMEOUT,
    ) -> ToolExecutionResult:
        """Run a git subprocess and return the result.

        Args:
            args: Arguments to pass after ``git``.
            cwd: Working directory (defaults to workspace).
            deadline: Seconds before the process is killed.

        Returns:
            A ``ToolExecutionResult`` with stdout as content on success,
            or stderr/error message with ``is_error=True`` on failure.
        """
        work_dir = cwd or self._workspace
        env = {
            **os.environ,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_NOSYSTEM": "1",
        }

        logger.debug(
            GIT_COMMAND_START,
            command=["git", *args],
            cwd=str(work_dir),
        )

        try:
            proc = await asyncio.create_subprocess_exec(
                "git",
                *args,
                cwd=work_dir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
        except OSError:
            logger.warning(
                GIT_COMMAND_FAILED,
                command=["git", *args],
                error="subprocess start failed",
                exc_info=True,
            )
            return ToolExecutionResult(
                content="Failed to start git process",
                is_error=True,
            )

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(),
                timeout=deadline,
            )
        except TimeoutError:
            proc.kill()
            await proc.communicate()
            logger.warning(
                GIT_COMMAND_TIMEOUT,
                command=["git", *args],
                deadline=deadline,
            )
            return ToolExecutionResult(
                content=f"Git command timed out after {deadline}s",
                is_error=True,
            )

        stdout = stdout_bytes.decode("utf-8", errors="replace").strip()
        stderr = stderr_bytes.decode("utf-8", errors="replace").strip()

        if proc.returncode != 0:
            logger.warning(
                GIT_COMMAND_FAILED,
                command=["git", *args],
                returncode=proc.returncode,
                stderr=stderr,
            )
            error_output = stderr or stdout or "Unknown git error"
            return ToolExecutionResult(
                content=error_output,
                is_error=True,
            )

        logger.debug(
            GIT_COMMAND_SUCCESS,
            command=["git", *args],
        )
        return ToolExecutionResult(content=stdout)


# ── GitStatusTool ─────────────────────────────────────────────────


class GitStatusTool(_BaseGitTool):
    """Show the working tree status of the git repository.

    Returns the output of ``git status`` with optional short or
    porcelain formatting.
    """

    def __init__(self, *, workspace: Path) -> None:
        """Initialize the git_status tool.

        Args:
            workspace: Absolute path to the workspace root.
        """
        super().__init__(
            name="git_status",
            description=(
                "Show the working tree status. Returns modified, staged, "
                "and untracked files in the workspace repository."
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "short": {
                        "type": "boolean",
                        "description": "Use short format output.",
                        "default": False,
                    },
                    "porcelain": {
                        "type": "boolean",
                        "description": "Use machine-readable porcelain format.",
                        "default": False,
                    },
                },
                "additionalProperties": False,
            },
            workspace=workspace,
        )

    async def execute(
        self,
        *,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        """Run ``git status``.

        Args:
            arguments: Optional ``short`` and ``porcelain`` flags.

        Returns:
            A ``ToolExecutionResult`` with the status output.
        """
        args = ["status"]
        if arguments.get("porcelain"):
            args.append("--porcelain")
        elif arguments.get("short"):
            args.append("--short")
        return await self._run_git(args)


# ── GitLogTool ────────────────────────────────────────────────────


class GitLogTool(_BaseGitTool):
    """Show commit log history.

    Returns recent commits with optional filtering by count, author,
    date range, ref, and paths.
    """

    _MAX_COUNT_LIMIT: int = 100

    def __init__(self, *, workspace: Path) -> None:
        """Initialize the git_log tool.

        Args:
            workspace: Absolute path to the workspace root.
        """
        super().__init__(
            name="git_log",
            description=(
                "Show commit log. Returns recent commits with optional "
                "filtering by count, author, date range, and paths."
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "max_count": {
                        "type": "integer",
                        "description": "Max commits (default 10, max 100).",
                        "default": 10,
                        "minimum": 1,
                        "maximum": 100,
                    },
                    "oneline": {
                        "type": "boolean",
                        "description": "Use one-line format.",
                        "default": False,
                    },
                    "ref": {
                        "type": "string",
                        "description": "Branch, tag, or commit ref to start from.",
                    },
                    "author": {
                        "type": "string",
                        "description": "Filter commits by author pattern.",
                    },
                    "since": {
                        "type": "string",
                        "description": "Show commits after date (e.g. '2024-01-01').",
                    },
                    "until": {
                        "type": "string",
                        "description": "Show commits before this date.",
                    },
                    "paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Limit to commits touching these paths.",
                    },
                },
                "additionalProperties": False,
            },
            workspace=workspace,
        )

    async def execute(
        self,
        *,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        """Run ``git log``.

        Args:
            arguments: Log options (max_count, oneline, ref, author,
                since, until, paths).

        Returns:
            A ``ToolExecutionResult`` with the log output.
        """
        max_count = min(
            arguments.get("max_count", 10),
            self._MAX_COUNT_LIMIT,
        )
        args = ["log", f"--max-count={max_count}"]

        if arguments.get("oneline"):
            args.append("--oneline")

        if author := arguments.get("author"):
            args.append(f"--author={author}")

        if since := arguments.get("since"):
            args.append(f"--since={since}")

        if until := arguments.get("until"):
            args.append(f"--until={until}")

        if ref := arguments.get("ref"):
            if err := self._check_ref(ref, param="ref"):
                return err
            args.append(ref)

        paths: list[str] = arguments.get("paths", [])
        if paths:
            if err := self._check_paths(paths):
                return err
            args.append("--")
            args.extend(paths)

        result = await self._run_git(args)
        if not result.is_error and not result.content:
            return ToolExecutionResult(content="No commits found")
        return result


# ── GitDiffTool ───────────────────────────────────────────────────


class GitDiffTool(_BaseGitTool):
    """Show changes between commits, the index, and the working tree.

    Returns the output of ``git diff`` with optional ref comparison,
    staged changes view, stat summary, and path filtering.
    """

    def __init__(self, *, workspace: Path) -> None:
        """Initialize the git_diff tool.

        Args:
            workspace: Absolute path to the workspace root.
        """
        super().__init__(
            name="git_diff",
            description=(
                "Show changes between commits, index, and working tree. "
                "Supports staged changes, ref comparison, and path filtering."
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "staged": {
                        "type": "boolean",
                        "description": "Show staged (cached) changes.",
                        "default": False,
                    },
                    "ref1": {
                        "type": "string",
                        "description": "First ref for comparison.",
                    },
                    "ref2": {
                        "type": "string",
                        "description": "Second ref for comparison.",
                    },
                    "stat": {
                        "type": "boolean",
                        "description": "Show diffstat summary instead of full diff.",
                        "default": False,
                    },
                    "paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Limit diff to these paths.",
                    },
                },
                "additionalProperties": False,
            },
            workspace=workspace,
        )

    async def execute(
        self,
        *,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        """Run ``git diff``.

        Args:
            arguments: Diff options (staged, ref1, ref2, stat, paths).

        Returns:
            A ``ToolExecutionResult`` with the diff output. Empty diff
            returns "No changes" (not an error).
        """
        args = ["diff"]

        if arguments.get("staged"):
            args.append("--cached")

        if arguments.get("stat"):
            args.append("--stat")

        if ref1 := arguments.get("ref1"):
            if err := self._check_ref(ref1, param="ref1"):
                return err
            args.append(ref1)
        if ref2 := arguments.get("ref2"):
            if err := self._check_ref(ref2, param="ref2"):
                return err
            args.append(ref2)

        paths: list[str] = arguments.get("paths", [])
        if paths:
            if err := self._check_paths(paths):
                return err
            args.append("--")
            args.extend(paths)

        result = await self._run_git(args)
        if not result.is_error and not result.content:
            return ToolExecutionResult(content="No changes")
        return result


# ── GitBranchTool ─────────────────────────────────────────────────


class GitBranchTool(_BaseGitTool):
    """Manage branches — list, create, switch, or delete.

    Supports listing all branches, creating new branches (optionally
    from a start point), switching between branches, and deleting
    branches.
    """

    _ACTIONS_REQUIRING_NAME = frozenset({"create", "switch", "delete"})

    def __init__(self, *, workspace: Path) -> None:
        """Initialize the git_branch tool.

        Args:
            workspace: Absolute path to the workspace root.
        """
        super().__init__(
            name="git_branch",
            description=(
                "Manage branches: list, create, switch, or delete. "
                "Provide an action and branch name as needed."
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["list", "create", "switch", "delete"],
                        "description": "Branch action to perform.",
                        "default": "list",
                    },
                    "name": {
                        "type": "string",
                        "description": (
                            "Branch name (required for create/switch/delete)."
                        ),
                    },
                    "start_point": {
                        "type": "string",
                        "description": "Starting ref for branch creation.",
                    },
                    "force": {
                        "type": "boolean",
                        "description": "Force delete (-D) instead of safe delete (-d).",
                        "default": False,
                    },
                },
                "required": ["action"],
                "additionalProperties": False,
            },
            workspace=workspace,
        )

    async def _list_branches(self) -> ToolExecutionResult:
        """List all branches."""
        result = await self._run_git(["branch", "-a"])
        if not result.is_error and not result.content:
            return ToolExecutionResult(content="No branches found")
        return result

    async def _create_branch(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        """Create a branch, optionally from a start point."""
        args = ["branch", name]
        if start_point := arguments.get("start_point"):
            if err := self._check_ref(start_point, param="start_point"):
                return err
            args.append(start_point)
        return await self._run_git(args)

    async def execute(
        self,
        *,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        """Run a branch operation.

        Args:
            arguments: Branch action, name, start_point, force.

        Returns:
            A ``ToolExecutionResult`` with the operation output.
        """
        action: str = arguments.get("action", "list")
        name: str | None = arguments.get("name")

        if action in self._ACTIONS_REQUIRING_NAME and not name:
            return ToolExecutionResult(
                content=f"Branch name is required for '{action}' action",
                is_error=True,
            )

        if action == "list":
            return await self._list_branches()

        # Narrow name to str — guaranteed by _ACTIONS_REQUIRING_NAME guard
        branch_name: str = name  # type: ignore[assignment]

        if err := self._check_ref(branch_name, param="name"):
            return err

        if action == "create":
            return await self._create_branch(branch_name, arguments)

        if action == "switch":
            return await self._run_git(["switch", branch_name])

        # action == "delete" (per schema enum constraint)
        flag = "-D" if arguments.get("force") else "-d"
        return await self._run_git(["branch", flag, branch_name])


# ── GitCommitTool ─────────────────────────────────────────────────


class GitCommitTool(_BaseGitTool):
    """Stage and commit changes.

    Stages specified paths (or all changes with ``all=True``), then
    creates a commit with the provided message.
    """

    def __init__(self, *, workspace: Path) -> None:
        """Initialize the git_commit tool.

        Args:
            workspace: Absolute path to the workspace root.
        """
        super().__init__(
            name="git_commit",
            description=(
                "Stage and commit changes. Provide a commit message and "
                "optionally specify paths to stage or use 'all' to stage "
                "everything."
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "Commit message.",
                    },
                    "paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Paths to stage before committing.",
                    },
                    "all": {
                        "type": "boolean",
                        "description": "Stage all modified and deleted files.",
                        "default": False,
                    },
                },
                "required": ["message"],
                "additionalProperties": False,
            },
            workspace=workspace,
        )

    async def execute(
        self,
        *,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        """Stage and commit changes.

        Args:
            arguments: Commit message, optional paths, optional all flag.

        Returns:
            A ``ToolExecutionResult`` with the commit output.
        """
        message: str = arguments["message"]
        paths: list[str] = arguments.get("paths", [])
        stage_all: bool = arguments.get("all", False)

        # Stage files
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

        # Commit
        return await self._run_git(["commit", "-m", message])


# ── GitCloneTool ──────────────────────────────────────────────────


class GitCloneTool(_BaseGitTool):
    """Clone a git repository into the workspace.

    Validates that the target directory stays within the workspace
    boundary.  Supports optional branch selection and shallow clone
    depth.
    """

    def __init__(self, *, workspace: Path) -> None:
        """Initialize the git_clone tool.

        Args:
            workspace: Absolute path to the workspace root.
        """
        super().__init__(
            name="git_clone",
            description=(
                "Clone a git repository into a directory within the "
                "workspace. Supports branch selection and shallow clones."
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "Repository URL to clone.",
                    },
                    "directory": {
                        "type": "string",
                        "description": "Target directory name within workspace.",
                    },
                    "branch": {
                        "type": "string",
                        "description": "Branch to clone.",
                    },
                    "depth": {
                        "type": "integer",
                        "description": "Shallow clone depth.",
                        "minimum": 1,
                    },
                },
                "required": ["url"],
                "additionalProperties": False,
            },
            workspace=workspace,
        )

    async def execute(
        self,
        *,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        """Clone a repository.

        Args:
            arguments: Clone URL, optional directory, branch, depth.

        Returns:
            A ``ToolExecutionResult`` with the clone output.
        """
        url: str = arguments["url"]

        if not _is_allowed_clone_url(url):
            return ToolExecutionResult(
                content=(
                    "Invalid clone URL. Only https://, http://, ssh://, "
                    "git://, and SCP-like (user@host:path) URLs are allowed"
                ),
                is_error=True,
            )

        args = ["clone"]

        if branch := arguments.get("branch"):
            args.extend(["--branch", branch])

        if depth := arguments.get("depth"):
            args.extend(["--depth", str(depth)])

        args.append(url)

        if directory := arguments.get("directory"):
            if err := self._check_paths([directory]):
                return err
            args.append(directory)

        return await self._run_git(args, deadline=_CLONE_TIMEOUT)
