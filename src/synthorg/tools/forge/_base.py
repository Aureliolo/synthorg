"""Shared base + helpers for the resource-grouped forge agent tools.

Holds the connection-resolution, credential-brokering, approval-gating,
and error-mapping machinery every concrete ``forge_*`` tool inherits, so
the public tool module carries only the per-resource dispatch logic.
"""

import json
from abc import ABC, abstractmethod
from typing import ClassVar, override

from pydantic import BaseModel, JsonValue
from pydantic import ValidationError as PydanticValidationError

from synthorg.core.boundary import parse_typed
from synthorg.core.critical_errors import reraise_critical
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
from synthorg.integrations.connections.catalog import ConnectionCatalog
from synthorg.integrations.connections.models import Connection
from synthorg.integrations.errors import SecretRetrievalError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.tool import (
    FORGE_TOOL_CONNECTION_FAILED,
    FORGE_TOOL_CREDENTIAL_FAILED,
)
from synthorg.security.autonomy.enums import ActionType, ToolCategory
from synthorg.tools._governed_action import (
    ConnectionApprovalGate,
    GovernedApprovalMismatchError,
    require_governed_args,
    signature_for,
)
from synthorg.tools.base import BaseTool, ToolExecutionResult
from synthorg.tools.forge._runtime import ForgeToolDeps
from synthorg.tools.forge.errors import (
    ForgeConnectionNotFoundError,
    ForgeCredentialError,
    ForgeRateLimitedError,
    ForgeToolArgumentError,
    ForgeToolError,
    ForgeUnsupportedError,
    ForgeUpstreamError,
)

logger = get_logger(__name__)

_ACTION_TYPE = ActionType.COMMS_EXTERNAL.value
_TRUNCATED_NOTE = "\n... [truncated]"


class _BaseForgeTool(BaseTool, ABC):
    """Shared connection-resolution + approval gating for the forge tools."""

    args_model: ClassVar[type[BaseModel] | None] = None

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
            category=ToolCategory.EXTERNAL_DATA,
            action_type=_ACTION_TYPE,
            parameters_schema=args_model.model_json_schema(),
        )
        self._runtime = deps.runtime
        self._gate = ConnectionApprovalGate(
            approval_store=deps.approval_store,
            agent_id=deps.agent_id,
            task_id=deps.task_id,
            action_type=_ACTION_TYPE,
            effective_autonomy=deps.effective_autonomy,
            risk_classifier=deps.risk_classifier,
            clock=deps.clock,
        )

    @property
    def _catalog(self) -> ConnectionCatalog:
        return self._runtime.connection_catalog

    async def _run(self, args: BaseModel) -> ToolExecutionResult:
        """Resolve the connection, gate writes, then dispatch.

        Returns:
            The tool result, or an approval-parking result.

        Raises:
            ForgeToolArgumentError: When the bound connection has no
                base_url, or the base_url fails validation. Connection /
                credential / upstream failures propagate as other
                ``ForgeToolError`` leaves.
        """
        conn = await self._resolve_connection()
        governed = require_governed_args(args)
        # A connection the operator marked sensitive gates every call
        # (read or write), matching the external_api tool; otherwise only
        # mutating actions park for approval.
        if conn.sensitive or governed.is_write:
            parked = await self._gate.gate(
                signature_for(
                    namespace=self.name,
                    connection=self._runtime.connection_name,
                    args=args,
                ),
                connection=self._runtime.connection_name,
                approval_id=None,
                title=f"Forge {self.name} on {self._runtime.connection_name!r}",
                description=f"Agent requests a forge {self.name} call.",
            )
            if parked is not None:
                return parked
        token = await self._resolve_token(conn)
        try:
            client = build_forge_agent_api_client(
                connection_type=conn.connection_type,
                base_url=str(conn.base_url),
                token=token,
                timeout=self._runtime.timeout_seconds,
            )
        except GitBackendConfigError as exc:
            raise ForgeToolArgumentError(safe_error_description(exc)) from exc
        try:
            return await self._dispatch_guarded(client, args)
        finally:
            await _safe_aclose(client)

    async def _resolve_connection(self) -> Connection:
        conn = await self._catalog.get(self._runtime.connection_name)
        if conn is None:
            logger.warning(
                FORGE_TOOL_CONNECTION_FAILED,
                connection=self._runtime.connection_name,
                reason="connection_not_found",
            )
            msg = f"Forge connection {self._runtime.connection_name!r} not found"
            raise ForgeConnectionNotFoundError(msg)
        if not forge_agent_api_supported(conn.connection_type):
            logger.warning(
                FORGE_TOOL_CONNECTION_FAILED,
                connection=conn.name,
                connection_type=conn.connection_type.value,
                reason="unsupported_forge",
            )
            msg = (
                f"Forge type {conn.connection_type.value!r} has no agent-operations"
                " client wired"
            )
            raise ForgeUnsupportedError(msg)
        if not conn.base_url:
            logger.warning(
                FORGE_TOOL_CONNECTION_FAILED,
                connection=conn.name,
                reason="missing_base_url",
            )
            msg = f"Forge connection {conn.name!r} has no base_url"
            raise ForgeToolArgumentError(msg)
        return conn

    async def _resolve_token(self, conn: Connection) -> str:
        try:
            credentials = await self._catalog.get_credentials(conn.name)
        except SecretRetrievalError as exc:
            logger.warning(
                FORGE_TOOL_CREDENTIAL_FAILED,
                connection=conn.name,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = "Failed to broker credentials for forge connection"
            raise ForgeCredentialError(msg) from exc
        token = credentials.get("token")
        if not token:
            msg = f"Forge connection {conn.name!r} has no token"
            raise ForgeCredentialError(msg)
        return token

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

    @abstractmethod
    async def _dispatch(
        self, client: ForgeAgentApiClient, args: BaseModel
    ) -> ToolExecutionResult:
        """Map the parsed action onto a client call and format the result."""

    @override
    async def execute(self, *, arguments: dict[str, object]) -> ToolExecutionResult:
        """Run the forge tool.

        Returns:
            The tool result (or an approval-parking result).
        """
        model = self.args_model
        assert model is not None  # noqa: S101 -- set by every subclass
        try:
            args = parse_typed("tool.execute", arguments, model)
        except PydanticValidationError as exc:
            return ToolExecutionResult(
                content=f"Invalid arguments: {safe_error_description(exc)}",
                is_error=True,
            )
        try:
            return await self._run(args)
        except ForgeRateLimitedError as exc:
            metadata: dict[str, JsonValue] = {}
            if exc.retry_after_seconds is not None:
                metadata["retry_after_seconds"] = exc.retry_after_seconds
            return ToolExecutionResult(
                content=str(exc), is_error=True, metadata=metadata
            )
        except (ForgeToolError, GovernedApprovalMismatchError) as exc:
            return ToolExecutionResult(content=str(exc), is_error=True)


async def _safe_aclose(client: ForgeAgentApiClient) -> None:
    """Release the client without letting cleanup mask the real error.

    A failing ``aclose()`` in a ``finally`` would otherwise replace the
    in-flight exception (or ``CancelledError``) with its own.
    """
    try:
        await client.aclose()
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            FORGE_TOOL_CONNECTION_FAILED,
            reason="client_close_failed",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )


def _json_result(data: object) -> ToolExecutionResult:
    return ToolExecutionResult(content=json.dumps(data, ensure_ascii=False))


__all__ = [
    "_TRUNCATED_NOTE",
    "ForgeAgentApiClient",
    "ToolExecutionResult",
    "_BaseForgeTool",
    "_json_result",
]
