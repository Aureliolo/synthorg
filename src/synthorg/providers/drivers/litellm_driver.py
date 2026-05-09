"""LiteLLM-backed completion driver.

Wraps ``litellm.acompletion`` behind the ``BaseCompletionProvider``
contract, mapping between domain models and LiteLLM's chat-completion
API.
"""

from collections.abc import (
    Mapping,  # noqa: TC003  # runtime annotation on driver method
)
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

import litellm as _litellm
from litellm.exceptions import (
    APIConnectionError as LiteLLMConnectionError,
)
from litellm.exceptions import (
    AuthenticationError as LiteLLMAuthError,
)
from litellm.exceptions import (
    BadRequestError as LiteLLMBadRequest,
)
from litellm.exceptions import (
    ContentPolicyViolationError as LiteLLMContentPolicy,
)
from litellm.exceptions import (
    ContextWindowExceededError as LiteLLMContextWindow,
)
from litellm.exceptions import (
    InternalServerError as LiteLLMInternalError,
)
from litellm.exceptions import (
    NotFoundError as LiteLLMNotFound,
)
from litellm.exceptions import (
    RateLimitError as LiteLLMRateLimit,
)
from litellm.exceptions import (
    ServiceUnavailableError as LiteLLMUnavailable,
)
from litellm.exceptions import (
    Timeout as LiteLLMTimeout,
)

from synthorg.core.clock import Clock, SystemClock
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.provider import (
    PROVIDER_AUTH_ERROR,
    PROVIDER_BATCH_CAPABILITIES_PARTIAL,
    PROVIDER_CALL_ERROR,
    PROVIDER_CONNECTION_ERROR,
    PROVIDER_MODEL_INFO_UNAVAILABLE,
    PROVIDER_MODEL_INFO_UNEXPECTED_ERROR,
    PROVIDER_MODEL_NOT_FOUND,
    PROVIDER_RATE_LIMITED,
    PROVIDER_RETRY_AFTER_PARSE_FAILED,
    PROVIDER_STREAM_CHUNK_NO_DELTA,
    PROVIDER_STREAM_DONE,
)
from synthorg.providers import errors
from synthorg.providers.base import BaseCompletionProvider
from synthorg.providers.capabilities import ModelCapabilities
from synthorg.providers.drivers.litellm_tool_accumulator import (
    _ToolCallAccumulator,
    accumulate_tool_call_deltas,
    emit_pending_tool_calls,
)
from synthorg.providers.enums import AuthType, StreamEventType
from synthorg.providers.models import (
    CompletionResponse,
    StreamChunk,
)
from synthorg.providers.resilience.rate_limiter import RateLimiter
from synthorg.providers.resilience.retry import RetryHandler

