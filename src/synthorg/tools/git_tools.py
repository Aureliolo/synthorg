"""Built-in git tools for version control operations.

Provides workspace-scoped git tools that agents use to interact with
git repositories.  All tools enforce workspace boundary security -- the
LLM never controls absolute paths.  See ``_git_base._BaseGitTool`` for
the subprocess execution model, environment hardening, and path
validation shared by all tools.
"""

from pathlib import Path  # noqa: TC003 -- used at runtime
from typing import TYPE_CHECKING, Any, ClassVar, Final

from pydantic import BaseModel  # noqa: TC002 -- ClassVar type at runtime

from synthorg.core.enums import ActionType
from synthorg.observability import get_logger
from synthorg.observability.events.git import (
    GIT_CLONE_DNS_PINNED,
    GIT_CLONE_TOCTOU_SKIPPED,
    GIT_CLONE_URL_REJECTED,
    GIT_COMMAND_START,
)
from synthorg.tools._git_args import (
    GitBranchArgs,
    GitCloneArgs,
    GitCommitArgs,
    GitDiffArgs,
    GitLogArgs,
    GitStatusArgs,
)
from synthorg.tools._git_base import _BaseGitTool
from synthorg.tools.base import ToolExecutionResult
from synthorg.tools.git_url_validator import (
    _CREDENTIAL_RE,
    ALLOWED_CLONE_SCHEMES,
    DnsValidationOk,
    GitCloneNetworkPolicy,
    build_curl_resolve_value,
    is_allowed_clone_scheme,
    validate_clone_url_host,
    verify_dns_consistency,
)

if TYPE_CHECKING:
    from synthorg.tools.sandbox.protocol import SandboxBackend

logger = get_logger(__name__)

_CLONE_TIMEOUT: Final[float] = 120.0


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

    args_model: ClassVar[type[BaseModel] | None] = GitLogArgs

    _MAX_COUNT_LIMIT: Final[int] = 100

    def __init__(
        self,
        *,
        workspace: Path,
        sandbox: SandboxBackend | None = None,
    ) -> None:
        """Initialize the git_log tool.

        Args:
            workspace: Absolute path to the workspace root.
            sandbox: Optional sandbox backend for subprocess isolation.
        """
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
        arguments: dict[str, Any],
    ) -> list[str] | ToolExecutionResult:
        """Validate and build ``--author``, ``--since``, ``--until`` args.

        Returns the argument list on success, or an error result if any
        filter value fails the flag-injection check.

        Returns:
            Result of type ``list[str] | ToolExecutionResult``.
        """
        filter_args: list[str] = []
        for param, flag in (
            ("author", "--author"),
            ("since", "--since"),
            ("until", "--until"),
        ):
            if value := arguments.get(param):
                if err := self._check_git_arg(value, param=param):
                    return err
                filter_args.append(f"{flag}={value}")
        return filter_args

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

        filter_args = self._build_filter_args(arguments)
        if isinstance(filter_args, ToolExecutionResult):
            return filter_args
        args.extend(filter_args)

        if ref := arguments.get("ref"):
            if err := self._check_git_arg(ref, param="ref"):
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
            if err := self._check_git_arg(ref1, param="ref1"):
                return err
            args.append(ref1)
        if ref2 := arguments.get("ref2"):
            if not ref1:
                return ToolExecutionResult(
                    content="ref2 requires ref1 to be specified",
                    is_error=True,
                )
            if err := self._check_git_arg(ref2, param="ref2"):
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
    """Manage branches -- list, create, switch, or delete.

    Supports listing all branches, creating new branches (optionally
    from a start point), switching between branches, and deleting
    branches.
    """

    args_model: ClassVar[type[BaseModel] | None] = GitBranchArgs

    _ACTIONS_REQUIRING_NAME = frozenset({"create", "switch", "delete"})

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
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        """Create a branch, optionally from a start point.

        Returns:
            Result of type ``ToolExecutionResult``.
        """
        args = ["branch", name]
        if start_point := arguments.get("start_point"):
            if err := self._check_git_arg(start_point, param="start_point"):
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
                content=(f"Branch name is required for '{action}' action"),
                is_error=True,
            )

        if action == "list":
            return await self._list_branches()

        # Narrowing: guaranteed non-None by guard above.
        branch_name: str = name  # type: ignore[assignment]

        if err := self._check_git_arg(branch_name, param="name"):
            return err

        if action == "create":
            return await self._create_branch(branch_name, arguments)

        if action == "switch":
            return await self._run_git(["switch", branch_name])

        if action == "delete":
            flag = "-D" if arguments.get("force") else "-d"
            return await self._run_git(["branch", flag, branch_name])

        return ToolExecutionResult(
            content=f"Unknown branch action: {action!r}",
            is_error=True,
        )


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


# ── GitCloneTool ──────────────────────────────────────────────────


