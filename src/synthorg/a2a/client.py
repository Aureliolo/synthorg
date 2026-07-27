# module-kind: integration
"""Outbound A2A client for delegating to external agents.

Sends JSON-RPC 2.0 requests to external A2A-compatible agents,
pulling credentials from the connection catalog and validating
outbound URLs against SSRF rules.
"""

from typing import Final, override
from uuid import uuid4

import httpx
from pydantic import JsonValue

from synthorg.a2a._client_errors import A2AClientError, A2ATransientError
from synthorg.a2a._client_skills import SkillNegotiationMixin
from synthorg.a2a.models import (
    A2AMessage,
    A2ATask,
    JsonRpcRequest,
    JsonRpcResponse,
)
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.normalization import strip_trailing_slash
from synthorg.core.resilience.retry_after import (
    coerce_finite_nonneg_seconds,
    parse_retry_after_seconds,
)
from synthorg.integrations.connections.catalog import ConnectionCatalog
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.a2a import (
    A2A_OUTBOUND_FAILED,
    A2A_OUTBOUND_RESPONSE_INVALID,
    A2A_OUTBOUND_SENT,
    A2A_OUTBOUND_SSRF_BLOCKED,
)
from synthorg.tools.network_validator import NetworkPolicy

logger = get_logger(__name__)

_HTTP_TOO_MANY_REQUESTS: Final[int] = 429