from .mappers import (
    extract_tool_calls,
    map_finish_reason,
    messages_to_dicts,
    tools_to_dicts,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator

    from synthorg.config.schema import ProviderConfig, ProviderModelConfig
    from synthorg.providers.models import (
        ChatMessage,
        CompletionConfig,
        ToolDefinition,
    )

logger = get_logger(__name__)

_CREDENTIAL_CACHE_TTL = 300.0
"""Cached credentials from the connection catalog are refreshed at
most every ``_CREDENTIAL_CACHE_TTL`` seconds. Prevents pinning stale
OAuth/rotating tokens for the lifetime of the driver."""

# ── Exception mapping table ──────────────────────────────────────

_EXCEPTION_TABLE: tuple[tuple[type[Exception], type[errors.ProviderError]], ...] = (
    (LiteLLMAuthError, errors.AuthenticationError),
    (LiteLLMRateLimit, errors.RateLimitError),
    (LiteLLMNotFound, errors.ModelNotFoundError),
    (LiteLLMContextWindow, errors.InvalidRequestError),
    (LiteLLMContentPolicy, errors.ContentFilterError),
    (LiteLLMBadRequest, errors.InvalidRequestError),
    (LiteLLMTimeout, errors.ProviderTimeoutError),
    (LiteLLMUnavailable, errors.ProviderInternalError),
    (LiteLLMInternalError, errors.ProviderInternalError),
    (LiteLLMConnectionError, errors.ProviderConnectionError),
)


class LiteLLMDriver(BaseCompletionProvider):
    """Completion driver backed by LiteLLM.

    Uses ``litellm.acompletion`` for both streaming and non-streaming
    calls.  Model identifiers are prefixed with the LiteLLM routing key
    (``litellm_provider`` if set, otherwise the provider name -- e.g.
    ``example-provider/example-medium-001``) so LiteLLM routes to the
    correct backend.

    Args:
        provider_name: Provider key from config (e.g. ``"example-provider"``).
        config: Provider configuration including API key, base URL,
            and model definitions.

    Raises:
        ProviderError: All LiteLLM exceptions are mapped to the
            ``ProviderError`` hierarchy via ``_map_exception``.
    """

    def __init__(
        self,
        provider_name: str,
        config: ProviderConfig,
        *,
        connection_catalog: Any | None = None,
        clock: Clock | None = None,
    ) -> None:
        retry_handler = (
            RetryHandler(config.retry) if config.retry.max_retries > 0 else None
        )
        rate_limiter = RateLimiter(
            config.rate_limiter,
            provider_name=provider_name,
        )
        super().__init__(
            retry_handler=retry_handler,
            rate_limiter=rate_limiter if rate_limiter.is_enabled else None,
        )
        self._provider_name = provider_name
        self._config = config
        self._connection_catalog = connection_catalog
        self._clock: Clock = clock if clock is not None else SystemClock()
        self._resolved_credentials: dict[str, str] | None = None
        # Cached credentials expire after ``_CREDENTIAL_CACHE_TTL`` so
        # rotating/OAuth tokens are re-fetched from the catalog
        # periodically instead of being pinned for the lifetime of
        # the driver. The TTL is intentionally coarse -- it is a safety
        # net for rotation, not a token-refresh mechanism.
        self._credentials_cached_at: float | None = None
        self._model_lookup: MappingProxyType[str, ProviderModelConfig] = (
            MappingProxyType(self._build_model_lookup(config.models))
        )
        self._routing_key = config.litellm_provider or provider_name

    async def _ensure_credentials_resolved(self) -> None:
        """Resolve credentials from ConnectionCatalog if needed.

        Caches the result on the driver instance with a bounded TTL
        so rotating/OAuth tokens are picked up on a subsequent call
        rather than pinned forever.
        """
        if self._config.connection_name is None or self._connection_catalog is None:
            return
        from synthorg.observability.events.integrations import (  # noqa: PLC0415
            PROVIDER_CONNECTION_RESOLVED,
        )

        now = self._clock.monotonic()
        # Never serve cached OAuth credentials -- the token manager
        # can rotate them at any moment and a stale bearer token
        # would just fail auth on the next request with no way for
        # the driver to recover. Always go back to the catalog and
        # pick up the current access token.
        if self._config.auth_type is not AuthType.OAUTH and (
            self._resolved_credentials is not None
            and self._credentials_cached_at is not None
            and (now - self._credentials_cached_at) < _CREDENTIAL_CACHE_TTL
        ):
            return

        creds = await self._connection_catalog.get_credentials(
            self._config.connection_name,
        )
        # Snapshot so the caller's view is insulated from any
        # subsequent mutation in the catalog layer.
        self._resolved_credentials = dict(creds)
        self._credentials_cached_at = now
        logger.info(
            PROVIDER_CONNECTION_RESOLVED,
            provider=self._provider_name,
            connection_name=self._config.connection_name,
        )

    async def _do_complete(
        self,
        messages: list[ChatMessage],
        model: str,
        *,
        tools: list[ToolDefinition] | None = None,
        config: CompletionConfig | None = None,
    ) -> CompletionResponse:
        """Call ``litellm.acompletion`` and map the response."""
        try:
            await self._ensure_credentials_resolved()
            model_config = self._resolve_model(model)
            litellm_model = f"{self._routing_key}/{model_config.id}"
            kwargs = self._build_kwargs(
                messages,
                litellm_model,
                tools=tools,
                config=config,
            )
            response = await _litellm.acompletion(**kwargs)
        except errors.ProviderError:
            raise
        except Exception as exc:
            raise self._map_exception(exc, model) from exc
        return self._map_response(response, model_config)

    async def _do_stream(
        self,
        messages: list[ChatMessage],
        model: str,
        *,
        tools: list[ToolDefinition] | None = None,
        config: CompletionConfig | None = None,
    ) -> AsyncIterator[StreamChunk]:
        """Call ``litellm.acompletion(stream=True)`` and return a mapped iterator.

        Returns an ``AsyncIterator[StreamChunk]`` (rather than yielding
        directly) because the base class ``await``s this coroutine to
        obtain the iterator.
        """
        try:
            await self._ensure_credentials_resolved()
            model_config = self._resolve_model(model)
            litellm_model = f"{self._routing_key}/{model_config.id}"
            kwargs = self._build_kwargs(
                messages,
                litellm_model,
                tools=tools,
                config=config,
                stream=True,
            )
            raw_stream = await _litellm.acompletion(**kwargs)
            return self._wrap_stream(raw_stream, model, model_config)
        except errors.ProviderError:
            raise
        except Exception as exc:
            raise self._map_exception(exc, model) from exc

    async def _do_get_model_capabilities(
        self,
        model: str,
    ) -> ModelCapabilities:
        """Build ``ModelCapabilities`` from config + LiteLLM info.

        Queries LiteLLM's model registry for metadata (tool support,
        vision, max output tokens).  Falls back to
        :class:`ProviderModelDefaults.fallback_max_output_tokens` when
        LiteLLM has no data.  The final ``max_output_tokens`` is
        capped at the model's configured ``max_context``.
        """
        model_config = self._resolve_model(model)
        return self._build_capabilities(model_config)

    async def batch_get_capabilities(
        self,
        models: tuple[str, ...],
    ) -> Mapping[str, ModelCapabilities | None]:
        """Resolve capabilities for many models in a single tight loop.

        Overrides the base implementation: each capability is built
        from the static preset catalog plus a LiteLLM model-info
        lookup, all of which is synchronous and in-process. Per-model
        failures (unknown ids, validation errors) collapse to ``None``
        entries; ``MemoryError`` and ``RecursionError`` propagate.
        """
        results: dict[str, ModelCapabilities | None] = {}
        for model in models:
            # Skip _resolve_model() because its miss path emits
            # PROVIDER_MODEL_NOT_FOUND at ERROR; an expected partial
            # miss in a batch lookup must not be recorded as a failed
            # request. Read the lookup directly and degrade silently to
            # ``None`` (the partial-failure event is reserved for real
            # capability-build errors below).
            model_config = self._model_lookup.get(model)
            if model_config is None:
                results[model] = None
                continue
            try:
                results[model] = self._build_capabilities(model_config)
            except MemoryError, RecursionError:
                raise
            except Exception as exc:
                logger.warning(
                    PROVIDER_BATCH_CAPABILITIES_PARTIAL,
                    provider=self._provider_name,
                    model=model,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                results[model] = None
        # Return a read-only view so callers cannot mutate the batch
        # snapshot in place (matches the immutability pattern used by
        # ``_model_lookup`` itself).
        return MappingProxyType(results)

    def _build_capabilities(
        self,
        model_config: ProviderModelConfig,
    ) -> ModelCapabilities:
        """Construct ``ModelCapabilities`` from a resolved model config.

        Shared between single ``_do_get_model_capabilities`` and the
        batched ``batch_get_capabilities`` so both paths produce
        identical results.
        """
        litellm_model = f"{self._routing_key}/{model_config.id}"
        info = self._get_litellm_model_info(litellm_model)

        fallback = self._config.defaults.fallback_max_output_tokens
        max_output = int(
            info.get("max_output_tokens", 0) or info.get("max_tokens", 0) or fallback,
        )
        supports_streaming = bool(info.get("supports_streaming", True))
        supports_tools = bool(
            info.get("supports_function_calling", False),
        )

        return ModelCapabilities(
            model_id=model_config.id,
            provider=self._provider_name,
            max_context_tokens=model_config.max_context,
            max_output_tokens=min(max_output, model_config.max_context),
            supports_tools=supports_tools,
            supports_vision=bool(
                info.get("supports_vision", False),
            ),
            supports_streaming=supports_streaming,
            supports_streaming_tool_calls=supports_tools and supports_streaming,
            supports_system_messages=bool(
                info.get("supports_system_messages", True),
            ),
            cost_per_1k_input=model_config.cost_per_1k_input,
            cost_per_1k_output=model_config.cost_per_1k_output,
        )

    # ── Model resolution ─────────────────────────────────────────

    @staticmethod
    def _build_model_lookup(
        models: tuple[ProviderModelConfig, ...],
    ) -> dict[str, ProviderModelConfig]:
        """Build alias/id -> model config lookup.

        Raises:
            ValueError: If two models share the same ID, or an alias
                collides with another model's ID or alias.
        """
        lookup: dict[str, ProviderModelConfig] = {}
        for m in models:
            if m.id in lookup and lookup[m.id] is not m:
                logger.error(
                    PROVIDER_CALL_ERROR,
                    error="duplicate_model_id",
                    model_id=m.id,
                )
                msg = f"Duplicate model lookup key: {m.id!r}"
                raise ValueError(msg)
            lookup[m.id] = m
            if m.alias is not None:
                if m.alias in lookup and lookup[m.alias].id != m.id:
                    logger.error(
                        PROVIDER_CALL_ERROR,
                        error="model_alias_collision",
                        alias=m.alias,
                        collides_with=lookup[m.alias].id,
                    )
                    msg = (
                        f"Model alias {m.alias!r} collides with "
                        f"existing key for model {lookup[m.alias].id!r}"
                    )
                    raise ValueError(msg)
                lookup[m.alias] = m
        return lookup

    def _resolve_model(self, model: str) -> ProviderModelConfig:
        """Resolve a model alias or ID to its config.

        Raises:
            ModelNotFoundError: If not found in this provider.
        """
        config = self._model_lookup.get(model)
        if config is None:
            logger.error(
                PROVIDER_MODEL_NOT_FOUND,
                provider=self._provider_name,
                model=model,
                available=sorted(self._model_lookup),
            )
            msg = f"Model {model!r} not found in provider {self._provider_name!r}"
            raise errors.ModelNotFoundError(
                msg,
                context={
                    "provider": self._provider_name,
                    "model": model,
                },
            )
        return config

    # ── Request building ─────────────────────────────────────────

    def _build_kwargs(  # noqa: C901, PLR0912
        self,
        messages: list[ChatMessage],
        litellm_model: str,
        *,
        tools: list[ToolDefinition] | None = None,
        config: CompletionConfig | None = None,
        stream: bool = False,
    ) -> dict[str, Any]:
        """Build keyword arguments for ``litellm.acompletion``."""
        kwargs: dict[str, Any] = {
            "model": litellm_model,
            "messages": messages_to_dicts(messages),
        }
        if tools:
            kwargs["tools"] = tools_to_dicts(tools)
        if stream:
            kwargs["stream"] = True
            kwargs["stream_options"] = {"include_usage": True}

        resolved = self._resolved_credentials
        match self._config.auth_type:
            case AuthType.API_KEY:
                key = resolved.get("api_key") if resolved else None
                if key is None:
                    key = self._config.api_key
                if key is not None:
                    kwargs["api_key"] = key
            case AuthType.OAUTH:
                # Catalog-backed OAuth stores the bearer under the
                # ``access_token`` key (set by
                # ``ConnectionCatalog.store_oauth_tokens``). Fall back
                # to ``api_key`` (legacy embedded config) and finally
                # to the static ``self._config.api_key``. Missing any
                # of these means the request would go out
                # unauthenticated, so we leave ``kwargs["api_key"]``
                # unset only when nothing resolves.
                key = None
                if resolved:
                    key = resolved.get("access_token") or resolved.get("api_key")
                if key is None:
                    key = self._config.api_key
                if key is not None:
                    kwargs["api_key"] = key
            case AuthType.CUSTOM_HEADER:
                # Prefer catalog-resolved credentials so a
                # ``connection_name`` provider can ship the header
                # without duplicating it in config. Fall back to the
                # embedded fields for the legacy, catalog-less path.
                header_name = resolved.get("custom_header_name") if resolved else None
                if header_name is None:
                    header_name = self._config.custom_header_name
                header_value = resolved.get("custom_header_value") if resolved else None
                if header_value is None:
                    header_value = self._config.custom_header_value
                if header_name and header_value:
                    kwargs["extra_headers"] = {header_name: header_value}
            case AuthType.SUBSCRIPTION:
                # Pass as api_key -- the correct kwarg for LiteLLM
                # authentication.  Do NOT use "auth_token" -- it is
                # not a litellm.completion() parameter and is silently
                # discarded.
                token = resolved.get("subscription_token") if resolved else None
                if token is None:
                    token = self._config.subscription_token
                if token is not None:
                    kwargs["api_key"] = token
            case AuthType.NONE:
                pass

        if self._config.base_url is not None:
            kwargs["api_base"] = self._config.base_url
        return _apply_completion_config(kwargs, config)

    # ── Response mapping ─────────────────────────────────────────

    def _map_response(
        self,
        response: Any,
        model_config: ProviderModelConfig,
    ) -> CompletionResponse:
        """Map a LiteLLM ``ModelResponse`` to ``CompletionResponse``."""
        choices = getattr(response, "choices", [])
        if not choices:
            logger.error(
                PROVIDER_CALL_ERROR,
                provider=self._provider_name,
                model=model_config.id,
                error="empty_choices_in_response",
            )
            msg = f"Provider returned empty choices for model {model_config.id!r}"
            raise errors.ProviderInternalError(
                msg,
                context={
                    "provider": self._provider_name,
                    "model": model_config.id,
                },
            )

        choice = choices[0]
        message = choice.message

        content: str | None = getattr(message, "content", None)
        raw_tc = getattr(message, "tool_calls", None)
        tool_calls = extract_tool_calls(raw_tc)
        finish = map_finish_reason(
            getattr(choice, "finish_reason", None),
        )

        usage_obj = getattr(response, "usage", None)
        input_tok = int(getattr(usage_obj, "prompt_tokens", 0) or 0)
        output_tok = int(getattr(usage_obj, "completion_tokens", 0) or 0)
        usage = self.compute_cost(
            input_tok,
            output_tok,
            cost_per_1k_input=model_config.cost_per_1k_input,
            cost_per_1k_output=model_config.cost_per_1k_output,
        )

        return CompletionResponse(
            content=content,
            tool_calls=tool_calls,
            finish_reason=finish,
            usage=usage,
            model=model_config.id,
            provider_request_id=getattr(response, "id", None),
        )

    # ── Streaming ────────────────────────────────────────────────

    def _wrap_stream(
        self,
        raw_stream: Any,
        model: str,
        model_config: ProviderModelConfig,
    ) -> AsyncGenerator[StreamChunk]:
        """Return an async generator that maps raw chunks."""
        process = self._process_chunk
        handle_exc = self._map_exception
        provider = self._provider_name

        async def _generate() -> AsyncGenerator[StreamChunk]:
            pending: dict[int, _ToolCallAccumulator] = {}
            try:
                async for chunk in raw_stream:
                    for sc in process(
                        chunk,
                        pending,
                        model_config,
                    ):
                        yield sc
            except Exception as exc:
                logger.error(
                    PROVIDER_CALL_ERROR,
                    provider=provider,
                    model=model,
                )
                raise handle_exc(exc, model) from exc

            for sc in emit_pending_tool_calls(pending):
                yield sc
            logger.debug(
                PROVIDER_STREAM_DONE,
                provider=provider,
                model=model,
            )
            yield StreamChunk(event_type=StreamEventType.DONE)

        return _generate()

    def _process_chunk(
        self,
        chunk: Any,
        pending: dict[int, _ToolCallAccumulator],
        model_config: ProviderModelConfig,
    ) -> list[StreamChunk]:
        """Extract ``StreamChunk`` events from one raw chunk."""
        result: list[StreamChunk] = []
        choices = getattr(chunk, "choices", [])

        if not choices:
            usage_obj = getattr(chunk, "usage", None)
            if usage_obj is not None:
                result.append(
                    self._make_usage_chunk(usage_obj, model_config),
                )
            return result

        delta = getattr(choices[0], "delta", None)
        if delta is None:
            logger.debug(PROVIDER_STREAM_CHUNK_NO_DELTA)
            return result

        text = getattr(delta, "content", None)
        if text:
            result.append(
                StreamChunk(
                    event_type=StreamEventType.CONTENT_DELTA,
                    content=text,
                )
            )

        raw_tc = getattr(delta, "tool_calls", None)
        if raw_tc:
            accumulate_tool_call_deltas(raw_tc, pending)

        usage_obj = getattr(chunk, "usage", None)
        if usage_obj is not None:
            result.append(
                self._make_usage_chunk(usage_obj, model_config),
            )

        return result

    def _make_usage_chunk(
        self,
        usage_obj: Any,
        model_config: ProviderModelConfig,
    ) -> StreamChunk:
        """Build a ``USAGE`` stream chunk."""
        input_tok = int(getattr(usage_obj, "prompt_tokens", 0) or 0)
        output_tok = int(getattr(usage_obj, "completion_tokens", 0) or 0)
        usage = self.compute_cost(
            input_tok,
            output_tok,
            cost_per_1k_input=model_config.cost_per_1k_input,
            cost_per_1k_output=model_config.cost_per_1k_output,
        )
        return StreamChunk(
            event_type=StreamEventType.USAGE,
            usage=usage,
        )

    # ── Exception mapping ────────────────────────────────────────

    def _map_exception(
        self,
        exc: Exception,
        model: str,
    ) -> errors.ProviderError:
        """Map a LiteLLM exception to the provider error hierarchy."""
        ctx: dict[str, Any] = {
            "provider": self._provider_name,
            "model": model,
        }

        for litellm_type, our_type in _EXCEPTION_TABLE:
            if isinstance(exc, litellm_type):
                if our_type is errors.RateLimitError:
                    logger.warning(
                        PROVIDER_RATE_LIMITED,
                        provider=self._provider_name,
                        model=model,
                    )
                    return errors.RateLimitError(
                        str(exc),
                        retry_after=self._extract_retry_after(exc),
                        context=ctx,
                    )
                if our_type is errors.AuthenticationError:
                    logger.error(
                        PROVIDER_AUTH_ERROR,
                        provider=self._provider_name,
                        model=model,
                    )
                elif our_type is errors.ProviderConnectionError:
                    logger.warning(
                        PROVIDER_CONNECTION_ERROR,
                        provider=self._provider_name,
                        model=model,
                    )
                return our_type(
                    f"Provider {self._provider_name} error",
                    context={**ctx, "detail": str(exc)},
                )

        if isinstance(exc, errors.ProviderError):
            return exc

        return errors.ProviderInternalError(
            f"Unexpected error from provider {self._provider_name}",
            context={**ctx, "detail": str(exc)},
        )

    @staticmethod
    def _extract_retry_after(exc: Exception) -> float | None:
        """Extract ``retry-after`` seconds from exception headers."""
        headers = getattr(exc, "headers", None)
        if not isinstance(headers, dict):
            return None
        # Case-insensitive lookup per HTTP semantics
        raw: str | None = None
        for key, value in headers.items():
            if isinstance(key, str) and key.lower() == "retry-after":
                raw = value
                break
        if raw is None:
            return None
        try:
            return float(raw)
        except ValueError, TypeError:
            logger.debug(
                PROVIDER_RETRY_AFTER_PARSE_FAILED,
                raw_value=repr(raw),
            )
            return None

    # ── LiteLLM model info ───────────────────────────────────────

    @staticmethod
    def _get_litellm_model_info(
        litellm_model: str,
    ) -> dict[str, Any]:
        """Query LiteLLM for static model metadata.

        Returns empty dict if the model is unknown to LiteLLM.
        Uses config defaults when metadata is unavailable.
        """
        try:
            raw = _litellm.get_model_info(model=litellm_model)
            info: dict[str, Any] = dict(raw) if raw else {}
        except KeyError, ValueError:
            logger.info(
                PROVIDER_MODEL_INFO_UNAVAILABLE,
                model=litellm_model,
            )
            return {}
        except Exception:
            logger.warning(
                PROVIDER_MODEL_INFO_UNEXPECTED_ERROR,
                model=litellm_model,
            )
            return {}
        return info if isinstance(info, dict) else {}


# ── Module-level helpers ─────────────────────────────────────────


def _apply_completion_config(
    kwargs: dict[str, Any],
    config: CompletionConfig | None,
) -> dict[str, Any]:
    """Return a new kwargs dict with ``CompletionConfig`` fields merged in."""
    if config is None:
        return kwargs
    extra: dict[str, Any] = {}
    if config.temperature is not None:
        extra["temperature"] = config.temperature
    if config.max_tokens is not None:
        extra["max_tokens"] = config.max_tokens
    if config.stop_sequences:
        extra["stop"] = list(config.stop_sequences)
    if config.top_p is not None:
        extra["top_p"] = config.top_p
    if config.timeout is not None:
        extra["timeout"] = config.timeout
    return {**kwargs, **extra}
