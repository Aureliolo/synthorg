"""Abstract base class for completion providers.

Concrete adapters subclass ``BaseCompletionProvider`` and implement
the ``_do_*`` hooks.  The base class handles input validation,
automatic retry, rate limiting, and provides a cost-computation helper.
"""

import math
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Callable, Coroutine
from typing import Any, TypeVar

from synthorg.constants import BUDGET_ROUNDING_PRECISION
from synthorg.observability import get_logger
from synthorg.observability.events.provider import (
    PROVIDER_CALL_ERROR,
    PROVIDER_CALL_START,
    PROVIDER_CALL_SUCCESS,
    PROVIDER_STREAM_START,
)

from .capabilities import ModelCapabilities  # noqa: TC001
from .errors import InvalidRequestError, RateLimitError
from .models import (
    ChatMessage,
    CompletionConfig,
    CompletionResponse,
    StreamChunk,
    TokenUsage,
    ToolDefinition,
)
from .resilience.rate_limiter import RateLimiter  # noqa: TC001
from .resilience.retry import RetryHandler  # noqa: TC001

logger = get_logger(__name__)

_T = TypeVar("_T")


class BaseCompletionProvider(ABC):
    """Shared base for all completion provider adapters.

    Subclasses implement three hooks:

    * ``_do_complete`` — raw non-streaming call
    * ``_do_stream`` — raw streaming call
    * ``_do_get_model_capabilities`` — capability lookup

    The public methods validate inputs before delegating to hooks.
    When a ``retry_handler`` and/or ``rate_limiter`` are provided,
    calls are automatically wrapped with retry and rate-limiting logic.
    A static ``compute_cost`` helper is available for subclasses to
    build ``TokenUsage`` records from raw token counts.

    Args:
        retry_handler: Optional retry handler for transient errors.
        rate_limiter: Optional client-side rate limiter.
    """

    def __init__(
        self,
        *,
        retry_handler: RetryHandler | None = None,
        rate_limiter: RateLimiter | None = None,
    ) -> None:
        self._retry_handler = retry_handler
        self._rate_limiter = rate_limiter

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
        self._validate_messages(messages)
        self._validate_model(model)
        logger.debug(
            PROVIDER_CALL_START,
            model=model,
            message_count=len(messages),
        )

        async def _attempt() -> CompletionResponse:
            return await self._rate_limited_call(
                self._do_complete,
                messages,
                model,
                tools=tools,
                config=config,
            )

        try:
            result = await self._resilient_execute(_attempt)
        except Exception:
            logger.error(PROVIDER_CALL_ERROR, model=model, exc_info=True)
            raise
        logger.debug(
            PROVIDER_CALL_SUCCESS,
            model=model,
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
        self._validate_messages(messages)
        self._validate_model(model)
        logger.debug(
            PROVIDER_STREAM_START,
            model=model,
            message_count=len(messages),
        )

        async def _attempt() -> AsyncIterator[StreamChunk]:
            return await self._rate_limited_call(
                self._do_stream,
                messages,
                model,
                tools=tools,
                config=config,
            )

        try:
            return await self._resilient_execute(_attempt)
        except Exception:
            logger.error(PROVIDER_CALL_ERROR, model=model, exc_info=True)
            raise

    async def get_model_capabilities(self, model: str) -> ModelCapabilities:
        """Validate model identifier, delegate to ``_do_get_model_capabilities``.

        Args:
            model: Model identifier.

        Returns:
            Static capability and cost information.

        Raises:
            InvalidRequestError: If model is blank.
        """
        self._validate_model(model)
        return await self._do_get_model_capabilities(model)

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
        attempt_fn: Callable[[], Coroutine[Any, Any, _T]],
    ) -> _T:
        """Execute *attempt_fn* with retry if configured.

        Args:
            attempt_fn: Zero-argument async callable for a single attempt.

        Returns:
            The return value of *attempt_fn*.
        """
        if self._retry_handler is not None:
            return await self._retry_handler.execute(attempt_fn)
        return await attempt_fn()

    async def _rate_limited_call(
        self,
        func: Callable[..., Coroutine[Any, Any, _T]],
        *args: Any,
        **kwargs: Any,
    ) -> _T:
        """Wrap a call with rate limiter acquire/release.

        Holds the slot for the full stream lifetime. Pauses the limiter
        on ``RateLimitError`` with ``retry_after`` before re-raising.
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
                streaming_owns_release = True
                acquired = False

                async def _hold_slot_for_stream(
                    inner: AsyncIterator[Any],
                ) -> AsyncIterator[Any]:
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

    # -- Helpers ------------------------------------------------------

    @staticmethod
    def compute_cost(
        input_tokens: int,
        output_tokens: int,
        *,
        cost_per_1k_input: float,
        cost_per_1k_output: float,
    ) -> TokenUsage:
        """Build a ``TokenUsage`` from raw token counts and per-1k rates.

        Args:
            input_tokens: Number of input tokens (must be >= 0).
            output_tokens: Number of output tokens (must be >= 0).
            cost_per_1k_input: Cost per 1 000 input tokens in USD
                (finite and >= 0).
            cost_per_1k_output: Cost per 1 000 output tokens in USD
                (finite and >= 0).

        Returns:
            Populated ``TokenUsage`` with computed cost.

        Raises:
            InvalidRequestError: If any parameter is negative or
                non-finite.
        """
        if input_tokens < 0:
            msg = "input_tokens must be non-negative"
            raise InvalidRequestError(
                msg,
                context={"input_tokens": input_tokens},
            )
        if output_tokens < 0:
            msg = "output_tokens must be non-negative"
            raise InvalidRequestError(
                msg,
                context={"output_tokens": output_tokens},
            )
        if cost_per_1k_input < 0 or not math.isfinite(cost_per_1k_input):
            msg = "cost_per_1k_input must be a finite non-negative number"
            raise InvalidRequestError(
                msg,
                context={"cost_per_1k_input": cost_per_1k_input},
            )
        if cost_per_1k_output < 0 or not math.isfinite(cost_per_1k_output):
            msg = "cost_per_1k_output must be a finite non-negative number"
            raise InvalidRequestError(
                msg,
                context={"cost_per_1k_output": cost_per_1k_output},
            )
        cost = (input_tokens / 1000) * cost_per_1k_input + (
            output_tokens / 1000
        ) * cost_per_1k_output
        return TokenUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=round(cost, BUDGET_ROUNDING_PRECISION),
        )

    @staticmethod
    def _validate_messages(messages: list[ChatMessage]) -> None:
        """Reject empty message lists.

        Args:
            messages: Conversation messages.

        Raises:
            InvalidRequestError: If no messages are provided.
        """
        if not messages:
            msg = "messages must not be empty"
            logger.error(PROVIDER_CALL_ERROR, error="messages must not be empty")
            raise InvalidRequestError(msg, context={"field": "messages"})

    @staticmethod
    def _validate_model(model: str) -> None:
        """Reject blank, empty, or non-string model identifiers.

        Args:
            model: Model identifier string.

        Raises:
            InvalidRequestError: If model is not a string, empty,
                or whitespace-only.
        """
        if not isinstance(model, str) or not model.strip():
            msg = "model must be a non-blank string"
            logger.error(
                PROVIDER_CALL_ERROR,
                error="model must be a non-blank string",
                received_type=type(model).__name__,
            )
            raise InvalidRequestError(
                msg,
                context={
                    "field": "model",
                    "received_type": type(model).__name__,
                },
            )
