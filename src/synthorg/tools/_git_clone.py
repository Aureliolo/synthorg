"""Workspace-scoped git clone tool with SSRF + DNS-rebinding mitigation.

Split out of ``git_tools`` because clone carries the entire URL-validation
surface (scheme allowlist, host/IP SSRF checks, TOCTOU DNS pinning) that the
read/branch/commit tools do not. See ``_git_base._BaseGitTool`` for the
subprocess execution model shared by all git tools.
"""

from pathlib import Path
from typing import ClassVar, Final, cast, override

from pydantic import BaseModel

from synthorg.observability import get_logger
from synthorg.observability.events.git import (
    GIT_CLONE_DNS_PINNED,
    GIT_CLONE_TOCTOU_SKIPPED,
    GIT_CLONE_URL_REJECTED,
)
from synthorg.security.autonomy.enums import ActionType
from synthorg.tools._git_args import GitCloneArgs
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
from synthorg.tools.sandbox.protocol import SandboxBackend

logger = get_logger(__name__)

_CLONE_TIMEOUT: Final[float] = 120.0


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

    @override
    async def execute(
        self,
        *,
        arguments: dict[str, object],
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
        url = cast("str", arguments["url"])

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

        if branch := cast("str | None", arguments.get("branch")):
            if err := self._check_git_arg(branch, param="branch"):
                return err
            args.extend(["--branch", branch])

        if depth := arguments.get("depth"):
            args.extend(["--depth", str(depth)])

        args.append("--")
        args.append(url)

        if directory := cast("str | None", arguments.get("directory")):
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
