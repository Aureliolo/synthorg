"""Governed external API/data access tool.

Brokers credentials from the connection catalog, constrains egress to the
connection's host via the SSRF ``NetworkPolicy`` + DNS pinning, enforces the
connection's bus-coordinated rate limit, and routes sensitive calls (a
connection flagged ``sensitive`` or any write method) to human approval with a
content-addressed, one-shot consumption guard. Delegates the actual egress to a
pluggable :class:`ExternalAccessProvider`.
"""

from datetime import UTC
from typing import TYPE_CHECKING, Any, ClassVar
from urllib.parse import unquote, urlsplit
from uuid import uuid4

from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError

from synthorg.api.boundary import parse_typed
from synthorg.core.approval import ApprovalItem
from synthorg.core.clock import Clock, SystemClock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.enums import (
    ActionType,
    ApprovalRiskLevel,
    ApprovalSource,
    ApprovalStatus,
    ToolCategory,
)
from synthorg.core.resilience_config import RateLimiterConfig
from synthorg.integrations.errors import (
    ConnectionRateLimitError,
    SecretRetrievalError,
)
from synthorg.integrations.rate_limiting.decorator import with_connection_rate_limit
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.external_api import (
    EXTERNAL_API_APPROVAL_CONSUMED,
    EXTERNAL_API_APPROVAL_REQUIRED,
    EXTERNAL_API_CALL_STARTED,
    EXTERNAL_API_CALL_SUCCEEDED,
    EXTERNAL_API_EGRESS_BLOCKED,
    EXTERNAL_API_RATE_LIMITED,
    EXTERNAL_API_RISK_CLASSIFY_FAILED,
    EXTERNAL_API_SIGNATURE_MISMATCH,
)
from synthorg.providers.url_utils import redact_url
from synthorg.tools.base import BaseTool, ToolExecutionResult
from synthorg.tools.external_api._args import ExternalApiArgs
from synthorg.tools.external_api._credentials import build_auth_headers
from synthorg.tools.external_api._signature import ApprovalSignature
from synthorg.tools.external_api.errors import (
    ExternalApiApprovalMismatchError,
    ExternalApiConnectionNotFoundError,
    ExternalApiCredentialError,
    ExternalApiEgressBlockedError,
    ExternalApiError,
    ExternalApiResponseError,
)
from synthorg.tools.external_api.provider import ExternalAccessRequest
from synthorg.tools.network_validator import (
    NetworkPolicy,
    extract_hostname,
    validate_url_host,
)

if TYPE_CHECKING:
    from synthorg.approval.protocol import ApprovalStoreProtocol
    from synthorg.integrations.connections.catalog import ConnectionCatalog
    from synthorg.integrations.connections.models import Connection
    from synthorg.security.autonomy.models import EffectiveAutonomy
    from synthorg.security.timeout.risk_tier_classifier import (
        DefaultRiskTierClassifier,
    )
    from synthorg.tools.external_api.provider import ExternalAccessProvider

logger = get_logger(__name__)

_ACTION_TYPE = ActionType.EXTERNAL_DATA_REQUEST.value

# Hop-by-hop / framing headers an agent must never set: ``Host`` would allow
# virtual-host injection past the egress host check, and the framing headers
# let an agent desync the request body from the transport.
_RESTRICTED_REQUEST_HEADERS: frozenset[str] = frozenset(
    {"host", "content-length", "transfer-encoding"},
)


