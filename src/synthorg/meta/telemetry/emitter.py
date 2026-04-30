"""HTTP analytics emitter for cross-deployment telemetry.

Buffers anonymized events and flushes them in batches to the
configured collector endpoint. Flush triggers: batch size
threshold, time interval (periodic background task), or
explicit ``flush()``/``aclose()`` call. Retries on 5xx with
exponential backoff, drops on 4xx. 3xx redirects are treated
as failures (POST may not have been stored).
"""

import asyncio
import contextlib
import time
from typing import TYPE_CHECKING, Self

import httpx

from synthorg.meta.telemetry.anonymizer import anonymize_decision, anonymize_rollout
from synthorg.meta.telemetry.models import AnonymizedOutcomeEvent, EventBatch
from synthorg.observability import get_logger
from synthorg.observability.events.cross_deployment import (
    XDEPLOY_BATCH_DROPPED,
    XDEPLOY_BATCH_FLUSH_FAILED,
    XDEPLOY_BATCH_FLUSH_RETRYING,
    XDEPLOY_BATCH_FLUSHED,
    XDEPLOY_EMITTER_CLOSED,
    XDEPLOY_EMITTER_INITIALIZED,
    XDEPLOY_EVENT_EMIT_FAILED,
    XDEPLOY_EVENT_QUEUED,
)

if TYPE_CHECKING:
    from collections.abc import Collection
    from types import TracebackType

    from synthorg.meta.chief_of_staff.models import ProposalOutcome
    from synthorg.meta.config import SelfImprovementConfig
    from synthorg.meta.models import ImprovementProposal, RolloutResult
    from synthorg.meta.telemetry.config import CrossDeploymentAnalyticsConfig

logger = get_logger(__name__)

_MAX_RETRIES = 3
_BACKOFF_BASE_SECONDS = 1.0
_SUCCESS_MIN = 200
_SUCCESS_MAX = 300
_CLIENT_ERROR_MIN = 400
_SERVER_ERROR_MIN = 500
_LOG_BODY_MAX_LEN = 500


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
    ) -> None:
        self._analytics_config = analytics_config
        self._self_improvement_config = self_improvement_config
        self._builtin_rule_names = frozenset(builtin_rule_names)
        self._buffer: list[AnonymizedOutcomeEvent] = []
        self._lock = asyncio.Lock()
        self._last_flush_at = time.monotonic()
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
        except MemoryError, RecursionError:
            raise
        except Exception:
            logger.exception(XDEPLOY_EVENT_EMIT_FAILED, event_type="proposal_decision")

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
        except MemoryError, RecursionError:
            raise
        except Exception:
            logger.exception(XDEPLOY_EVENT_EMIT_FAILED, event_type="rollout_result")

    async def flush(self) -> None:
        """Flush all buffered events to the collector.

        The buffer is cleared up-front so concurrent ``emit_*`` calls
        can keep enqueueing while the network round-trip runs. If the
        flush is cancelled mid-flight (e.g. by ``aclose()`` cancelling
        the periodic task) we re-stage the cleared batch onto the front
        of the buffer so the subsequent ``aclose().flush()`` retries it
        instead of silently dropping the batch.
        """
        async with self._lock:
            if not self._buffer:
                return
            batch = tuple(self._buffer)
            self._buffer.clear()
            self._last_flush_at = time.monotonic()
        try:
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
        ``CancelledError`` to the awaiter via ``suppress``. The
        explicit final ``flush()`` then sweeps any events appended
        between the cancellation request and this call.
        """
        self._closed = True
        if self._flush_task is not None:
            self._flush_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._flush_task
        await self.flush()
        await self._client.aclose()
        logger.info(XDEPLOY_EMITTER_CLOSED)

    async def __aenter__(self) -> Self:
        """Enter the async context manager."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Flush + close on context manager exit."""
        await self.aclose()

    async def _ensure_flush_task(self) -> None:
        """Start the periodic flush background task if not running."""
        if self._flush_task is None or self._flush_task.done():
            self._flush_task = asyncio.create_task(
                self._periodic_flush(),
            )

    async def _periodic_flush(self) -> None:
        """Background loop that flushes on interval.

        Runs until ``close()`` sets ``_closed`` and cancels this
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

        Silently drops events after ``close()`` has been called.
        """
        if self._closed:
            return
        await self._ensure_flush_task()
        should_flush = False
        async with self._lock:
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

        Retries up to ``_MAX_RETRIES`` times on 5xx responses
        with exponential backoff. Drops the batch on 4xx.
        Treats 3xx redirects as failures.
        """
        if self._analytics_config.collector_url is None:
            msg = "collector_url is required when analytics is enabled"
            raise ValueError(msg)
        url = str(self._analytics_config.collector_url).rstrip("/") + "/events"
        payload = EventBatch(events=events).model_dump(mode="json")

        for attempt in range(_MAX_RETRIES + 1):
            try:
                response = await self._client.post(
                    url,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                )
            except Exception:
                await self._handle_send_error(attempt, len(events))
                continue
            if self._handle_response(response, attempt, len(events)):
                return
            # 5xx: sleep before retry (delay logged by _handle_response).
            if attempt < _MAX_RETRIES:
                delay = _BACKOFF_BASE_SECONDS * (2**attempt)
                await asyncio.sleep(delay)

        logger.error(
            XDEPLOY_BATCH_FLUSH_FAILED,
            event_count=len(events),
            retries_exhausted=True,
        )

    def _handle_response(
        self,
        response: httpx.Response,
        attempt: int,
        event_count: int,
    ) -> bool:
        """Handle HTTP response. Returns True if processing is done."""
        if _SUCCESS_MIN <= response.status_code < _SUCCESS_MAX:
            logger.info(
                XDEPLOY_BATCH_FLUSHED,
                event_count=event_count,
                status=response.status_code,
            )
            return True
        if _CLIENT_ERROR_MIN <= response.status_code < _SERVER_ERROR_MIN:
            body = _safe_response_text(response)
            logger.warning(
                XDEPLOY_BATCH_DROPPED,
                event_count=event_count,
                status=response.status_code,
                response_body=body,
            )
            return True
        # 3xx redirects: treat as failure (POST may not be stored).
        # 5xx: will retry if attempts remain.
        if attempt < _MAX_RETRIES:
            logger.warning(
                XDEPLOY_BATCH_FLUSH_RETRYING,
                attempt=attempt + 1,
                status=response.status_code,
            )
        return False

    async def _handle_send_error(
        self,
        attempt: int,
        event_count: int,
    ) -> None:
        """Handle send exception with retry or final failure."""
        if attempt < _MAX_RETRIES:
            delay = _BACKOFF_BASE_SECONDS * (2**attempt)
            logger.warning(
                XDEPLOY_BATCH_FLUSH_RETRYING,
                attempt=attempt + 1,
                delay_seconds=delay,
            )
            await asyncio.sleep(delay)
        else:
            logger.exception(
                XDEPLOY_BATCH_FLUSH_FAILED,
                event_count=event_count,
            )


def _safe_response_text(response: httpx.Response) -> str:
    """Safely extract response body text for logging."""
    try:
        text = response.text
    except Exception:
        return "(unable to read response body)"
    if len(text) > _LOG_BODY_MAX_LEN:
        return text[: _LOG_BODY_MAX_LEN - 3] + "..."
    return text
