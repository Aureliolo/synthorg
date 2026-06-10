"""Abstract base class for completion providers.

Concrete adapters subclass ``BaseCompletionProvider`` and implement
the ``_do_*`` hooks.  The base class handles input validation,
automatic retry, rate limiting, and provides a cost-computation helper.
"""

import asyncio
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Callable, Coroutine, Mapping
from typing import Final, ParamSpec, TypeVar

from opentelemetry.trace import Status, StatusCode

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.events.provider import (
    PROVIDER_BATCH_CAPABILITIES_PARTIAL,
    PROVIDER_CALL_ERROR,
    PROVIDER_CALL_START,
    PROVIDER_CALL_SUCCESS,
    PROVIDER_STREAM_START,
)
from synthorg.observability.metrics_hub import record_provider_error
from synthorg.observability.tracing.instrumentation import get_tracer

from ._validation import validate_messages, validate_model
from .capabilities import ModelCapabilities
from .cost_recording import current_cost_context, emit_cost_record_from_context
from .errors import RateLimitError, classify_provider_error
from .models import (
    ChatMessage,
    CompletionConfig,
    CompletionResponse,
    StreamChunk,
    ToolDefinition,
)
from .resilience.errors import RetryExhaustedError
from .resilience.rate_limiter import RateLimiter
from .resilience.retry import RetryHandler

logger = get_logger(__name__)
_tracer = get_tracer(__name__)

_T = TypeVar("_T")
_P = ParamSpec("_P")

_MILLISECONDS_PER_SECOND: Final[float] = 1000.0


