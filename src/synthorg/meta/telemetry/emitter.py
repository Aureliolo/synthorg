"""HTTP analytics emitter for cross-deployment telemetry.

Buffers anonymized events and flushes them in batches to the
configured collector endpoint. Flush triggers: batch size
threshold, time interval (periodic background task), or
explicit ``flush()``/``aclose()`` call. Retries on 5xx with
exponential backoff, drops on 4xx. 3xx redirects are treated
as failures (POST may not have been stored).
"""

import asyncio
from collections.abc import Collection
from types import TracebackType
from typing import Final, Self

import httpx

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.normalization import strip_trailing_slash
from synthorg.core.resilience import GeneralRetryHandler
from synthorg.meta.chief_of_staff.models import ProposalOutcome
from synthorg.meta.config import SelfImprovementConfig
from synthorg.meta.models import ImprovementProposal, RolloutResult
from synthorg.meta.telemetry.anonymizer import anonymize_decision, anonymize_rollout
from synthorg.meta.telemetry.config import CrossDeploymentAnalyticsConfig
from synthorg.meta.telemetry.models import AnonymizedOutcomeEvent, EventBatch
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.cross_deployment import (
    XDEPLOY_BATCH_DROPPED,
    XDEPLOY_BATCH_FLUSH_FAILED,
    XDEPLOY_BATCH_FLUSH_RETRYING,
    XDEPLOY_BATCH_FLUSHED,
    XDEPLOY_EMITTER_CLOSED,
    XDEPLOY_EMITTER_INITIALIZED,
    XDEPLOY_EVENT_EMIT_FAILED,
    XDEPLOY_EVENT_QUEUED,
    XDEPLOY_RESPONSE_BODY_UNREADABLE,
)

logger = get_logger(__name__)

_MAX_RETRIES: Final[int] = 3
_BACKOFF_BASE_SECONDS: Final[float] = 1.0
_BACKOFF_CAP_SECONDS: Final[float] = 30.0
_SUCCESS_MIN: Final[int] = 200
_SUCCESS_MAX: Final[int] = 300
_CLIENT_ERROR_MIN: Final[int] = 400
_SERVER_ERROR_MIN: Final[int] = 500
_LOG_BODY_MAX_LEN: Final[int] = 500


class _TransientPostError(
    Exception,
):  # lint-allow: domain-error-hierarchy -- internal retry sentinel
    """Internal sentinel: retryable HTTP failure (network exception, 3xx, 5xx).

    Carries either an HTTP status (3xx / 5xx response) or ``None``
    when the underlying ``httpx`` call raised a network-layer
    exception.  Wrapping into a single exception type lets
    :class:`GeneralRetryHandler` classify retries with one predicate
    while preserving distinct status logging in the call site.
    """

    def __init__(self, status: int | None, body: str = "") -> None:
        self.status = status
        self.body = body
        super().__init__(
            f"transient post failure: status={status} body={body!r}",
        )