class A2AClient(SkillNegotiationMixin):
    """Outbound JSON-RPC 2.0 client for A2A federation.

    Sends requests to external A2A agents, pulling credentials
    from the connection catalog and validating URLs against SSRF
    rules.

    Args:
        connection_catalog: Connection catalog for credential
            retrieval.
        timeout_seconds: HTTP request timeout in seconds.  Required
            kwarg -- callers must thread the value from
            :class:`synthorg.a2a.config.A2AConfig.client_timeout_seconds`
            so the constructor default cannot drift from the config
            default (pre-alpha: no hidden fallbacks).
        network_validator: SSRF validation (optional; when None,
            SSRF checks are skipped -- test-only).
        http_client: Optional injected httpx.AsyncClient for tests.
    """

    __slots__ = (
        "_catalog",
        "_http_client",
        "_network_validator",
        "_timeout",
    )

    def __init__(
        self,
        connection_catalog: ConnectionCatalog,
        *,
        timeout_seconds: float,
        network_validator: NetworkPolicy | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._catalog = connection_catalog
        self._network_validator = network_validator
        self._timeout = timeout_seconds
        self._http_client = http_client

    def set_timeout_seconds(self, value: float) -> None:
        """Update the per-request HTTP timeout (hot).

        Pushed in by ``A2AClientSettingsSubscriber`` so an operator change
        to ``a2a.client_timeout_seconds`` applies on the next request (both
        the persistent-client and SSRF-pinned paths read ``_timeout`` per
        call) without a restart.

        Raises:
            ValueError: If *value* is not positive.
        """
        if value <= 0:
            msg = f"timeout_seconds must be > 0, got {value}"
            raise ValueError(msg)
        self._timeout = value

    async def aclose(self) -> None:
        """Close the underlying HTTP client if present."""
        if self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None

    async def send_message(
        self,
        peer_name: str,
        message: A2AMessage,
    ) -> A2ATask:
        """Send a ``message/send`` request to an external peer.

        Args:
            peer_name: Connection name of the target peer.
            message: Validated A2A message to deliver; serialised to the
                JSON-RPC ``message`` param via ``model_dump(mode="json")``.

        Returns:
            A2A task from the peer's response.

        Raises:
            A2AClientError: On connection, auth, or peer errors.
        """
        return await self._call_method(
            peer_name,
            "message/send",
            {"message": message.model_dump(mode="json")},
        )

    async def get_task(
        self,
        peer_name: str,
        task_id: str,
    ) -> A2ATask:
        """Send a ``tasks/get`` request to an external peer.

        Args:
            peer_name: Connection name of the target peer.
            task_id: Remote task identifier.

        Returns:
            A2A task from the peer's response.

        Raises:
            A2AClientError: On connection or peer errors.
        """
        return await self._call_method(
            peer_name,
            "tasks/get",
            {"id": task_id},
        )

    async def cancel_task(
        self,
        peer_name: str,
        task_id: str,
    ) -> A2ATask:
        """Send a ``tasks/cancel`` request to an external peer.

        Args:
            peer_name: Connection name of the target peer.
            task_id: Remote task identifier.

        Returns:
            A2A task from the peer's response.

        Raises:
            A2AClientError: On connection or peer errors.
        """
        return await self._call_method(
            peer_name,
            "tasks/cancel",
            {"id": task_id},
        )

    @override
    async def _call_method_raw(
        self,
        peer_name: str,
        method: str,
        params: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        """Execute a JSON-RPC call to a peer and return its raw result.

        Shared transport for every outbound method: connection lookup,
        SSRF validation + IP pinning, auth-header injection, send, and
        JSON-RPC error mapping. The result payload is returned untyped so
        task methods (:meth:`_call_method`) and skill-negotiation methods
        (:meth:`query_skills` / :meth:`negotiate_skills`) can each parse it
        into their own response model.

        Args:
            peer_name: Connection name.
            method: JSON-RPC method name.
            params: Method parameters.

        Returns:
            The JSON-RPC ``result`` object (empty dict when the peer
            returned no result).

        Raises:
            A2AClientError: On any failure.
        """
        conn = await self._catalog.get(peer_name)
        if conn is None:
            msg = f"A2A peer connection '{peer_name}' not found"
            logger.warning(
                A2A_OUTBOUND_FAILED,
                peer_name=peer_name,
                reason="connection_not_found",
            )
            raise A2AClientError(msg, peer_name=peer_name)

        base_url = conn.base_url
        if not base_url:
            msg = f"A2A peer '{peer_name}' has no base_url"
            logger.warning(
                A2A_OUTBOUND_FAILED,
                peer_name=peer_name,
                reason="no_base_url",
            )
            raise A2AClientError(msg, peer_name=peer_name)

        # SSRF validation on outbound URL. ``validate_url_host`` resolves DNS
        # and returns an error string when the host is blocked, or a
        # ``DnsValidationOk`` carrying the resolved public IPs. Both the
        # block result and the resolved IPs are consumed: the request below
        # pins the connection to a validated IP so a rebind between this
        # check and the connect cannot redirect the call to a private
        # address (TOCTOU / DNS-rebinding).
        pinned_ip: str | None = None
        pinned_hostname: str | None = None
        if self._network_validator is not None:
            from synthorg.tools.network_validator import (  # noqa: PLC0415
                extract_hostname,
                validate_url_host,
            )

            url_str = str(base_url)
            hostname = extract_hostname(url_str)
            if hostname is None:
                logger.warning(
                    A2A_OUTBOUND_SSRF_BLOCKED,
                    peer_name=peer_name,
                    url=url_str,
                    reason="unparseable URL",
                )
                msg = f"SSRF: cannot parse URL for peer '{peer_name}'"
                raise A2AClientError(msg, peer_name=peer_name)
            try:
                validation = await validate_url_host(url_str, self._network_validator)
            except Exception as ssrf_exc:
                reraise_critical(ssrf_exc)
                logger.warning(
                    A2A_OUTBOUND_SSRF_BLOCKED,
                    peer_name=peer_name,
                    url=url_str,
                    error_type=type(ssrf_exc).__name__,
                    error=safe_error_description(ssrf_exc),
                )
                msg = f"SSRF: blocked outbound URL for peer '{peer_name}'"
                raise A2AClientError(
                    msg,
                    peer_name=peer_name,
                ) from ssrf_exc
            if isinstance(validation, str):
                logger.warning(
                    A2A_OUTBOUND_SSRF_BLOCKED,
                    peer_name=peer_name,
                    url=url_str,
                    reason=validation,
                )
                msg = f"SSRF: blocked outbound URL for peer '{peer_name}'"
                raise A2AClientError(msg, peer_name=peer_name)
            if validation.resolved_ips:
                pinned_ip = validation.resolved_ips[0]
                pinned_hostname = validation.hostname

        # Build JSON-RPC request
        request_id = str(uuid4())
        rpc_req = JsonRpcRequest(
            id=request_id,
            method=method,
            params=params,
        )

        # Pull credentials and inject auth headers per scheme.
        credentials = await self._catalog.get_credentials(peer_name)
        headers: dict[str, str] = {
            "Content-Type": "application/json",
        }
        auth_scheme = credentials.get("auth_scheme", "api_key")
        if auth_scheme == "bearer" or (
            auth_scheme == "oauth2" and "access_token" in credentials
        ):
            token = credentials.get("access_token", "")
            if token:
                headers["Authorization"] = f"Bearer {token}"
        elif auth_scheme == "api_key":
            api_key = credentials.get("api_key", "")
            header_name = credentials.get("header_name", "X-API-Key")
            if api_key:
                headers[header_name] = api_key
        # mTLS: no auth header needed -- rely on client certificates

        url = f"{strip_trailing_slash(str(base_url))}/api/v1/a2a"
        response = await self._send_request(
            url,
            rpc_req,
            headers,
            peer_name,
            method,
            pinned_ip=pinned_ip,
            pinned_hostname=pinned_hostname,
        )
        rpc_resp = _parse_rpc_response(response, peer_name)

        # Fail closed on a JSON-RPC id mismatch: a stale or mis-correlated
        # peer response must never be parsed as the current call's result.
        if rpc_resp.id != request_id:
            logger.warning(
                A2A_OUTBOUND_RESPONSE_INVALID,
                peer_name=peer_name,
                reason="rpc_id_mismatch",
                method=method,
            )
            msg = f"Peer '{peer_name}' returned a mismatched JSON-RPC id"
            raise A2AClientError(msg, peer_name=peer_name)

        if rpc_resp.error is not None:
            msg = (
                f"A2A peer '{peer_name}' returned error: "
                f"{rpc_resp.error.message} (code={rpc_resp.error.code})"
            )
            logger.warning(
                A2A_OUTBOUND_FAILED,
                peer_name=peer_name,
                reason="rpc_error",
                rpc_error_code=rpc_resp.error.code,
            )
            raise A2AClientError(msg, peer_name=peer_name)

        return rpc_resp.result or {}

    async def _call_method(
        self,
        peer_name: str,
        method: str,
        params: dict[str, JsonValue],
    ) -> A2ATask:
        """Execute a task-returning JSON-RPC call and parse the response.

        Args:
            peer_name: Connection name.
            method: JSON-RPC method name.
            params: Method parameters.

        Returns:
            Parsed A2A task from the response.

        Raises:
            A2AClientError: On any failure or a malformed task payload.
        """
        result = await self._call_method_raw(peer_name, method, params)
        # ``A2ATask`` supplies defaults for every field but ``id``, so a
        # truncated payload like ``{"id": "t-1"}`` would otherwise validate
        # into a synthetic SUBMITTED task. Require the lifecycle-bearing
        # fields explicitly; ``messages``/``metadata`` legitimately default
        # to empty for a freshly-submitted task, so they are not required.
        missing_fields = tuple(
            field for field in ("id", "state") if field not in result
        )
        if missing_fields:
            msg = (
                f"Peer '{peer_name}' returned malformed response "
                f"(missing task fields: {', '.join(missing_fields)})"
            )
            logger.warning(
                A2A_OUTBOUND_RESPONSE_INVALID,
                peer_name=peer_name,
                reason="missing_task_fields",
                missing_fields=missing_fields,
            )
            raise A2AClientError(msg, peer_name=peer_name)
        try:
            return A2ATask.model_validate(result)
        except Exception as exc:
            reraise_critical(exc)
            msg = f"Peer '{peer_name}' returned invalid task payload"
            logger.warning(
                A2A_OUTBOUND_RESPONSE_INVALID,
                peer_name=peer_name,
                reason="invalid_payload",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise A2AClientError(msg, peer_name=peer_name) from exc

    async def _send_request(
        self,
        url: str,
        rpc_req: JsonRpcRequest,
        headers: dict[str, str],
        peer_name: str,
        method: str,
        *,
        pinned_ip: str | None = None,
        pinned_hostname: str | None = None,
    ) -> httpx.Response:
        """Send HTTP request with differentiated error handling.

        Args:
            url: Target URL.
            rpc_req: JSON-RPC request to send.
            headers: HTTP headers.
            peer_name: Peer name for error context.
            method: RPC method for error context.
            pinned_ip: Validated IP to pin the connection to (TOCTOU
                mitigation); ``None`` skips pinning.
            pinned_hostname: Original hostname preserved for the TLS SNI /
                ``Host`` header when pinning.

        Returns:
            HTTP response.

        Raises:
            A2ATransientError: On a 429 or a connection / timeout error
                (retryable).
            A2AClientError: On any other HTTP failure.
        """
        try:
            response = await self._do_post(
                url,
                rpc_req,
                headers,
                pinned_ip=pinned_ip,
                pinned_hostname=pinned_hostname,
            )
            response.raise_for_status()
        except (httpx.NetworkError, httpx.TimeoutException) as exc:
            logger.warning(
                A2A_OUTBOUND_FAILED,
                peer_name=peer_name,
                method=method,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                transient=True,
            )
            msg = f"Connection to peer '{peer_name}' failed"
            raise A2ATransientError(msg, peer_name=peer_name) from exc
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status == _HTTP_TOO_MANY_REQUESTS:
                retry_after = coerce_finite_nonneg_seconds(
                    parse_retry_after_seconds(
                        exc.response.headers.get("Retry-After"),
                    ),
                )
                logger.warning(
                    A2A_OUTBOUND_FAILED,
                    peer_name=peer_name,
                    method=method,
                    status=status,
                    transient=True,
                    retry_after_seconds=retry_after,
                )
                msg = f"Peer '{peer_name}' rate-limited the request (429)"
                raise A2ATransientError(
                    msg,
                    peer_name=peer_name,
                    retry_after_seconds=retry_after,
                ) from exc
            logger.warning(
                A2A_OUTBOUND_FAILED,
                peer_name=peer_name,
                method=method,
                status=status,
            )
            msg = f"Peer '{peer_name}' returned {status}"
            raise A2AClientError(msg, peer_name=peer_name) from exc
        except httpx.HTTPError as exc:
            logger.warning(
                A2A_OUTBOUND_FAILED,
                peer_name=peer_name,
                method=method,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = f"Request to peer '{peer_name}' failed"
            raise A2AClientError(msg, peer_name=peer_name) from exc

        logger.info(
            A2A_OUTBOUND_SENT,
            peer_name=peer_name,
            method=method,
        )
        return response

    async def _do_post(
        self,
        url: str,
        rpc_req: JsonRpcRequest,
        headers: dict[str, str],
        *,
        pinned_ip: str | None = None,
        pinned_hostname: str | None = None,
    ) -> httpx.Response:
        """Execute the HTTP POST, reusing injected client.

        When ``pinned_ip`` / ``pinned_hostname`` are set (production SSRF
        path), the fresh client is wired with a :class:`PinnedDnsTransport`
        so the connection targets the exact IP the validator approved,
        closing the DNS-rebinding window. An injected test client is used
        as-is (tests stub the transport).

        Args:
            url: Target URL.
            rpc_req: JSON-RPC request to send.
            headers: HTTP headers.
            pinned_ip: Validated IP to pin to; ``None`` skips pinning.
            pinned_hostname: Hostname preserved for TLS SNI when pinning.

        Returns:
            HTTP response.
        """
        if self._http_client is not None:
            # Pass the timeout per request (not just the client's baked-in
            # default) so a hot ``set_timeout_seconds`` update applies to the
            # persistent-client path, matching the SSRF-pinned path below.
            return await self._http_client.post(
                url,
                json=rpc_req.model_dump(mode="json"),
                headers=headers,
                timeout=self._timeout,
            )
        transport: httpx.AsyncBaseTransport | None = None
        if pinned_ip is not None and pinned_hostname is not None:
            from synthorg.tools._dns_pinning import PinnedDnsTransport  # noqa: PLC0415

            transport = PinnedDnsTransport(hostname=pinned_hostname, ip=pinned_ip)
        # ``AsyncClient`` takes ownership of an injected transport and closes
        # it on context exit; a second explicit aclose() would double-close.
        async with httpx.AsyncClient(
            timeout=self._timeout, transport=transport
        ) as http:
            return await http.post(
                url,
                json=rpc_req.model_dump(mode="json"),
                headers=headers,
            )


def _parse_rpc_response(
    response: httpx.Response,
    peer_name: str,
) -> JsonRpcResponse:
    """Parse and validate a JSON-RPC response.

    Args:
        response: HTTP response from the peer.
        peer_name: Peer name for error context.

    Returns:
        Validated JSON-RPC response.

    Raises:
        A2AClientError: On parse or validation failure.
    """
    try:
        raw = response.json()
    except Exception as exc:
        reraise_critical(exc)
        logger.warning(
            A2A_OUTBOUND_FAILED,
            peer_name=peer_name,
            reason="response_json_decode_error",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        msg = f"Peer '{peer_name}' returned invalid JSON"
        raise A2AClientError(msg, peer_name=peer_name) from exc

    try:
        return JsonRpcResponse.model_validate(raw)
    except Exception as exc:
        reraise_critical(exc)
        logger.warning(
            A2A_OUTBOUND_FAILED,
            peer_name=peer_name,
            reason="response_validation_error",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        msg = f"Peer '{peer_name}' returned invalid JSON-RPC"
        raise A2AClientError(msg, peer_name=peer_name) from exc


__all__ = ["A2AClient", "A2AClientError", "A2ATransientError"]
