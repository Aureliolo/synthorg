"""Shared generic base for connection-gated, approval-governed agent tools.

The forge (``forge_*``) and chat (``chat_*``) tool families run the identical
governance pipeline: resolve the bound connection, gate sensitive/mutating
calls through the identity-bound one-shot approval flow, broker credentials,
build a per-call client pinned to the connection host, dispatch exactly once,
and normalise the outcome (including upstream failures) into a
``ToolExecutionResult``. They differ only in the client type, the runtime
bundle, the support predicate, whether a ``base_url`` is mandatory, and how
upstream-library errors translate to the family's typed leaves.

This base owns the shared pipeline so the two families cannot silently drift;
each subclass supplies only the type-specific hooks. New governance behaviour
added here applies to every family at once.
"""

import json
from abc import ABC, abstractmethod
from typing import ClassVar, Protocol, cast, override

from pydantic import BaseModel, JsonValue
from pydantic import ValidationError as PydanticValidationError

from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.core.boundary import parse_typed
from synthorg.core.clock import Clock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.effective_autonomy import EffectiveAutonomy
from synthorg.integrations.connections.catalog import ConnectionCatalog
from synthorg.integrations.connections.models import Connection, ConnectionType
from synthorg.integrations.errors import SecretRetrievalError
from synthorg.observability import get_logger, safe_error_description
from synthorg.security.autonomy.enums import ActionType, ToolCategory
from synthorg.security.timeout.protocol import RiskTierClassifier
from synthorg.tools._governed_action import (
    ConnectionApprovalGate,
    GovernedApprovalMismatchError,
    require_governed_args,
    signature_for,
)
from synthorg.tools.base import BaseTool, ToolExecutionResult
from synthorg.tools.errors import ToolError

logger = get_logger(__name__)

_ACTION_TYPE = ActionType.COMMS_EXTERNAL.value


class _AsyncCloseable(Protocol):
    async def aclose(self) -> None: ...


class _RetryAfterCarrier(Protocol):
    retry_after_seconds: float | None


class _ConnectionToolRuntime(Protocol):
    @property
    def connection_catalog(self) -> ConnectionCatalog: ...
    @property
    def connection_name(self) -> str: ...
    @property
    def timeout_seconds(self) -> float: ...


class _GateDeps(Protocol):
    @property
    def approval_store(self) -> ApprovalStoreProtocol: ...
    @property
    def agent_id(self) -> str: ...
    @property
    def task_id(self) -> str | None: ...
    @property
    def effective_autonomy(self) -> EffectiveAutonomy | None: ...
    @property
    def risk_classifier(self) -> RiskTierClassifier | None: ...
    @property
    def clock(self) -> Clock | None: ...


def build_connection_gate(deps: _GateDeps) -> ConnectionApprovalGate:
    """Build the shared one-shot approval gate from per-run collaborators.

    Returns:
        The gate bound to this run's agent + task.
    """
    return ConnectionApprovalGate(
        approval_store=deps.approval_store,
        agent_id=deps.agent_id,
        task_id=deps.task_id,
        action_type=_ACTION_TYPE,
        effective_autonomy=deps.effective_autonomy,
        risk_classifier=deps.risk_classifier,
        clock=deps.clock,
    )


def json_result(data: object) -> ToolExecutionResult:
    """Serialise a JSON-able payload as a successful tool result.

    Returns:
        The formatted tool result.
    """
    return ToolExecutionResult(content=json.dumps(data, ensure_ascii=False))


