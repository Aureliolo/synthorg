"""Forge-specific bindings for the shared governed-connection tool base.

The connection-resolution / approval-gating / dispatch / error-mapping
pipeline lives in :mod:`synthorg.tools._governed_connection_tool`; this
module supplies only the forge-specific hooks: the agent-operations client
builder, the support predicate, and the upstream-error translation onto the
``Forge*`` typed leaves. Concrete ``forge_*`` tools subclass ``_BaseForgeTool``
and implement ``_dispatch``.
"""

from abc import ABC
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
from synthorg.observability import safe_error_description
from synthorg.observability.events.tool import (
    FORGE_TOOL_CONNECTION_FAILED,
    FORGE_TOOL_CREDENTIAL_FAILED,
)
from synthorg.tools._governed_connection_tool import GovernedConnectionTool
from synthorg.tools._governed_connection_tool import json_result as _json_result
from synthorg.tools.base import ToolExecutionResult
from synthorg.tools.errors import ToolError
from synthorg.tools.forge._runtime import ForgeToolDeps, ForgeToolsRuntime
from synthorg.tools.forge.errors import (
    ForgeConnectionNotFoundError,
    ForgeCredentialError,
    ForgeRateLimitedError,
    ForgeToolArgumentError,
    ForgeUnsupportedError,
    ForgeUpstreamError,
)

_TRUNCATED_NOTE = "\n... [truncated]"


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
