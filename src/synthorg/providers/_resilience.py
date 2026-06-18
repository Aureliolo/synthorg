# module-kind: code
"""Retry + rate-limit execution shell shared by every provider call.

These wrap a single provider attempt with the optional retry handler and
rate limiter that :class:`~synthorg.providers.base.BaseCompletionProvider`
configures. They carry no provider-specific logic, so they live as pure
functions the base class delegates to from ``complete`` / ``stream`` /
``get_model_capabilities``.
"""

from collections.abc import AsyncIterator, Callable, Coroutine

from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.provider import PROVIDER_STREAM_CLOSE_FAILED

from .errors import RateLimitError
from .resilience.rate_limiter import RateLimiter
from .resilience.retry import RetryHandler

logger = get_logger(__name__)


async def aclose_quietly(iterator: object, *, model: str) -> None:
    """Close *iterator* if it supports ``aclose``, swallowing non-critical errors.

    Cleanup-only helper for the cost-recording stream wrapper: the slot
    release already runs in :func:`rate_limited_call`'s own ``finally``,
    so a failure here must not mask the in-flight ``GeneratorExit`` (early
    consumer close) nor the natural stream-exhaustion path. Critical
    errors still propagate; everything else is logged.

    Args:
        iterator: The object whose ``aclose`` to drive when present.
        model: Model identifier for the close-failure log context.
    """
    inner_aclose = getattr(iterator, "aclose", None)
    if inner_aclose is None:
        return
    try:
        await inner_aclose()
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            PROVIDER_STREAM_CLOSE_FAILED,
            model=model,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )


async def resilient_execute[T](
    attempt_fn: Callable[[], Coroutine[object, object, T]],
    *,
    retry_handler: RetryHandler | None,
) -> T:
    """Execute *attempt_fn* with retry if a handler is configured.

    Args:
        attempt_fn: Zero-argument async callable for a single attempt.
        retry_handler: Optional retry handler; when ``None`` the attempt
            runs exactly once.

    Returns:
        The return value of *attempt_fn*.
    """
    if retry_handler is not None:
        retry_result = await retry_handler.execute(attempt_fn)
        return retry_result.value
    return await attempt_fn()


async def rate_limited_call[**P, T](
    rate_limiter: RateLimiter | None,
    func: Callable[P, Coroutine[object, object, T]],
    *args: P.args,
    **kwargs: P.kwargs,
) -> T:
    """Wrap a call with rate-limiter acquire/release.

    Holds the slot for the full stream lifetime. Pauses the limiter on
    ``RateLimitError`` with ``retry_after`` before re-raising.

    Args:
        rate_limiter: Optional limiter; when ``None`` the call is
            unthrottled.
        func: The async provider call to wrap.
        *args: Positional arguments forwarded to ``func``.
        **kwargs: Keyword arguments forwarded to ``func``.

    Returns:
        The return value of ``func``, or an async-iterator wrapper that
        holds the limiter slot until the stream is exhausted.

    Raises:
        RateLimitError: Re-raised after pausing the limiter with the
            provider's ``retry_after``.
    """
    acquired = False
    if rate_limiter is not None:
        await rate_limiter.acquire()
        acquired = True
    streaming_owns_release = False
    try:
        result = await func(*args, **kwargs)
        if acquired and isinstance(result, AsyncIterator):
            # Transfer slot ownership to a wrapper generator so the
            # concurrency slot is held until the stream is exhausted.
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
        if rate_limiter is not None and exc.retry_after is not None:
            rate_limiter.pause(exc.retry_after)
        raise
    else:
        return result
    finally:
        if acquired and not streaming_owns_release:
            rate_limiter.release()  # type: ignore[union-attr]