class GovernedConnectionTool[
    ClientT: _AsyncCloseable,
    RuntimeT: _ConnectionToolRuntime,
](BaseTool, ABC):
    """Connection-resolution + approval gating shared by governed tools.

    Subclasses bind ``ClientT`` / ``RuntimeT`` and supply the family
    constants (kind label, log events, error leaves, host policy) plus the
    type-specific hooks (``_supported`` / ``_build_client`` /
    ``_dispatch_guarded`` / ``_dispatch``).
    """

    args_model: ClassVar[type[BaseModel] | None] = None

    # Family-specific configuration, set on the per-family subclass.
    _KIND: ClassVar[str]
    _CONNECTION_FAILED_EVENT: ClassVar[str]
    _CREDENTIAL_FAILED_EVENT: ClassVar[str]
    _REQUIRE_BASE_URL: ClassVar[bool]
    _UNSUPPORTED_MSG: ClassVar[str]
    _UNSUPPORTED_REASON: ClassVar[str]
    _not_found_error: ClassVar[type[ToolError]]
    _unsupported_error: ClassVar[type[ToolError]]
    _argument_error: ClassVar[type[ToolError]]
    _credential_error: ClassVar[type[ToolError]]
    _rate_limited_error: ClassVar[type[ToolError]]

    def __init__(
        self,
        *,
        name: str,
        description: str,
        args_model: type[BaseModel],
        runtime: RuntimeT,
        gate: ConnectionApprovalGate,
    ) -> None:
        super().__init__(
            name=name,
            description=description,
            # EXTERNAL_DATA (not COMMUNICATION): these tools are external-API
            # access of the same shape as the external_api tool (bound
            # connection, credential-brokered, approval-gated egress).
            # Governance is the COMMS_EXTERNAL action_type + the gate, not
            # the category.
            category=ToolCategory.EXTERNAL_DATA,
            action_type=_ACTION_TYPE,
            parameters_schema=args_model.model_json_schema(),
        )
        self._runtime: RuntimeT = runtime
        self._gate = gate

    @property
    def _catalog(self) -> ConnectionCatalog:
        return self._runtime.connection_catalog

    async def _run(self, args: BaseModel) -> ToolExecutionResult:
        """Resolve the connection, gate writes, then dispatch.

        Returns:
            The tool result, or an approval-parking result.

        Raises:
            ToolError: A connection / credential / argument / upstream
                failure, as the family's typed leaf.
        """
        conn = await self._resolve_connection()
        governed = require_governed_args(args)
        # A connection the operator marked sensitive gates every call (read
        # or write); otherwise only mutating actions park for approval.
        if conn.sensitive or governed.is_write:
            parked = await self._gate.gate(
                signature_for(
                    namespace=self.name,
                    connection=self._runtime.connection_name,
                    args=args,
                ),
                connection=self._runtime.connection_name,
                approval_id=None,
                title=f"{self._KIND} {self.name} on {self._runtime.connection_name!r}",
                description=f"Agent requests a {self._KIND.lower()} {self.name} call.",
            )
            if parked is not None:
                return parked
        token = await self._resolve_token(conn)
        client = self._build_client(
            connection_type=conn.connection_type,
            base_url=str(conn.base_url or ""),
            token=token,
            timeout=self._runtime.timeout_seconds,
        )
        try:
            return await self._dispatch_guarded(client, args)
        finally:
            await self._safe_aclose(client)

    async def _resolve_connection(self) -> Connection:
        conn = await self._catalog.get(self._runtime.connection_name)
        if conn is None:
            logger.warning(
                self._CONNECTION_FAILED_EVENT,
                connection=self._runtime.connection_name,
                reason="connection_not_found",
            )
            msg = f"{self._KIND} connection {self._runtime.connection_name!r} not found"
            raise self._not_found_error(msg)
        if not self._supported(conn.connection_type):
            logger.warning(
                self._CONNECTION_FAILED_EVENT,
                connection=conn.name,
                connection_type=conn.connection_type.value,
                reason=self._UNSUPPORTED_REASON,
            )
            raise self._unsupported_error(
                self._UNSUPPORTED_MSG.format(ctype=conn.connection_type.value)
            )
        if self._REQUIRE_BASE_URL and not conn.base_url:
            logger.warning(
                self._CONNECTION_FAILED_EVENT,
                connection=conn.name,
                reason="missing_base_url",
            )
            msg = f"{self._KIND} connection {conn.name!r} has no base_url"
            raise self._argument_error(msg)
        return conn

    async def _resolve_token(self, conn: Connection) -> str:
        try:
            credentials = await self._catalog.get_credentials(conn.name)
        except SecretRetrievalError as exc:
            logger.warning(
                self._CREDENTIAL_FAILED_EVENT,
                connection=conn.name,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = f"Failed to broker credentials for {self._KIND.lower()} connection"
            raise self._credential_error(msg) from exc
        token = credentials.get("token")
        if not token:
            msg = f"{self._KIND} connection {conn.name!r} has no token"
            raise self._credential_error(msg)
        return token

    async def _safe_aclose(self, client: ClientT) -> None:
        """Release the client without letting cleanup mask the real error.

        A failing ``aclose()`` in a ``finally`` would otherwise replace the
        in-flight exception (or ``CancelledError``) with its own.
        """
        try:
            await client.aclose()
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                self._CONNECTION_FAILED_EVENT,
                reason="client_close_failed",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

    @abstractmethod
    def _supported(self, connection_type: ConnectionType) -> bool:
        """Whether the bound connection type has a wired client."""

    @abstractmethod
    def _build_client(
        self,
        *,
        connection_type: ConnectionType,
        base_url: str,
        token: str,
        timeout: float,
    ) -> ClientT:
        """Build the per-call client, mapping a config error to the leaf."""

    @abstractmethod
    async def _dispatch_guarded(
        self, client: ClientT, args: BaseModel
    ) -> ToolExecutionResult:
        """Dispatch and map upstream-client errors to the family's leaves."""

    @abstractmethod
    async def _dispatch(self, client: ClientT, args: BaseModel) -> ToolExecutionResult:
        """Map the parsed action onto a client call and format the result."""

    @override
    async def execute(self, *, arguments: dict[str, object]) -> ToolExecutionResult:
        """Run the tool.

        Returns:
            The tool result (or an approval-parking result). Rate-limit and
            other typed tool errors are returned as ``is_error`` results, not
            raised, so the agent (not the tool) decides whether to retry.
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
        except self._rate_limited_error as exc:
            metadata: dict[str, JsonValue] = {}
            retry_after = cast("_RetryAfterCarrier", exc).retry_after_seconds
            if retry_after is not None:
                metadata["retry_after_seconds"] = retry_after
            return ToolExecutionResult(
                content=str(exc), is_error=True, metadata=metadata
            )
        except (ToolError, GovernedApprovalMismatchError) as exc:
            return ToolExecutionResult(content=str(exc), is_error=True)


__all__ = [
    "GovernedConnectionTool",
    "build_connection_gate",
    "json_result",
]
