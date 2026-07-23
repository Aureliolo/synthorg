"""Forge-specific bindings for the shared governed-connection tool base.

The connection-resolution / approval-gating / dispatch / error-mapping
pipeline lives in :mod:`synthorg.tools._governed_connection_tool`; this
module supplies only the forge-specific hooks: the agent-operations client
builder, the support predicate, and the upstream-error translation onto the
``Forge*`` typed leaves. Concrete ``forge_*`` tools subclass ``_BaseForgeTool``
and implement ``_dispatch``.
"""

from abc import ABC
from fnmatch import fnmatch
from typing import ClassVar, override

from pydantic import BaseModel

from synthorg.core.domain_errors import FeatureNotImplementedError
from synthorg.engine.errors import (
    GitBackendConfigError,
    GitBackendForgeApiError,
    GitBackendForgeAuthError,
    GitBackendRateLimitError,
)
from synthorg.engine.workspace.git_backend.forge_api import (
    ForgeAgentApiClient,
    build_forge_agent_api_client,
    forge_agent_api_supported,
)
from synthorg.integrations.connections.models import Connection, ConnectionType
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.tool import (
    FORGE_TOOL_CONNECTION_FAILED,
    FORGE_TOOL_CREDENTIAL_FAILED,
    FORGE_TOOL_REPO_SCOPE_DENIED,
)
from synthorg.tools._governed_connection_tool import GovernedConnectionTool
from synthorg.tools._governed_connection_tool import json_result as _json_result
from synthorg.tools.base import ToolExecutionResult
from synthorg.tools.errors import ToolError
from synthorg.tools.forge._args import _ForgeArgsBase
from synthorg.tools.forge._runtime import ForgeToolDeps, ForgeToolsRuntime
from synthorg.tools.forge.errors import (
    ForgeConnectionNotFoundError,
    ForgeCredentialError,
    ForgeRateLimitedError,
    ForgeRepoScopeError,
    ForgeToolArgumentError,
    ForgeUnsupportedError,
    ForgeUpstreamError,
)

logger = get_logger(__name__)

_TRUNCATED_NOTE = "\n... [truncated]"


def _repo_in_scope(owner: str, repo: str, allowed: tuple[str, ...]) -> bool:
    """Whether ``owner/repo`` matches any allowed scope entry.

    Scope entries are ``owner/repo`` with ``fnmatch`` globs permitted
    (``owner/*``, ``*/*``). An empty scope matches nothing (fail-closed).

    Returns:
        ``True`` if the repository is admitted by the scope.
    """
    target = f"{owner}/{repo}"
    return any(fnmatch(target, pattern) for pattern in allowed)


class _BaseForgeTool(
    GovernedConnectionTool[ForgeAgentApiClient, ForgeToolsRuntime], ABC
):
    """Forge bindings for the shared governed-connection tool pipeline."""

    _KIND: ClassVar[str] = "Forge"
    _CONNECTION_FAILED_EVENT: ClassVar[str] = FORGE_TOOL_CONNECTION_FAILED
    _CREDENTIAL_FAILED_EVENT: ClassVar[str] = FORGE_TOOL_CREDENTIAL_FAILED
    _REQUIRE_BASE_URL: ClassVar[bool] = True
    _UNSUPPORTED_MSG: ClassVar[str] = (
        "Forge type {ctype!r} has no agent-operations client wired"
    )
    _UNSUPPORTED_REASON: ClassVar[str] = "unsupported_forge"
    _not_found_error: ClassVar[type[ToolError]] = ForgeConnectionNotFoundError
    _unsupported_error: ClassVar[type[ToolError]] = ForgeUnsupportedError
    _argument_error: ClassVar[type[ToolError]] = ForgeToolArgumentError
    _credential_error: ClassVar[type[ToolError]] = ForgeCredentialError
    _rate_limited_error: ClassVar[type[ToolError]] = ForgeRateLimitedError

    def __init__(
        self,
        *,
        name: str,
        description: str,
        args_model: type[BaseModel],
        deps: ForgeToolDeps,
    ) -> None:
        super().__init__(
            name=name,
            description=description,
            args_model=args_model,
            runtime=deps.runtime,
            gate_deps=deps,
        )

    @override
    async def _resolve_connection(self, args: BaseModel) -> Connection:
        """Resolve the connection, then enforce its repository scope.

        The scope check runs after resolution (it reads the live
        connection's ``allowed_repos``) but before the approval gate, so an
        out-of-scope call is refused outright rather than parked for a
        human. The scope is fail-closed: a connection with no repositories
        selected admits none.

        Returns:
            The resolved, in-scope connection.

        Raises:
            ForgeRepoScopeError: When ``owner/repo`` is outside the bound
                connection's ``allowed_repos`` scope.
        """
        conn = await super()._resolve_connection(args)
        if isinstance(args, _ForgeArgsBase):
            owner, repo = str(args.owner), str(args.repo)
            allowed = tuple(str(entry) for entry in conn.allowed_repos)
            if not _repo_in_scope(owner, repo, allowed):
                logger.warning(
                    FORGE_TOOL_REPO_SCOPE_DENIED,
                    connection=conn.name,
                    owner=owner,
                    repo=repo,
                )
                msg = (
                    f"Repository {owner}/{repo!r} is outside connection "
                    f"{conn.name!r}'s allowed scope. Ask an operator to add it."
                )
                raise ForgeRepoScopeError(msg)
        return conn

    @override
    def _supported(self, connection_type: ConnectionType) -> bool:
        return forge_agent_api_supported(connection_type)

    @override
    def _build_client(
        self,
        *,
        conn: Connection,
        token: str,
        timeout: float,
    ) -> ForgeAgentApiClient:
        try:
            return build_forge_agent_api_client(
                connection_type=conn.connection_type,
                base_url=str(conn.base_url or ""),
                token=token,
                timeout=timeout,
            )
        except GitBackendConfigError as exc:
            raise ForgeToolArgumentError(safe_error_description(exc)) from exc

    @override
    async def _dispatch_guarded(
        self, client: ForgeAgentApiClient, args: BaseModel
    ) -> ToolExecutionResult:
        """Dispatch and map lower-level forge-client errors to typed leaves.

        Returns:
            The tool result.

        Raises:
            ForgeRateLimitedError: The forge rate-limited the request.
            ForgeUpstreamError: An auth or other non-2xx / transport failure.
            ForgeUnsupportedError: The forge does not support the operation.
        """
        try:
            return await self._dispatch(client, args)
        except GitBackendRateLimitError as exc:
            msg = "Forge rate-limited the request; retry later"
            raise ForgeRateLimitedError(
                msg, retry_after_seconds=exc.retry_after
            ) from exc
        except GitBackendForgeAuthError as exc:
            msg = "Forge authentication failed (check the connection token/scopes)"
            raise ForgeUpstreamError(msg) from exc
        except GitBackendForgeApiError as exc:
            raise ForgeUpstreamError(safe_error_description(exc)) from exc
        except FeatureNotImplementedError as exc:
            raise ForgeUnsupportedError(str(exc)) from exc


__all__ = [
    "_TRUNCATED_NOTE",
    "ForgeAgentApiClient",
    "ToolExecutionResult",
    "_BaseForgeTool",
    "_json_result",
]