class ExternalApiTool(BaseTool):
    """Agent-callable tool for governed external API/data access."""

    args_model: ClassVar[type[BaseModel] | None] = ExternalApiArgs

    def __init__(  # noqa: PLR0913 -- governance collaborators are all required
        self,
        *,
        connection_catalog: ConnectionCatalog,
        approval_store: ApprovalStoreProtocol,
        provider: ExternalAccessProvider,
        agent_id: str,
        task_id: str | None = None,
        network_policy: NetworkPolicy | None = None,
        effective_autonomy: EffectiveAutonomy | None = None,
        risk_classifier: DefaultRiskTierClassifier | None = None,
        max_response_bytes: int,
        timeout_seconds: float,
        default_max_rpm: int,
        clock: Clock | None = None,
    ) -> None:
        super().__init__(
            name="external_api",
            description=(
                "Access an external API or data source through a configured"
                " connection. Provide the connection name plus a relative path"
                " (or an absolute url within the connection's hosts). Credentials"
                " are brokered automatically; rate limits and egress are enforced;"
                " sensitive calls require human approval. On approval, re-issue"
                " the same call to proceed."
            ),
            category=ToolCategory.EXTERNAL_DATA,
            parameters_schema=ExternalApiArgs.model_json_schema(),
        )
        self._catalog = connection_catalog
        self._approval_store = approval_store
        self._provider = provider
        self._agent_id = agent_id
        self._task_id = task_id
        self._network_policy = network_policy or NetworkPolicy()
        self._risk_classifier = risk_classifier
        self._max_response_bytes = max_response_bytes
        self._timeout_seconds = timeout_seconds
        self._default_max_rpm = default_max_rpm
        self._clock: Clock = clock if clock is not None else SystemClock()
        self._auto_approved = (
            effective_autonomy is not None
            and _ACTION_TYPE in effective_autonomy.auto_approve_actions
        )

    async def execute(self, *, arguments: dict[str, Any]) -> ToolExecutionResult:
        """Run a governed external API call.

        Returns:
            Result of type ``ToolExecutionResult``.
        """
        try:
            args = parse_typed("tool.external_api", arguments, ExternalApiArgs)
        except PydanticValidationError as exc:
            return ToolExecutionResult(
                content=f"Invalid arguments: {safe_error_description(exc)}",
                is_error=True,
            )

        try:
            return await self._run(args)
        except ExternalApiError as exc:
            return ToolExecutionResult(content=str(exc), is_error=True)

    async def _run(self, args: ExternalApiArgs) -> ToolExecutionResult:
        """Execute the governed flow; raises ``ExternalApiError`` on failure.

        Returns:
            Result of type ``ToolExecutionResult``.

        Raises:
            ExternalApiConnectionNotFoundError: If the requested resource cannot be
                located.
            ExternalApiEgressBlockedError: If the related operation fails.
        """
        conn = await self._catalog.get(args.connection)
        if conn is None:
            msg = f"Connection {args.connection!r} not found"
            raise ExternalApiConnectionNotFoundError(msg)

        resolved_url = self._resolve_url(conn, args)
        validation = await validate_url_host(resolved_url, self._network_policy)
        if isinstance(validation, str):
            logger.warning(
                EXTERNAL_API_EGRESS_BLOCKED,
                connection=args.connection,
                url=redact_url(resolved_url),
                reason=validation,
            )
            msg = f"Egress blocked: {validation}"
            raise ExternalApiEgressBlockedError(msg)

        signature = ApprovalSignature.build(
            connection=args.connection,
            method=args.method,
            resolved_url=resolved_url,
            body=args.body,
            headers=self._signable_headers(args.headers),
        )
        if (conn.sensitive or args.is_write) and not self._auto_approved:
            gate = await self._gate_approval(args, signature)
            if gate is not None:
                return gate

        merged_headers = self._broker_headers(conn, await self._credentials(conn))
        merged_headers = self._merge_agent_headers(args.headers, merged_headers)

        pinned_ip = validation.resolved_ips[0] if validation.resolved_ips else None
        pinned_hostname = validation.hostname if validation.resolved_ips else None
        request = ExternalAccessRequest(
            method=args.method,
            url=resolved_url,
            headers=merged_headers,
            body=args.body,
            timeout_seconds=self._timeout_seconds,
            max_response_bytes=self._max_response_bytes,
            pinned_ip=pinned_ip,
            pinned_hostname=pinned_hostname,
        )
        logger.info(
            EXTERNAL_API_CALL_STARTED,
            connection=args.connection,
            method=args.method,
            url=redact_url(resolved_url),
        )
        return await self._egress(conn, request)

    def _resolve_url(self, conn: Connection, args: ExternalApiArgs) -> str:
        """Build the target URL and confirm its host matches the connection.

        The egress boundary is host-level (per the design spec): the agent
        may target any path on the connection's host, via a relative path or
        an absolute url on the same host, but never widen to another host.
        ``.`` / ``..`` traversal segments are rejected outright; the
        connection's base-URL path is a default prefix for relative paths,
        not a containment boundary for absolute same-host urls.

        Returns:
            Result of type ``str``.

        Raises:
            ExternalApiEgressBlockedError: If the related operation fails.
        """
        if not conn.base_url:
            logger.warning(
                EXTERNAL_API_EGRESS_BLOCKED,
                connection=args.connection,
                reason="no_base_url",
            )
            msg = f"Connection {args.connection!r} has no base_url"
            raise ExternalApiEgressBlockedError(msg)
        base_host = extract_hostname(conn.base_url)
        if args.path:
            resolved = conn.base_url.rstrip("/") + "/" + args.path.lstrip("/")
        else:
            resolved = args.url
        if self._has_dot_segment(resolved):
            logger.warning(
                EXTERNAL_API_EGRESS_BLOCKED,
                connection=args.connection,
                url=redact_url(resolved),
                reason="path_traversal",
            )
            msg = "URL path must not contain '.' or '..' traversal segments"
            raise ExternalApiEgressBlockedError(msg)
        resolved_host = extract_hostname(resolved)
        if (
            base_host is None
            or resolved_host is None
            or resolved_host.lower() != base_host.lower()
        ):
            logger.warning(
                EXTERNAL_API_EGRESS_BLOCKED,
                connection=args.connection,
                url=redact_url(resolved),
                reason="host_outside_connection",
            )
            msg = (
                f"URL host is not within connection {args.connection!r}"
                f" (allowed host: {base_host!r})"
            )
            raise ExternalApiEgressBlockedError(msg)
        return resolved

    @staticmethod
    def _has_dot_segment(url: str) -> bool:
        """Whether the URL path contains a ``.`` or ``..`` traversal segment.

        The path is percent-decoded first so encoded traversal sequences
        (``%2e`` / ``%2e%2e``) that an upstream server would normalise back
        into ``.`` / ``..`` are detected here rather than slipping past.

        Returns:
            ``True`` when the predicate holds, ``False`` otherwise.
        """
        path = unquote(urlsplit(url).path)
        return any(segment in {".", ".."} for segment in path.split("/"))

    async def _credentials(self, conn: Connection) -> dict[str, str]:
        """Fetch decrypted credentials, mapping retrieval failure to a domain error.

        Returns:
            Mapping from ``str`` to ``str``.

        Raises:
            ExternalApiCredentialError: If the related operation fails.
        """
        try:
            return await self._catalog.get_credentials(conn.name)
        except SecretRetrievalError as exc:
            logger.warning(
                EXTERNAL_API_EGRESS_BLOCKED,
                connection=conn.name,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                reason="credential_retrieval_failed",
            )
            msg = "Failed to broker credentials for connection"
            raise ExternalApiCredentialError(msg) from exc

    @staticmethod
    def _broker_headers(
        conn: Connection,
        credentials: dict[str, str],
    ) -> dict[str, str]:
        """Map credentials to auth headers (never logged).

        Returns:
            Mapping from ``str`` to ``str``.
        """
        return build_auth_headers(conn.auth_method, credentials)

    @staticmethod
    def _signable_headers(agent_headers: dict[str, str]) -> dict[str, str]:
        """Agent headers minus restricted ones that egress would strip.

        The approval signature must describe the request that is actually
        sent, so restricted headers (``Host`` / framing) -- which
        ``_merge_agent_headers`` drops before egress -- must not influence
        the signature either. Otherwise two calls differing only in a
        never-sent ``Host`` would sign differently and force a redundant
        re-approval.

        Returns:
            Mapping from ``str`` to ``str``.
        """
        return {
            k: v
            for k, v in agent_headers.items()
            if k.lower() not in _RESTRICTED_REQUEST_HEADERS
        }

    @classmethod
    def _merge_agent_headers(
        cls,
        agent_headers: dict[str, str],
        brokered_headers: dict[str, str],
    ) -> dict[str, str]:
        """Layer agent headers under brokered ones, case-insensitively.

        Agent-supplied headers are dropped when they are restricted
        (``Host`` / framing headers) or collide case-insensitively with a
        brokered header, so an agent can neither inject a forged ``Host``
        nor shadow a brokered credential with a differently-cased
        duplicate. Brokered headers always win.

        Returns:
            Mapping from ``str`` to ``str``.
        """
        brokered_keys = {k.lower() for k in brokered_headers}
        safe_agent_headers = {
            k: v
            for k, v in cls._signable_headers(agent_headers).items()
            if k.lower() not in brokered_keys
        }
        return {**safe_agent_headers, **brokered_headers}

    async def _egress(
        self,
        conn: Connection,
        request: ExternalAccessRequest,
    ) -> ToolExecutionResult:
        """Rate-limited egress with graceful rate-limit + transport-error handling.

        Returns:
            Result of type ``ToolExecutionResult``.
        """
        config = conn.rate_limiter or RateLimiterConfig(
            max_requests_per_minute=self._default_max_rpm,
        )
        rate_limited = with_connection_rate_limit(conn.name, config=config)(
            self._provider.request,
        )
        try:
            response = await rate_limited(request)
        except ConnectionRateLimitError as exc:
            logger.warning(
                EXTERNAL_API_RATE_LIMITED,
                connection=conn.name,
                error=safe_error_description(exc),
            )
            return ToolExecutionResult(
                content=(
                    f"Rate limit exceeded for connection {conn.name!r}; retry later."
                ),
                is_error=True,
                metadata={"rate_limited": True, "connection": conn.name},
            )
        except ExternalApiResponseError as exc:
            return ToolExecutionResult(content=str(exc), is_error=True)

        logger.info(
            EXTERNAL_API_CALL_SUCCEEDED,
            connection=conn.name,
            status_code=response.status_code,
            truncated=response.truncated,
        )
        return ToolExecutionResult(
            content=response.body,
            metadata={
                "status_code": response.status_code,
                "truncated": response.truncated,
                "connection": conn.name,
            },
        )

    async def _gate_approval(
        self,
        args: ExternalApiArgs,
        signature: ApprovalSignature,
    ) -> ToolExecutionResult | None:
        """Consume a matching approval, or park for one.

        Returns ``None`` to proceed (a matching grant was consumed), or a
        parking result when no approval exists yet. Raises
        ``ExternalApiApprovalMismatchError`` when an explicitly-referenced
        approval does not match this call, or the consume CAS loses a race.

        Returns:
            The resulting ``ToolExecutionResult``, or ``None`` when unavailable.

        Raises:
            ExternalApiApprovalMismatchError: If the related operation fails.
        """
        match = await self._find_matching_approval(args, signature)
        if match is None:
            return await self._park_for_approval(args, signature)
        consumed = await self._approval_store.consume_if_approved(match)
        if consumed is None:
            logger.warning(
                EXTERNAL_API_SIGNATURE_MISMATCH,
                connection=args.connection,
                approval_id=match,
                reason="already_consumed_or_race",
            )
            msg = "Approval was already used or is no longer valid"
            raise ExternalApiApprovalMismatchError(msg)
        logger.info(
            EXTERNAL_API_APPROVAL_CONSUMED,
            connection=args.connection,
            approval_id=match,
        )
        return None

    async def _find_matching_approval(
        self,
        args: ExternalApiArgs,
        signature: ApprovalSignature,
    ) -> str | None:
        """Find an APPROVED, unconsumed approval matching this call.

        With an explicit ``approval_id`` the match is strict: a missing,
        un-approved, consumed, or signature-mismatched item raises
        ``ExternalApiApprovalMismatchError`` (a deliberate replay/confusion
        signal) rather than silently minting another approval. Without one,
        a content-signature scan returns the id or ``None`` (park).

        Returns:
            The matching ``str``, or ``None`` when no match is found.

        Raises:
            ExternalApiApprovalMismatchError: If the related operation fails.
        """
        if args.approval_id is not None:
            item = await self._approval_store.get(args.approval_id)
            if (
                item is None
                or item.status is not ApprovalStatus.APPROVED
                or item.consumed_at is not None
                or not self._approval_bound_to_caller(item)
                or not signature.matches(
                    ApprovalSignature.from_metadata(item.metadata),
                )
            ):
                logger.warning(
                    EXTERNAL_API_SIGNATURE_MISMATCH,
                    connection=args.connection,
                    approval_id=args.approval_id,
                    reason="explicit_approval_no_match",
                )
                msg = "Supplied approval does not match this call or was already used"
                raise ExternalApiApprovalMismatchError(msg)
            return str(item.id)
        candidates = await self._approval_store.list_items(
            status=ApprovalStatus.APPROVED,
            action_type=_ACTION_TYPE,
        )
        for item in candidates:
            if (
                item.consumed_at is None
                and self._approval_bound_to_caller(item)
                and signature.matches(
                    ApprovalSignature.from_metadata(item.metadata),
                )
            ):
                return str(item.id)
        return None

    def _approval_bound_to_caller(self, item: ApprovalItem) -> bool:
        """Whether *item* was parked by this same agent and task.

        A content signature alone is not enough: two agents (or tasks)
        issuing the same call must not be able to consume each other's
        grant, and a leaked ``approval_id`` must not let an unrelated
        caller proceed. The grant is bound to the ``requested_by`` /
        ``task_id`` stamped at park time.

        Returns:
            ``True`` if the operation succeeds, ``False`` otherwise.
        """
        return item.requested_by == self._agent_id and item.task_id == self._task_id

    async def _park_for_approval(
        self,
        args: ExternalApiArgs,
        signature: ApprovalSignature,
    ) -> ToolExecutionResult:
        """Create a PENDING approval bound to this call and signal parking.

        Returns:
            Result of type ``ToolExecutionResult``.
        """
        approval_id = f"approval-{uuid4().hex}"
        risk_level = self._classify_risk()
        item = ApprovalItem(
            id=approval_id,
            action_type=_ACTION_TYPE,
            title=f"External API call to {args.connection!r}",
            description=(
                f"Agent requests a {args.method} call against connection"
                f" {args.connection!r}."
            ),
            requested_by=self._agent_id,
            risk_level=risk_level,
            source=ApprovalSource.PARKED_CONTEXT,
            created_at=self._clock.now().astimezone(UTC),
            task_id=self._task_id,
            metadata=signature.to_metadata(),
        )
        await self._approval_store.add(item)
        logger.info(
            EXTERNAL_API_APPROVAL_REQUIRED,
            connection=args.connection,
            approval_id=approval_id,
            risk_level=risk_level.value,
        )
        return ToolExecutionResult(
            content=(
                f"Approval required (id={approval_id}) for this external call."
                " Execution is paused until a human approves; on approval,"
                " re-issue the same call to proceed."
            ),
            metadata={
                "requires_parking": True,
                "approval_id": approval_id,
                "action_type": _ACTION_TYPE,
                "risk_level": risk_level.value,
            },
        )

    def _classify_risk(self) -> ApprovalRiskLevel:
        """Classify the call's risk, defaulting to HIGH when unavailable.

        Returns:
            Result of type ``ApprovalRiskLevel``.
        """
        if self._risk_classifier is None:
            return ApprovalRiskLevel.HIGH
        try:
            return self._risk_classifier.classify(_ACTION_TYPE)
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                EXTERNAL_API_RISK_CLASSIFY_FAILED,
                action_type=_ACTION_TYPE,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                note="risk classification failed; defaulting to HIGH",
            )
            return ApprovalRiskLevel.HIGH
