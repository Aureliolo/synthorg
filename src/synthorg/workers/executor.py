"""HTTP-callback task executor for the distributed worker pool.

The worker pool fetches :class:`TaskClaim` envelopes from JetStream;
this module turns each claim into a real task execution via the
backend's HTTP API. Workers never write to persistence directly
(single-writer invariant lives on the ``TaskEngine`` in the backend),
so the executor's only side effect is a typed HTTP call to
``POST /api/v1/tasks/{task_id}/execute``.

Outcome mapping:

- HTTP 2xx with a terminal task status (``completed`` / ``cancelled``
  / ``failed``) returns ``TaskClaimStatus.SUCCESS`` so JetStream acks.
- HTTP 2xx with a non-terminal status (the executor decided the task
  is not yet runnable) returns ``TaskClaimStatus.RETRY`` so JetStream
  nacks for a backoff.
- HTTP 409 (``ConflictError`` envelope) returns
  ``TaskClaimStatus.FAILED`` because the task moved out of an
  executable status between dispatch and execution; redelivery would
  not help. The terminal ack means the claim is removed.
- HTTP 404 returns ``TaskClaimStatus.FAILED`` for the same reason.
- HTTP 5xx, ``httpx.TransportError``, and ``httpx.TimeoutException``
  return ``TaskClaimStatus.RETRY`` so JetStream redelivers within the
  ``max_deliver`` budget.

Auth: the executor uses a Bearer token whose source is documented in
``docs/guides/workers-and-background-tasks.md``. The token is read
once at construction.
"""

from typing import Any, Final
from urllib.parse import quote

import httpx

from synthorg.core.clock import Clock
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.workers import (
    WORKERS_EXECUTOR_HTTP_FAILED,
    WORKERS_EXECUTOR_HTTP_INVOKED,
    WORKERS_EXECUTOR_HTTP_RETRY,
    WORKERS_EXECUTOR_HTTP_TERMINAL,
    WORKERS_EXECUTOR_INVALID_INIT_ARG,
)
from synthorg.workers.claim import TaskClaim, TaskClaimStatus

logger = get_logger(__name__)

DEFAULT_HTTP_TIMEOUT_SECONDS: Final[float] = 60.0
"""Per-request wall-clock timeout for ``POST /tasks/{id}/execute``.

Long enough to cover a single agent step (typical LLM call plus tool
invocation budget) without holding the JetStream ack-wait window
indefinitely. The worker's own ``ack_wait`` is the outer bound; this
inner timeout exists so a server hang surfaces as a retryable
transport error rather than a silent JetStream redelivery. Exposed
publicly so the worker entry point can mirror it on the shared
``httpx.AsyncClient`` baseline."""

_TERMINAL_STATUSES: Final[frozenset[str]] = frozenset(
    {"completed", "cancelled", "failed"}
)


