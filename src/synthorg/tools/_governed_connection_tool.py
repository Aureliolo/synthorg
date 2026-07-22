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
from synthorg.meta.mcp.errors import GuardrailViolationError
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

_DEFAULT_ACTION_TYPE = ActionType.COMMS_EXTERNAL.value


class _AsyncCloseable(Protocol):
    """A per-call client that releases its transport via ``aclose()``."""

    async def aclose(self) -> None: ...


class _RetryAfterCarrier(Protocol):
    """A rate-limit error carrying the upstream-advertised cooldown."""

    retry_after_seconds: float | None


class _ConnectionToolRuntime(Protocol):
    """The boot-scoped runtime bits the shared pipeline reads."""

    @property
    def connection_catalog(self) -> ConnectionCatalog: ...
    @property
    def connection_name(self) -> str: ...
    @property
    def timeout_seconds(self) -> float: ...


class _GateDeps(Protocol):
    """The per-run collaborators the approval gate is built from."""

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


def build_connection_gate(
    deps: _GateDeps, *, action_type: str = _DEFAULT_ACTION_TYPE
) -> ConnectionApprovalGate:
    """Build the shared one-shot approval gate from per-run collaborators.

    Args:
        deps: The per-run collaborators the gate binds to.
        action_type: The action type the gate parks and auto-approves
            under. Families that reach a materially different system
            (a deploy target, not a chat channel) bind their own so
            autonomy grants and risk classification stay separable.

    Returns:
        The gate bound to this run's agent + task.
    """
    return ConnectionApprovalGate(
        approval_store=deps.approval_store,
        agent_id=deps.agent_id,
        task_id=deps.task_id,
        action_type=action_type,
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

    # The action type this family parks, auto-approves and classifies risk
    # under. A family reaching a materially different system binds its own:
    # sharing one type would let an autonomy grant for the tamest family
    # silently cover the most dangerous one.
    _ACTION_TYPE: ClassVar[str] = _DEFAULT_ACTION_TYPE
    # Whether this tool destroys or replaces upstream state, as opposed to
    # merely writing (opening an issue is a write; replacing what is running
    # in production is not). Destructive tools additionally carry the
    # confirm + reason + actor guardrail.
    _DESTRUCTIVE: ClassVar[bool] = False

    def __init__(
        self,
        *,
        name: str,
        description: str,
        args_model: type[BaseModel],
        runtime: RuntimeT,
        gate_deps: _GateDeps,
    ) -> None:
        super().__init__(
            name=name,
            description=description,
            # EXTERNAL_DATA (not COMMUNICATION): these tools are external-API
            # access of the same shape as the external_api tool (bound
            # connection, credential-brokered, approval-gated egress).
            # Governance is the action_type + the gate, not the category.
            category=ToolCategory.EXTERNAL_DATA,
            action_type=self._ACTION_TYPE,
            parameters_schema=args_model.model_json_schema(),
        )
        self._runtime: RuntimeT = runtime
        self._gate_deps = gate_deps

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
        # Preconditions run before the gate so a call that was never
        # admissible (unconfirmed, unattributable) is refused outright
        # rather than parking an approval for a human to adjudicate.
        self._check_preconditions(args)
        conn = await self._resolve_connection(args)
        governed = require_governed_args(args)
        # A connection the operator marked sensitive gates every call (read
        # or write); otherwise only mutating actions park for approval.
        if conn.sensitive or governed.is_write:
            # Built here, not at construction: the action type can depend on
            # the resolved connection (a staging target is not a production
            # one), and the connection is only known once args are parsed.
            gate = build_connection_gate(
                self._gate_deps, action_type=self._action_type_for(conn)
            )
            parked = await gate.gate(
                signature_for(
                    namespace=self.name,
                    connection=conn.name,
                    args=args,
                ),
                connection=conn.name,
                approval_id=None,
                title=f"{self._KIND} {self.name} on {conn.name!r}",
                description=f"Agent requests a {self._KIND.lower()} {self.name} call.",
            )
            if parked is not None:
                return parked
        token = await self._resolve_token(conn)
        client = self._build_client(
            conn=conn,
            token=token,
            timeout=self._runtime.timeout_seconds,
        )
        try:
            return await self._dispatch_guarded(client, args)
        finally:
            await self._safe_aclose(client)

    def _check_preconditions(
        self,
        args: BaseModel,
    ) -> None:
        """Reject a call that is inadmissible regardless of approval.

        Runs before the approval gate. Destructive families override this
        to enforce the confirm + reason + actor triple, so a call nobody
        could authorise never reaches a human as a parked approval.

        Args:
            args: The parsed arguments.
        """

    def _action_type_for(
        self,
        conn: Connection,  # noqa: ARG002 -- read by families with per-target risk
    ) -> str:
        """Resolve the action type this call parks and classifies under.

        Args:
            conn: The already-resolved connection.

        Returns:
            The family's action type. Families whose blast radius varies
            per connection (a staging versus a production deploy target)
            override this to read the connection record, never an
            agent-supplied value.
        """
        return self._ACTION_TYPE

    async def _resolve_connection(
        self,
        args: BaseModel,  # noqa: ARG002 -- read by families selecting a target per call
    ) -> Connection:
        """Resolve the connection this call runs against.

        Args:
            args: The parsed arguments, for families that select their
                target per call rather than binding one at boot.

        Returns:
            The resolved connection.

        Raises:
            ToolError: The family's typed leaf when the connection is
                missing, of an unsupported type, or lacks a base_url.
        """
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
        conn: Connection,
        token: str,
        timeout: float,
    ) -> ClientT:
        """Build the per-call client, mapping a config error to the leaf.

        Receives the whole connection, not just its type and base URL, so
        a family whose client selection depends on operator-set record
        metadata can read it without smuggling state across the call.
        """

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
        except (
            ToolError,
            GovernedApprovalMismatchError,
            GuardrailViolationError,
        ) as exc:
            # A guardrail violation is a correctable caller error (no
            # confirm, blank reason): surface it as a result the agent can
            # act on rather than an exception that aborts the invoker.
            return ToolExecutionResult(content=str(exc), is_error=True)


__all__ = [
    "GovernedConnectionTool",
    "build_connection_gate",
    "json_result",
]