class BaseCompletionProvider(ABC):
    """Shared base for all completion provider adapters.

    Subclasses implement three hooks:

    * ``_do_complete`` -- raw non-streaming call
    * ``_do_stream`` -- raw streaming call
    * ``_do_get_model_capabilities`` -- capability lookup

    The public methods validate inputs before delegating to hooks.
    When a ``retry_handler`` and/or ``rate_limiter`` are provided,
    calls are automatically wrapped with retry and rate-limiting logic.
    Subclasses build ``TokenUsage`` records from raw token counts via
    ``compute_token_cost`` (``synthorg.providers._cost``).

    Args:
        retry_handler: Optional retry handler for transient errors.
        rate_limiter: Optional client-side rate limiter.
        clock: Optional injectable :class:`~synthorg.core.clock.Clock`
            used for latency measurement. Defaults to ``SystemClock``;
            tests inject ``FakeClock`` to drive virtual time without
            wall-clock waits.
    """

    def __init__(
        self,
        *,
        retry_handler: RetryHandler | None = None,
        rate_limiter: RateLimiter | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._retry_handler = retry_handler
        self._rate_limiter = rate_limiter
        self._clock: Clock = clock if clock is not None else SystemClock()

    def _provider_label(self) -> str:
        """Return the bounded provider identifier used for metrics / logs.

        Subclasses that carry a registry key typically expose it either
        as ``self.provider_name`` (assigned in ``__init__``) or
        ``self._provider_name``; if neither is populated we fall back
        to the concrete class name so the label is never empty when
        metrics fire before a driver has been fully constructed.
        Intentionally a method, not a property, so drivers that assign
        ``self.provider_name`` directly keep working without a setter.
        """
        for attr in ("provider_name", "_provider_name"):
            existing = getattr(self, attr, None)
            if isinstance(existing, str) and existing:
                return existing
        return type(self).__name__

    # -- Public API ---------------------------------------------------

    async def complete(
        self,
        messages: list[ChatMessage],
        model: str,
        *,
        tools: list[ToolDefinition] | None = None,
        config: CompletionConfig | None = None,
    ) -> CompletionResponse:
        """Validate inputs, delegate to ``_do_complete``.

        Applies rate limiting and retry automatically when configured.

        Args:
            messages: Conversation history.
            model: Model identifier to use.
            tools: Available tools for function calling.
            config: Optional completion parameters.

        Returns:
            The completion response.

        Raises:
            InvalidRequestError: If messages are empty or model is blank.
            RetryExhaustedError: If all retries are exhausted.
        """
        validate_messages(messages)
        validate_model(model)
        logger.debug(
            PROVIDER_CALL_START,
            model=model,
            message_count=len(messages),
        )

        async def _attempt() -> CompletionResponse:
            """Run one rate-limited ``_do_complete`` attempt for the retry handler.

            Returns:
                The driver's ``CompletionResponse`` for this attempt.
            """
            return await self._rate_limited_call(
                self._do_complete,
                messages,
                model,
                tools=tools,
                config=config,
            )

        from .resilience.retry import RetryResult  # noqa: PLC0415

        # Per-call child span under whatever parent span the caller
        # owns (typically ``agent.execution`` from AgentEngine).
        # ``record_exception=False`` and ``set_status_on_exception=False``
        # opt out of the auto-instrumentation that would otherwise
        # stamp the unscrubbed ``str(exc)`` into the span; we set
        # ``exception.message`` via ``safe_error_description`` instead
        # so attacker-controlled provider error strings are scrubbed
        # before reaching the OTLP exporter.
        provider_label = self._provider_label()
        span_attributes: dict[str, str | int] = {
            "provider.name": provider_label,
            "provider.model": model,
            "provider.message_count": len(messages),
            "provider.tool_count": len(tools) if tools else 0,
        }
        with _tracer.start_as_current_span(
            "provider.complete",
            attributes=span_attributes,
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            t_start = self._clock.monotonic()
            retry_info: RetryResult[CompletionResponse] | None = None
            try:
                if self._retry_handler is not None:
                    retry_info = await self._retry_handler.execute(_attempt)
                    result = retry_info.value
                else:
                    result = await _attempt()
            except Exception as exc:
                reraise_critical(exc)
                latency_ms = (
                    self._clock.monotonic() - t_start
                ) * _MILLISECONDS_PER_SECOND
                # ``logger.exception`` (what TRY400 suggests) would
                # attach a traceback whose serialized frame-locals can
                # leak provider credentials (API keys in headers,
                # connection URLs with user:pass). Use ``logger.error``
                # with the structured ``error_type`` + scrubbed
                # ``error`` fields instead.
                log_exception_redacted(
                    logger, PROVIDER_CALL_ERROR, exc, model=model, latency_ms=latency_ms
                )
                span.set_attribute("exception.type", type(exc).__name__)
                span.set_attribute(
                    "exception.message",
                    safe_error_description(exc),
                )
                span.set_attribute("provider.latency_ms", latency_ms)
                # ``set_status_on_exception=False`` opts out of the
                # auto-instrumentation that would have stamped an
                # un-scrubbed ``str(exc)`` into the span status, so the
                # ERROR status must be set manually here. The scrubbed
                # error description is exposed via ``exception.message``
                # above; ``Status.description`` is intentionally left
                # unset so the OTLP exporter never carries the raw
                # provider string.
                span.set_status(Status(StatusCode.ERROR))
                record_provider_error(
                    provider=provider_label,
                    model=model,
                    error_class=classify_provider_error(exc),
                )
                raise
            latency_ms = (self._clock.monotonic() - t_start) * _MILLISECONDS_PER_SECOND
            span.set_attribute("provider.latency_ms", latency_ms)
            if retry_info is not None:
                span.set_attribute(
                    "provider.retry_count",
                    max(0, retry_info.attempt_count - 1),
                )

        metadata: dict[str, object] = {"_synthorg_latency_ms": latency_ms}
        if retry_info is not None:
            metadata["_synthorg_retry_count"] = max(
                0,
                retry_info.attempt_count - 1,
            )
            if retry_info.retry_reason is not None:
                metadata["_synthorg_retry_reason"] = retry_info.retry_reason

        merged_metadata = dict(result.provider_metadata or {})
        merged_metadata.update(metadata)
        result = result.model_copy(update={"provider_metadata": merged_metadata})
        logger.debug(
            PROVIDER_CALL_SUCCESS,
            model=model,
        )

        # Cost recording chokepoint: when a ``cost_recording_scope`` is
        # open in the current asyncio task, emit a CostRecord. Sites
        # without a scope (probes, tests, and the engine path which
        # records via ``record_execution_costs`` post-execution) see no
        # change. Recording errors are logged and swallowed inside the
        # helper -- never surface to the caller.
        ctx = current_cost_context()
        if ctx is not None:
            await emit_cost_record_from_context(
                ctx,
                result,
                model=model,
                provider=self._provider_label(),
            )
        return result

    async def stream(
        self,
        messages: list[ChatMessage],
        model: str,
        *,
        tools: list[ToolDefinition] | None = None,
        config: CompletionConfig | None = None,
    ) -> AsyncIterator[StreamChunk]:
        """Validate inputs, delegate to ``_do_stream``.

        Only the initial connection setup is retried; mid-stream errors
        are not retried.

        .. note::

            Unlike :meth:`complete`, ``stream`` does **not** fire the
            cost-recording chokepoint.  Streaming responses surface
            usage as a terminal ``StreamEventType.USAGE`` chunk, so the
            recording logic would have to consume the iterator to
            extract token counts -- conflating cost recording with the
            stream-consumption contract.  Until streaming becomes a
            mainstream LLM call path in this codebase, callers using
            ``stream()`` are responsible for emitting their own
            ``CostRecord`` from the final usage chunk.  No call site in
            the current diff uses ``stream()`` for paid LLM work.

        Args:
            messages: Conversation history.
            model: Model identifier to use.
            tools: Available tools for function calling.
            config: Optional completion parameters.

        Returns:
            Async iterator of stream chunks.

        Raises:
            InvalidRequestError: If messages are empty or model is blank.
            RetryExhaustedError: If all retries are exhausted.
        """
        validate_messages(messages)
        validate_model(model)
        logger.debug(
            PROVIDER_STREAM_START,
            model=model,
            message_count=len(messages),
        )

        async def _attempt() -> AsyncIterator[StreamChunk]:
            """Run one rate-limited ``_do_stream`` attempt for the retry handler.

            Returns:
                The driver's ``StreamChunk`` async iterator for this attempt.
            """
            return await self._rate_limited_call(
                self._do_stream,
                messages,
                model,
                tools=tools,
                config=config,
            )

        try:
            return await self._resilient_execute(_attempt)
        except Exception as exc:
            reraise_critical(exc)
            # See the ``complete`` sibling handler; ``logger.error``
            # + scrubbed fields instead of ``logger.exception``
            # prevents traceback frame-locals from leaking provider
            # credentials.
            log_exception_redacted(logger, PROVIDER_CALL_ERROR, exc, model=model)
            record_provider_error(
                provider=self._provider_label(),
                model=model,
                error_class=classify_provider_error(exc),
            )
            raise

    async def get_model_capabilities(self, model: str) -> ModelCapabilities:
        """Validate model identifier, delegate to ``_do_get_model_capabilities``.

        Capability lookups go through the same retry handler and rate
        limiter as ``complete()`` / ``stream()`` so the contract "all
        provider calls go through BaseCompletionProvider" stays honest
        for any future driver whose ``_do_get_model_capabilities``
        does network I/O.  Same budget as completions: capability
        lookups consume a rate-limiter slot and are retried on
        retryable errors.

        Args:
            model: Model identifier.

        Returns:
            Static capability and cost information.

        Raises:
            InvalidRequestError: If model is blank.
            RetryExhaustedError: If all retries are exhausted.
        """
        validate_model(model)

        async def _attempt() -> ModelCapabilities:
            """Run one rate-limited capability lookup for the retry handler.

            Returns:
                The driver's ``ModelCapabilities`` for this attempt.
            """
            return await self._rate_limited_call(
                self._do_get_model_capabilities,
                model,
            )

        try:
            return await self._resilient_execute(_attempt)
        except Exception as exc:
            reraise_critical(exc)
            # ``logger.exception`` would attach a traceback whose
            # frame-locals can leak provider credentials; use
            # ``logger.error`` with the structured ``error_type`` +
            # scrubbed ``error`` fields, mirroring ``complete()`` /
            # ``stream()``. ``record_provider_error`` keeps the
            # provider-error metric in sync with the other call paths
            # so dashboards do not under-count capability failures.
            log_exception_redacted(
                logger,
                PROVIDER_CALL_ERROR,
                exc,
                model=model,
                phase="get_model_capabilities",
            )
            record_provider_error(
                provider=self._provider_label(),
                model=model,
                error_class=classify_provider_error(exc),
            )
            raise

    async def batch_get_capabilities(
        self,
        models: tuple[str, ...],
    ) -> Mapping[str, ModelCapabilities | None]:
        """Fan out capability lookups across many models in parallel.

        The default implementation runs ``get_model_capabilities`` per
        model concurrently via :class:`asyncio.TaskGroup`.

        Per-model classification errors (model-not-found, validation,
        non-retryable provider errors) degrade to ``None`` entries so a
        single bad model id does not poison the whole batch.

        ``RetryExhaustedError`` propagates: retry exhaustion is a
        signal that the provider is unhealthy, not a per-model
        classification issue. Surfacing it lets the caller decide
        whether to fail the whole list-models request or retry the
        batch later. ``MemoryError`` and ``RecursionError`` also
        propagate unchanged.

        Subclasses that expose a cheaper bulk source (e.g. a static
        preset catalog) should override this to avoid the per-model
        round trip.

        Returns:
            A mapping of model identifier to its ``ModelCapabilities``,
            with ``None`` for each model whose individual lookup failed
            with a per-model error.
        """
        if not models:
            return {}

        async def _one(m: str) -> tuple[str, ModelCapabilities | None]:
            """Resolve one model's capabilities, degrading errors to ``None``.

            Returns:
                A ``(model, capabilities)`` pair; the capabilities are
                ``None`` when the per-model lookup failed.

            Raises:
                MemoryError: Propagated so the task group can abort.
                RecursionError: Propagated so the task group can abort.
                RetryExhaustedError: Propagated when retries are exhausted
                    rather than degraded to ``None``.
            """
            try:
                return m, await self.get_model_capabilities(m)
            except MemoryError, RecursionError, RetryExhaustedError:
                raise
            except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                reraise_critical(exc)
                logger.warning(
                    PROVIDER_BATCH_CAPABILITIES_PARTIAL,
                    model=m,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                return m, None

        async with asyncio.TaskGroup() as tg:
            tasks = [tg.create_task(_one(m)) for m in models]
        return dict(t.result() for t in tasks)

    # -- Hooks (subclasses implement) ---------------------------------

    @abstractmethod
    async def _do_complete(
        self,
        messages: list[ChatMessage],
        model: str,
        *,
        tools: list[ToolDefinition] | None = None,
        config: CompletionConfig | None = None,
    ) -> CompletionResponse:
        """Provider-specific non-streaming completion.

        Subclasses **must** catch all provider-specific exceptions and
        re-raise them as appropriate ``ProviderError`` subclasses.
        Exceptions that escape without wrapping will bypass the error
        hierarchy.

        Args:
            messages: Conversation history.
            model: Model identifier to use.
            tools: Available tools for function calling.
            config: Optional completion parameters.

        Raises:
            ProviderError: All errors must use the provider error hierarchy.
        """
        ...

    @abstractmethod
    async def _do_stream(
        self,
        messages: list[ChatMessage],
        model: str,
        *,
        tools: list[ToolDefinition] | None = None,
        config: CompletionConfig | None = None,
    ) -> AsyncIterator[StreamChunk]:
        r"""Provider-specific streaming completion.

        Implementations must *return* an ``AsyncIterator`` (not ``yield``
        directly), since the caller ``await``\s this coroutine to obtain
        the iterator.

        Subclasses **must** catch all provider-specific exceptions and
        re-raise them as appropriate ``ProviderError`` subclasses.

        Args:
            messages: Conversation history.
            model: Model identifier to use.
            tools: Available tools for function calling.
            config: Optional completion parameters.

        Raises:
            ProviderError: All errors must use the provider error hierarchy.
        """
        ...

    @abstractmethod
    async def _do_get_model_capabilities(
        self,
        model: str,
    ) -> ModelCapabilities:
        """Provider-specific capability lookup.

        Args:
            model: Model identifier.

        Raises:
            ProviderError: All errors must use the provider error hierarchy.
        """
        ...

    # -- Resilience helpers -------------------------------------------

    async def _resilient_execute(
        self,
        attempt_fn: Callable[[], Coroutine[object, object, _T]],
    ) -> _T:
        """Execute *attempt_fn* with retry if configured.

        Args:
            attempt_fn: Zero-argument async callable for a single attempt.

        Returns:
            The return value of *attempt_fn*.
        """
        if self._retry_handler is not None:
            retry_result = await self._retry_handler.execute(attempt_fn)
            return retry_result.value
        return await attempt_fn()

    async def _rate_limited_call(
        self,
        func: Callable[_P, Coroutine[object, object, _T]],
        *args: _P.args,
        **kwargs: _P.kwargs,
    ) -> _T:
        """Wrap a call with rate limiter acquire/release.

        Holds the slot for the full stream lifetime. Pauses the limiter
        on ``RateLimitError`` with ``retry_after`` before re-raising.

        Returns:
            The return value of ``func``, or an async-iterator wrapper
            that holds the limiter slot until the stream is exhausted.

        Raises:
            RateLimitError: Re-raised after pausing the limiter with the
                provider's ``retry_after``.
        """
        acquired = False
        if self._rate_limiter is not None:
            await self._rate_limiter.acquire()
            acquired = True
        streaming_owns_release = False
        try:
            result = await func(*args, **kwargs)
            if acquired and isinstance(result, AsyncIterator):
                # Transfer slot ownership to a wrapper generator so the
                # concurrency slot is held until the stream is exhausted.
                rate_limiter = self._rate_limiter
                streaming_owns_release, acquired = True, False

                async def _hold_slot_for_stream(
                    inner: AsyncIterator[object],
                ) -> AsyncIterator[object]:
                    """Re-yield the inner stream, releasing the slot when exhausted."""
                    try:
                        async for chunk in inner:
                            yield chunk
                    finally:
                        rate_limiter.release()  # type: ignore[union-attr]

                return _hold_slot_for_stream(result)  # type: ignore[return-value]
        except RateLimitError as exc:
            if self._rate_limiter is not None and exc.retry_after is not None:
                self._rate_limiter.pause(exc.retry_after)
            raise
        else:
            return result
        finally:
            if acquired and not streaming_owns_release:
                self._rate_limiter.release()  # type: ignore[union-attr]