class HttpAnalyticsEmitter:
    """Emits anonymized outcome events to a collector via HTTP POST.

    Events are buffered in memory and flushed when the batch size
    threshold is reached, the flush interval has elapsed, or
    ``flush()``/``aclose()`` is called explicitly. A background
    periodic task ensures buffered events are flushed even when
    no new events arrive.

    Supports the async-context-manager protocol for guaranteed
    cleanup::

        async with HttpAnalyticsEmitter(...) as emitter:
            await emitter.emit_decision(...)
        # ``aclose()`` runs on exit; the httpx client and any
        # buffered events are flushed.

    Lock invariants: ``_buffer`` and ``_last_flush_at`` are
    protected by ``_lock``. ``_analytics_config``,
    ``_builtin_rule_names``, and ``_client`` are immutable or
    thread-safe and require no lock.

    Args:
        analytics_config: Cross-deployment analytics configuration.
        self_improvement_config: Full self-improvement config.
        builtin_rule_names: Set of built-in rule names for
            anonymization classification.
    """

    def __init__(
        self,
        *,
        analytics_config: CrossDeploymentAnalyticsConfig,
        self_improvement_config: SelfImprovementConfig,
        builtin_rule_names: Collection[str],
        clock: Clock | None = None,
    ) -> None:
        self._analytics_config = analytics_config
        self._self_improvement_config = self_improvement_config
        self._builtin_rule_names = frozenset(builtin_rule_names)
        self._clock: Clock = clock if clock is not None else SystemClock()
        self._buffer: list[AnonymizedOutcomeEvent] = []
        self._lock = asyncio.Lock()
        # Dedicated lifecycle lock serialises flush-task creation
        # (``_ensure_flush_task``) and shutdown (``aclose``). Without
        # this, two concurrent first-emit producers could both pass
        # the ``_flush_task is None`` guard and spawn two
        # ``_periodic_flush`` tasks; only the last assigned to
        # ``self._flush_task`` would be cancelled by ``aclose``, and
        # the orphan would continue running and call ``flush`` /
        # ``self._client`` after the client had been closed.
        self._lifecycle_lock = asyncio.Lock()
        # Dedicated send lock serialises in-flight ``_send_batch`` calls
        # with ``aclose``'s ``self._client.aclose()``. Without it,
        # ``aclose`` can close the httpx client mid-POST: the public
        # ``flush()`` releases ``self._lock`` before calling
        # ``_send_batch`` (so concurrent emit_* keep enqueuing while
        # the network round-trip runs), which means there is no
        # mutual exclusion between an in-flight POST and a racing
        # ``aclose``. ``aclose`` acquires this lock around its
        # ``self._client.aclose()`` so any in-flight send finishes
        # against a live client; the close then proceeds.
        self._send_lock = asyncio.Lock()
        self._last_flush_at = self._clock.monotonic()
        self._closed = False
        self._flush_task: asyncio.Task[None] | None = None
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(analytics_config.http_timeout_seconds),
        )
        logger.info(
            XDEPLOY_EMITTER_INITIALIZED,
            collector_url=str(analytics_config.collector_url),
            batch_size=analytics_config.batch_size,
        )

    @property
    def pending_count(self) -> int:
        """Number of events buffered but not yet flushed.

        Note: reads ``_buffer`` without the lock for simplicity.
        Only intended for testing and diagnostics -- not for
        production control flow decisions.

        Returns:
            Resulting integer.
        """
        return len(self._buffer)

    async def emit_decision(
        self,
        outcome: ProposalOutcome,
        *,
        proposal: ImprovementProposal,  # noqa: ARG002
    ) -> None:
        """Anonymize and buffer a proposal decision event.

        Args:
            outcome: The proposal outcome to anonymize.
            proposal: The decided proposal (for context).
        """
        try:
            event = anonymize_decision(
                outcome,
                analytics_config=self._analytics_config,
                self_improvement_config=self._self_improvement_config,
                builtin_rule_names=self._builtin_rule_names,
            )
            await self._enqueue(event)
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                XDEPLOY_EVENT_EMIT_FAILED,
                event_type="proposal_decision",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

    async def emit_rollout(
        self,
        result: RolloutResult,
        *,
        proposal: ImprovementProposal,
    ) -> None:
        """Anonymize and buffer a rollout result event.

        Args:
            result: The rollout result to anonymize.
            proposal: The associated proposal (for context).
        """
        try:
            event = anonymize_rollout(
                result,
                proposal=proposal,
                analytics_config=self._analytics_config,
                self_improvement_config=self._self_improvement_config,
                builtin_rule_names=self._builtin_rule_names,
            )
            await self._enqueue(event)
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                XDEPLOY_EVENT_EMIT_FAILED,
                event_type="rollout_result",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

    async def flush(self) -> None:
        """Flush all buffered events to the collector.

        The buffer is cleared up-front so concurrent ``emit_*`` calls
        can keep enqueueing while the network round-trip runs. If the
        flush is cancelled mid-flight (e.g. by ``aclose()`` cancelling
        the periodic task) we re-stage the cleared batch onto the front
        of the buffer so the subsequent ``aclose().flush()`` retries it
        instead of silently dropping the batch.

        ``self._send_lock`` is held across ``_send_batch`` so a
        concurrent ``aclose`` cannot close ``self._client`` while a
        POST is mid-flight; ``aclose`` acquires the same lock around
        its ``self._client.aclose()`` call.

        Raises:
            CancelledError: Raised on the corresponding failure path.
        """
        async with self._lock:
            if not self._buffer:
                return
            batch = tuple(self._buffer)
            self._buffer.clear()
            self._last_flush_at = self._clock.monotonic()
        try:
            async with self._send_lock:
                await self._send_batch(batch)
        except asyncio.CancelledError:
            async with self._lock:
                # Prepend so chronological order is preserved relative
                # to any events appended while we were sending.
                self._buffer[:0] = batch
            raise

    async def aclose(self) -> None:
        """Flush remaining events and close the HTTP client.

        Cancellation order matters: ``self._closed = True`` is set
        BEFORE awaiting the flush task so the periodic loop's
        ``while not self._closed`` guard exits cleanly on its next
        iteration. We then ``cancel()`` to interrupt the in-progress
        ``asyncio.sleep`` (which is the only place the loop can be
        parked); a flush already in progress propagates its
        ``CancelledError`` to the awaiter, which we suppress
        explicitly. The explicit final ``flush()`` then sweeps any
        events appended between the cancellation request and this
        call.

        ``self._lifecycle_lock`` is held across the flag flip + task
        cancel + client close so a concurrent ``_enqueue`` cannot
        spawn a fresh ``_periodic_flush`` task in between -- otherwise
        the orphan would survive shutdown and call ``flush`` (which
        touches ``self._client``) after ``self._client.aclose()``.

        Resource-close ordering: the ``httpx.AsyncClient`` MUST close
        even if ``self._flush_task`` raised a non-CancelledError
        exception or ``self.flush()`` failed. A naive ``contextlib.
        suppress(CancelledError)`` around the await would let any
        other exception (e.g. an unexpected ``RuntimeError`` raised
        from the periodic loop) propagate before
        ``self._client.aclose()`` ran, leaving the httpx connection
        pool open for the rest of the process lifetime. We capture
        the first non-CancelledError exception, finish the close
        sequence, and re-raise at the end so shutdown is observable
        without leaking the client.

        Raises:
            CancelledError: Raised on the corresponding failure path.
        """
        async with self._lifecycle_lock:
            self._closed = True
            deferred_exc: BaseException | None = None
            if self._flush_task is not None:
                self._flush_task.cancel()
                try:
                    await self._flush_task
                except asyncio.CancelledError:
                    pass  # expected: we just cancelled it
                except Exception as exc:
                    reraise_critical(exc)
                    deferred_exc = exc
            try:
                await self.flush()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                reraise_critical(exc)
                if deferred_exc is None:
                    deferred_exc = exc
            # Hold ``_send_lock`` over ``_client.aclose()`` so any
            # in-flight ``_send_batch`` (which holds the same lock
            # while POSTing) finishes against a live client. The
            # final ``flush`` above already drained the buffer
            # serially, but a concurrent caller of the public
            # ``flush()`` could still be mid-POST when we get here.
            async with self._send_lock:
                await self._client.aclose()
            logger.info(XDEPLOY_EMITTER_CLOSED)
            if deferred_exc is not None:
                raise deferred_exc

    async def __aenter__(self) -> Self:
        """Enter the async context manager.

        Returns:
            ``Self`` instance.
        """
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        _tb: TracebackType | None,
    ) -> None:
        """Flush + close on context manager exit."""
        await self.aclose()

    async def _ensure_flush_task(self) -> None:
        """Start the periodic flush background task if not running.

        Holds ``self._lifecycle_lock`` so two concurrent first-emit
        producers can't both pass the ``is None / done()`` guard and
        spawn two background tasks (only one would be remembered on
        ``self._flush_task``; the other would orphan and survive
        ``aclose``). The lock is also acquired by ``aclose`` so a
        producer racing shutdown observes the closed flag set inside
        the same critical section.
        """
        async with self._lifecycle_lock:
            if self._closed:
                return
            if self._flush_task is None or self._flush_task.done():
                self._flush_task = asyncio.create_task(
                    self._periodic_flush(),
                )

    async def _periodic_flush(self) -> None:
        """Background loop that flushes on interval.

        Runs until ``aclose()`` sets ``_closed`` and cancels this
        task. The cancellation interrupts the sleep, so no
        post-sleep guard is needed.
        """
        while not self._closed:
            await asyncio.sleep(
                self._analytics_config.flush_interval_seconds,
            )
            await self.flush()

    async def _enqueue(self, event: AnonymizedOutcomeEvent) -> None:
        """Add event to buffer and maybe flush.

        Silently drops events after ``aclose()`` has been called. The
        ``_closed`` check is repeated INSIDE the buffer-mutation lock
        so a producer that passed the early guard cannot append after
        ``aclose()`` set the flag and drained the buffer; otherwise
        the post-shutdown event would be stranded forever.
        """
        if self._closed:
            return
        await self._ensure_flush_task()
        should_flush = False
        async with self._lock:
            if self._closed:
                # ``aclose()`` set the flag while we were awaiting the
                # lock or the flush-task helper above; the buffer may
                # already have been drained. Drop the event rather
                # than stranding it past shutdown.
                #
                # mypy ``[unreachable]`` on the ``return`` below:
                # mypy narrows ``self._closed`` to ``False`` after the
                # outer guard, but this is concurrent code; another
                # coroutine can flip the flag while we ``await
                # _ensure_flush_task()`` or the lock. The narrowing is
                # incorrect for async-mutated state.
                return  # type: ignore[unreachable]
            self._buffer.append(event)
            logger.debug(
                XDEPLOY_EVENT_QUEUED,
                event_type=event.event_type,
                pending=len(self._buffer),
            )
            if len(self._buffer) >= self._analytics_config.batch_size:
                should_flush = True
        if should_flush:
            await self.flush()

    async def _send_batch(
        self,
        events: tuple[AnonymizedOutcomeEvent, ...],
    ) -> None:
        """POST a batch of events to the collector with retry.

        Retries up to ``_MAX_RETRIES`` times on 3xx / 5xx responses
        and network-layer exceptions with exponential backoff.
        Drops the batch on 4xx (terminal client error).  Treats 3xx
        redirects as failures because the POST may not have been
        stored on the redirect target.

        Raises:
            ValueError: Raised on the corresponding failure path.
            _TransientPostError: Raised on the corresponding failure path.
        """
        if self._analytics_config.collector_url is None:
            msg = "collector_url is required when analytics is enabled"
            raise ValueError(msg)
        url = (
            strip_trailing_slash(str(self._analytics_config.collector_url)) + "/events"
        )
        payload = EventBatch(events=events).model_dump(mode="json")
        event_count = len(events)

        async def post_once() -> None:
            """One POST attempt; raises ``_TransientPostError`` on retry.

            Only ``httpx.HTTPError`` (and subclasses) is wrapped as
            transient -- programming errors (TypeError, AttributeError,
            etc.) propagate so real bugs surface instead of being
            retried as transient network failures.

            Raises:
                _TransientPostError: Raised on the corresponding failure path.
            """
            try:
                response = await self._client.post(
                    url,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                )
            except httpx.HTTPError as exc:
                raise _TransientPostError(None) from exc

            if _SUCCESS_MIN <= response.status_code < _SUCCESS_MAX:
                logger.info(
                    XDEPLOY_BATCH_FLUSHED,
                    event_count=event_count,
                    status=response.status_code,
                )
                return
            if _CLIENT_ERROR_MIN <= response.status_code < _SERVER_ERROR_MIN:
                logger.warning(
                    XDEPLOY_BATCH_DROPPED,
                    event_count=event_count,
                    status=response.status_code,
                    response_body=_safe_response_text(response),
                )
                return
            raise _TransientPostError(
                response.status_code,
                _safe_response_text(response),
            )

        retry = GeneralRetryHandler(
            retryable=lambda exc: isinstance(exc, _TransientPostError),
            max_attempts=_MAX_RETRIES + 1,
            base=_BACKOFF_BASE_SECONDS,
            cap=_BACKOFF_CAP_SECONDS,
            event=XDEPLOY_BATCH_FLUSH_RETRYING,
            jitter=False,
        )
        try:
            await retry.execute(post_once, event_count=event_count)
        except _TransientPostError as exc:
            logger.error(
                XDEPLOY_BATCH_FLUSH_FAILED,
                event_count=event_count,
                retries_exhausted=True,
                final_status=exc.status,
            )


def _safe_response_text(response: httpx.Response) -> str:
    """Safely extract response body text for logging.

    Returns:
        Resulting string.
    """
    try:
        text = response.text
    except Exception as exc:
        reraise_critical(exc)
        logger.warning(
            XDEPLOY_RESPONSE_BODY_UNREADABLE,
            status_code=response.status_code,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return "(unable to read response body)"
    if len(text) > _LOG_BODY_MAX_LEN:
        return text[: _LOG_BODY_MAX_LEN - 3] + "..."
    return text