class GitCloneTool(_BaseGitTool):
    """Clone a git repository into the workspace.

    Validates that the target directory stays within the workspace
    boundary.  Supports optional branch selection and shallow clone
    depth.  URLs are validated against allowed schemes (https, ssh,
    SCP-like) and checked for SSRF via hostname/IP validation with
    async DNS resolution.  Local paths, ``file://``, and plain
    ``http://`` URLs are rejected.
    """

    args_model: ClassVar[type[BaseModel] | None] = GitCloneArgs

    def __init__(
        self,
        *,
        workspace: Path,
        sandbox: SandboxBackend | None = None,
        network_policy: GitCloneNetworkPolicy | None = None,
    ) -> None:
        """Initialize the git_clone tool.

        Args:
            workspace: Workspace root for clone destinations.
            sandbox: Optional sandbox backend that runs ``git`` in
                isolation. ``None`` runs locally inside the workspace.
            network_policy: SSRF + scheme allowlist policy applied to
                the requested URL. ``None`` uses the default
                conservative policy (HTTPS + SSH only).
        """
        super().__init__(
            name="git_clone",
            action_type=ActionType.VCS_READ,
            description=(
                "Clone a git repository into a directory within the "
                "workspace. Supports branch selection and shallow clones."
            ),
            parameters_schema=GitCloneArgs.model_json_schema(),
            workspace=workspace,
            sandbox=sandbox,
        )
        self._network_policy = (
            network_policy if network_policy is not None else GitCloneNetworkPolicy()
        )

    async def _apply_toctou_mitigation(
        self,
        args: list[str],
        validation: DnsValidationOk,
    ) -> list[str] | ToolExecutionResult:
        """Apply DNS rebinding mitigation based on transport type.

        For HTTPS URLs, prepends ``-c http.curloptResolve=...`` to
        *args* to pin git to the validated IPs.  For SSH/SCP URLs,
        performs a double-resolve consistency check.

        Args:
            args: Git command arguments (``["clone", ...]``).
            validation: Successful DNS validation result.

        Returns:
            The *args* list (potentially augmented with DNS pinning
            config for HTTPS) on success, or a
            ``ToolExecutionResult`` with ``is_error=True`` if DNS
            rebinding is detected.
        """
        if not validation.resolved_ips:
            # Literal IP, allowlisted host, IP blocking disabled,
            # or TOCTOU mitigation disabled -- no IPs to pin.
            logger.debug(
                GIT_CLONE_TOCTOU_SKIPPED,
                hostname=validation.hostname,
            )
            return args

        if validation.is_https:
            # Pin git to validated IPs via curloptResolve (git >= 2.37).
            # The sandbox container ships git 2.39+ (Debian bookworm);
            # no runtime version check needed since we control the image.
            # resolved_ips is guaranteed non-empty here (guard above).
            resolve_value = build_curl_resolve_value(
                validation.hostname,
                validation.port or 443,
                validation.resolved_ips,
            )
            logger.info(
                GIT_CLONE_DNS_PINNED,
                hostname=validation.hostname,
                resolve_value=resolve_value,
            )
            return ["-c", f"http.curloptResolve={resolve_value}", *args]

        # SSH/SCP: double-resolve and compare before execution
        rebind_err = await verify_dns_consistency(
            validation.hostname,
            frozenset(validation.resolved_ips),
            self._network_policy.dns_resolution_timeout,
        )
        if rebind_err is not None:
            return ToolExecutionResult(
                content=rebind_err,
                is_error=True,
            )
        return args

    async def execute(
        self,
        *,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        """Clone a repository.

        Validation order: scheme check -> argument checks (branch,
        depth, directory) -> SSRF host/IP check -> TOCTOU DNS
        rebinding mitigation -> ``git clone``.  All cheap local
        checks run before the async DNS lookup.

        Args:
            arguments: Clone URL, optional directory, branch, depth.

        Returns:
            A ``ToolExecutionResult`` with the clone output.
        """
        url: str = arguments["url"]

        if not is_allowed_clone_scheme(url):
            logger.warning(
                GIT_CLONE_URL_REJECTED,
                url=_CREDENTIAL_RE.sub(r"\1***@", url),
            )
            schemes = ", ".join(ALLOWED_CLONE_SCHEMES)
            return ToolExecutionResult(
                content=(
                    f"Invalid clone URL. Only {schemes} "
                    "and SCP-like (user@host:path) URLs are "
                    "allowed"
                ),
                is_error=True,
            )

        args = ["clone"]

        if branch := arguments.get("branch"):
            if err := self._check_git_arg(branch, param="branch"):
                return err
            args.extend(["--branch", branch])

        if depth := arguments.get("depth"):
            args.extend(["--depth", str(depth)])

        args.append("--")
        args.append(url)

        if directory := arguments.get("directory"):
            if err := self._check_paths([directory]):
                return err
            args.append(directory)

        # SSRF prevention: validate hostname/IP after all local checks.
        validation = await validate_clone_url_host(url, self._network_policy)
        if isinstance(validation, str):
            return ToolExecutionResult(content=validation, is_error=True)

        # TOCTOU DNS rebinding mitigation
        result = await self._apply_toctou_mitigation(args, validation)
        if isinstance(result, ToolExecutionResult):
            return result
        args = result

        return await self._run_git(args, deadline=_CLONE_TIMEOUT)
