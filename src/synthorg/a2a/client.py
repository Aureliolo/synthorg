"""Outbound A2A client for delegating to external agents.

Sends JSON-RPC 2.0 requests to external A2A-compatible agents,
pulling credentials from the connection catalog and validating
outbound URLs against SSRF rules.
"""

from typing import Any, ClassVar
from uuid import uuid4

import httpx

from synthorg.a2a.models import (
    A2ATask,
    JsonRpcRequest,
    JsonRpcResponse,
)
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import DomainError
from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.a2a import (
    A2A_OUTBOUND_FAILED,
    A2A_OUTBOUND_RESPONSE_INVALID,
    A2A_OUTBOUND_SENT,
    A2A_OUTBOUND_SSRF_BLOCKED,
)

logger = get_logger(__name__)


class A2AClientError(DomainError):
    """Error raised by the outbound A2A client."""

    default_message: ClassVar[str] = "A2A client request failed"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.PROVIDER_ERROR
    error_code: ClassVar[ErrorCode] = ErrorCode.PROVIDER_ERROR
    status_code: ClassVar[int] = 502

    def __init__(self, message: str, *, peer_name: str = "") -> None:
        super().__init__(message)
        self.peer_name = peer_name


class A2AClient:
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
        connection_catalog: Any,
        *,
        timeout_seconds: float,
        network_validator: Any | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._catalog = connection_catalog
        self._network_validator = network_validator
        self._timeout = timeout_seconds
        self._http_client = http_client

    async def aclose(self) -> None:
        """Close the underlying HTTP client if present."""
        if self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None

    async def send_message(
        self,
        peer_name: str,
        message_params: dict[str, Any],
    ) -> A2ATask:
        """Send a ``message/send`` request to an external peer.

        Args:
            peer_name: Connection name of the target peer.
            message_params: JSON-RPC params for message/send.

        Returns:
            A2A task from the peer's response.

        Raises:
            A2AClientError: On connection, auth, or peer errors.
        """
        return await self._call_method(
            peer_name,
            "message/send",
            message_params,
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

    async def _call_method(
        self,
        peer_name: str,
        method: str,
        params: dict[str, Any],
    ) -> A2ATask:
        """Execute a JSON-RPC call to a peer.

        Args:
            peer_name: Connection name.
            method: JSON-RPC method name.
            params: Method parameters.

        Returns:
            Parsed A2A task from the response.

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

        # SSRF validation on outbound URL
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
                await validate_url_host(url_str, self._network_validator)
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

        # Build JSON-RPC request
        rpc_req = JsonRpcRequest(
            id=str(uuid4()),
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

        url = f"{str(base_url).rstrip('/')}/api/v1/a2a"
        response = await self._send_request(
            url,
            rpc_req,
            headers,
            peer_name,
            method,
        )
        rpc_resp = _parse_rpc_response(response, peer_name)

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

        result = rpc_resp.result
        if not result or "id" not in result:
            msg = f"Peer '{peer_name}' returned malformed response (missing task id)"
            logger.warning(
                A2A_OUTBOUND_RESPONSE_INVALID,
                peer_name=peer_name,
                reason="missing_task_id",
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
    ) -> httpx.Response:
        """Send HTTP request with differentiated error handling.

        Args:
            url: Target URL.
            rpc_req: JSON-RPC request to send.
            headers: HTTP headers.
            peer_name: Peer name for error context.
            method: RPC method for error context.

        Returns:
            HTTP response.

        Raises:
            A2AClientError: On any HTTP failure.
        """
        try:
            response = await self._do_post(
                url,
                rpc_req,
                headers,
            )
            response.raise_for_status()
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            logger.warning(
                A2A_OUTBOUND_FAILED,
                peer_name=peer_name,
                method=method,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                transient=True,
            )
            msg = f"Connection to peer '{peer_name}' failed"
            raise A2AClientError(msg, peer_name=peer_name) from exc
        except httpx.HTTPStatusError as exc:
            logger.warning(
                A2A_OUTBOUND_FAILED,
                peer_name=peer_name,
                method=method,
                status=exc.response.status_code,
            )
            msg = f"Peer '{peer_name}' returned {exc.response.status_code}"
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
    ) -> httpx.Response:
        """Execute the HTTP POST, reusing injected client.

        Args:
            url: Target URL.
            rpc_req: JSON-RPC request to send.
            headers: HTTP headers.

        Returns:
            HTTP response.
        """
        if self._http_client is not None:
            return await self._http_client.post(
                url,
                json=rpc_req.model_dump(),
                headers=headers,
            )
        async with httpx.AsyncClient(timeout=self._timeout) as http:
            return await http.post(
                url,
                json=rpc_req.model_dump(),
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