class TaskExecutionExecutor:
    """HTTP-callback executor for :class:`Worker`.

    Args:
        api_base_url: Base URL of the SynthOrg backend (e.g.
            ``http://backend:3001``). Trailing slashes are stripped at
            construction so the executor's path concatenation cannot
            produce ``//``.
        auth_token: Bearer token used for every execute call. The
            backend's auth middleware validates it via the typed JWT
            boundary (``parse_typed("jwt", ...)``).
        http_client: Pre-constructed ``httpx.AsyncClient``. Injectable
            so tests can mount ``MockTransport`` without spinning a
            real backend.
        timeout_seconds: Per-request wall-clock budget. Defaults to
            :data:`DEFAULT_HTTP_TIMEOUT_SECONDS`.
        clock: Optional clock seam for the diagnostic log timestamps
            (not part of the HTTP envelope). Unused today; reserved so
            future retry/backoff logic stays test-deterministic.
    """

    __slots__ = (
        "_auth_token",
        "_base_url",
        "_clock",
        "_http",
        "_timeout_seconds",
    )

    def __init__(
        self,
        *,
        api_base_url: str,
        auth_token: str,
        http_client: httpx.AsyncClient,
        timeout_seconds: float = DEFAULT_HTTP_TIMEOUT_SECONDS,
        clock: Clock | None = None,
    ) -> None:
        if not api_base_url:
            msg = "api_base_url must be non-empty"
            logger.warning(
                WORKERS_EXECUTOR_INVALID_INIT_ARG,
                param="api_base_url",
                error=msg,
            )
            raise ValueError(msg)
        if not auth_token:
            msg = "auth_token must be non-empty"
            # Do NOT log the token value -- only the parameter name and
            # the canonical error message. The token is a Bearer
            # credential and would otherwise flow into structured logs.
            logger.warning(
                WORKERS_EXECUTOR_INVALID_INIT_ARG,
                param="auth_token",
                error=msg,
            )
            raise ValueError(msg)
        if timeout_seconds <= 0.0:
            msg = "timeout_seconds must be positive"
            logger.warning(
                WORKERS_EXECUTOR_INVALID_INIT_ARG,
                param="timeout_seconds",
                value=timeout_seconds,
                error=msg,
            )
            raise ValueError(msg)
        self._base_url = api_base_url.rstrip("/")
        self._auth_token = auth_token
        self._http = http_client
        self._timeout_seconds = timeout_seconds
        self._clock = clock

    async def __call__(self, claim: TaskClaim) -> TaskClaimStatus:
        """Execute the claim by calling the backend execute endpoint.

        Returns:
            The claim status mapped from the backend's HTTP response.
        """
        # URL-encode the task_id segment so reserved characters in the
        # claim identifier cannot produce a malformed path. ``safe=""``
        # forces slashes inside the id to be escaped too.
        encoded_task_id = quote(str(claim.task_id), safe="")
        url = f"{self._base_url}/api/v1/tasks/{encoded_task_id}/execute"
        logger.info(
            WORKERS_EXECUTOR_HTTP_INVOKED,
            task_id=claim.task_id,
            new_status=claim.new_status,
            url=url,
        )
        try:
            response = await self._http.post(
                url,
                headers={
                    "Authorization": f"Bearer {self._auth_token}",
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                json={
                    "previous_status": claim.previous_status,
                    "new_status": claim.new_status,
                    "idempotency_key": claim.idempotency_key,
                },
                timeout=self._timeout_seconds,
            )
        except httpx.TimeoutException as exc:
            logger.warning(
                WORKERS_EXECUTOR_HTTP_RETRY,
                task_id=claim.task_id,
                reason="timeout",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return TaskClaimStatus.RETRY
        except httpx.TransportError as exc:
            logger.warning(
                WORKERS_EXECUTOR_HTTP_RETRY,
                task_id=claim.task_id,
                reason="transport_error",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return TaskClaimStatus.RETRY

        return self._map_response(claim, response)

    def _map_response(
        self,
        claim: TaskClaim,
        response: httpx.Response,
    ) -> TaskClaimStatus:
        """Translate the HTTP response into a :class:`TaskClaimStatus`.

        The mapping is deterministic and documented in the module
        docstring so operators reading worker logs can predict
        ack/nack behaviour from the status alone.

        Returns:
            The ``TaskClaimStatus`` mapped from the response status code.
        """
        if response.status_code == httpx.codes.NOT_FOUND:
            logger.warning(
                WORKERS_EXECUTOR_HTTP_FAILED,
                task_id=claim.task_id,
                status_code=response.status_code,
                outcome="task_not_found",
            )
            return TaskClaimStatus.FAILED
        if response.status_code == httpx.codes.CONFLICT:
            logger.warning(
                WORKERS_EXECUTOR_HTTP_FAILED,
                task_id=claim.task_id,
                status_code=response.status_code,
                outcome="task_status_conflict",
            )
            return TaskClaimStatus.FAILED
        if response.status_code >= httpx.codes.INTERNAL_SERVER_ERROR:
            logger.warning(
                WORKERS_EXECUTOR_HTTP_RETRY,
                task_id=claim.task_id,
                status_code=response.status_code,
                reason="server_error",
            )
            return TaskClaimStatus.RETRY
        if not response.is_success:
            logger.warning(
                WORKERS_EXECUTOR_HTTP_FAILED,
                task_id=claim.task_id,
                status_code=response.status_code,
                outcome="non_retryable_4xx",
            )
            return TaskClaimStatus.FAILED

        payload = self._safe_json(response)
        terminal_status = self._extract_terminal_status(payload)
        if terminal_status is not None:
            logger.info(
                WORKERS_EXECUTOR_HTTP_TERMINAL,
                task_id=claim.task_id,
                terminal_status=terminal_status,
            )
            # All terminal task statuses (``completed`` / ``cancelled``
            # / ``failed``) map to SUCCESS so the JetStream claim is
            # acked. A task that finished in ``failed`` status is still
            # a successful execution from the worker's perspective; the
            # business-logic failure is recorded on the task itself,
            # not on the claim, so redelivery would not help.
            return TaskClaimStatus.SUCCESS
        # 2xx but no terminal status: the backend acknowledged the
        # request and may have advanced the task to an intermediate
        # state. Treat as retry so the next claim re-runs from the new
        # state, rather than acking now and losing the dispatch signal.
        logger.info(
            WORKERS_EXECUTOR_HTTP_RETRY,
            task_id=claim.task_id,
            reason="non_terminal_status",
        )
        return TaskClaimStatus.RETRY

    @staticmethod
    def _safe_json(response: httpx.Response) -> dict[str, Any]:
        """Return the JSON body, or an empty dict if it cannot be parsed.

        The execute endpoint always returns a typed envelope on
        success, but defensive parsing avoids crashing the worker on a
        misconfigured proxy that strips the body.
        """
        try:
            body = response.json()
        except ValueError, httpx.DecodingError:
            return {}
        if isinstance(body, dict):
            return body
        return {}

    @staticmethod
    def _extract_terminal_status(payload: dict[str, Any]) -> str | None:
        """Return the task's terminal status if the envelope reports one.

        The envelope shape is ``{"data": {"status": "<value>", ...}}``
        per ``ApiResponse[Task]``. The helper is liberal about the
        nested ``data`` key so a wrapper change does not break worker
        outcome mapping silently.
        """
        candidate = payload.get("data") if isinstance(payload, dict) else None
        if isinstance(candidate, dict):
            value = candidate.get("status")
        else:
            value = payload.get("status") if isinstance(payload, dict) else None
        if isinstance(value, str) and value in _TERMINAL_STATUSES:
            return value
        return None
