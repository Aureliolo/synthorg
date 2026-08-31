"""Deterministic scripted completion driver.

``ScriptedDriver`` is the canonical, config-selectable deterministic
``CompletionProvider``.  It produces reproducible completions with no
network I/O and no LLM spend, so the acceptance suite and the
simulation harness can drive the live agent runtime end to end.

It is NOT a real LLM backend: instantiating it always logs a WARNING so
an operator cannot quietly run a production company on scripted output.

Response selection is pluggable via the ``ScriptedResponseStrategy``
protocol.  Three strategies ship:

* :class:`DeterministicResponseStrategy` -- the safe default; a stable
  acknowledgement derived from the last user message (reproducible).
* :class:`SequencedResponseStrategy` -- replays a fixed tuple of
  responses in order; raises :class:`ScriptedProviderExhaustedError`
  when over-drawn.
* :class:`SingleResponseStrategy` -- returns one configured response
  every call, or raises one configured error.
"""

import asyncio
import base64
import hashlib
import threading
from collections.abc import AsyncIterator, Mapping
from typing import Final, Protocol, override, runtime_checkable

from synthorg.core.completion_enums import FinishReason
from synthorg.core.domain_errors import DomainError
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.provider import (
    PROVIDER_SCRIPTED_DRIVER_INSTANTIATED,
)
from synthorg.providers._cost import compute_image_cost
from synthorg.providers.base import BaseCompletionProvider
from synthorg.providers.capabilities import ModelCapabilities
from synthorg.providers.drivers.scripted_image import render_deterministic_png
from synthorg.providers.enums import MessageRole, StreamEventType
from synthorg.providers.image_generation import ImageGenerationMixin
from synthorg.providers.image_models import (
    GeneratedImage,
    ImageGenerationConfig,
    ImageGenerationResponse,
)
from synthorg.providers.models import (
    ChatMessage,
    CompletionConfig,
    CompletionResponse,
    StreamChunk,
    TokenUsage,
    ToolDefinition,
)

logger = get_logger(__name__)

_DEFAULT_PROVIDER_NAME: Final[str] = "scripted"
_DEFAULT_MODEL_ID: Final[str] = "scripted-model-001"
_SCRIPTED_INPUT_TOKENS: Final[int] = 1
_SCRIPTED_OUTPUT_TOKENS: Final[int] = 1
_SCRIPTED_COST: Final[float] = 0.0
_SCRIPTED_IMAGE_COST: Final[float] = 0.0
_CAP_MAX_CONTEXT_TOKENS: Final[int] = 200_000
_DET_DIGEST_LEN: Final[int] = 12


class ScriptedProviderExhaustedError(DomainError):
    """A sequenced scripted provider was called more times than scripted.

    Surfaced loudly (it is a test / simulation misconfiguration) rather
    than wrapping around or returning a silent default.
    """


@runtime_checkable
class ScriptedResponseStrategy(Protocol):
    """Pluggable response selector for :class:`ScriptedDriver`."""

    def next_response(
        self,
        messages: list[ChatMessage],
        model: str,
        tools: list[ToolDefinition] | None,
        config: CompletionConfig | None,
    ) -> CompletionResponse:
        """Return the next scripted response (or raise)."""
        ...


def _last_user_text(messages: list[ChatMessage]) -> str:
    """Return the most recent user message text, or empty string."""
    for message in reversed(messages):
        if message.role == MessageRole.USER and message.content is not None:
            return message.content
    return ""


def _scripted_usage() -> TokenUsage:
    """Build the fixed token-usage record stamped on scripted responses.

    Returns:
        A ``TokenUsage`` with the deterministic scripted token counts.
    """
    return TokenUsage(
        input_tokens=_SCRIPTED_INPUT_TOKENS,
        output_tokens=_SCRIPTED_OUTPUT_TOKENS,
        cost=_SCRIPTED_COST,
    )


class DeterministicResponseStrategy:
    """Stable acknowledgement derived from the last user message.

    Same input always yields the same content (a short stable digest),
    so simulation runs are reproducible without randomness or I/O.
    """

    def next_response(
        self,
        messages: list[ChatMessage],
        model: str,
        tools: list[ToolDefinition] | None,
        config: CompletionConfig | None,
    ) -> CompletionResponse:
        """Return a deterministic text completion."""
        del tools, config
        prompt = _last_user_text(messages)
        digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:_DET_DIGEST_LEN]
        return CompletionResponse(
            content=f"Scripted deterministic completion [{digest}]",
            finish_reason=FinishReason.STOP,
            usage=_scripted_usage(),
            model=model or _DEFAULT_MODEL_ID,
        )


class SequencedResponseStrategy:
    """Replay a fixed tuple of responses in order."""

    __slots__ = ("_index", "_lock", "_responses")

    def __init__(self, responses: tuple[CompletionResponse, ...]) -> None:
        self._responses = responses
        self._index = 0
        self._lock = threading.Lock()

    def next_response(
        self,
        messages: list[ChatMessage],
        model: str,
        tools: list[ToolDefinition] | None,
        config: CompletionConfig | None,
    ) -> CompletionResponse:
        """Return the next response, or raise when exhausted.

        The check-and-advance is guarded so concurrent agent runs
        sharing one scripted provider cannot skip or replay a response.

        Returns:
            The next ``CompletionResponse`` from the scripted sequence.

        Raises:
            ScriptedProviderExhaustedError: If the call count exceeds the
                number of scripted responses.
        """
        del messages, model, tools, config
        with self._lock:
            if self._index >= len(self._responses):
                msg = (
                    f"SequencedResponseStrategy exhausted: call "
                    f"#{self._index + 1} but only {len(self._responses)} "
                    f"responses were scripted"
                )
                raise ScriptedProviderExhaustedError(msg)
            response = self._responses[self._index]
            self._index += 1
        return response


class SingleResponseStrategy:
    """Return one configured response every call, or raise one error."""

    __slots__ = ("_error", "_response")

    def __init__(
        self,
        *,
        response: CompletionResponse | None = None,
        error: BaseException | None = None,
    ) -> None:
        if response is None and error is None:
            msg = "SingleResponseStrategy requires a response or an error"
            raise ValueError(msg)
        self._response = response
        self._error = error

    def next_response(
        self,
        messages: list[ChatMessage],
        model: str,
        tools: list[ToolDefinition] | None,
        config: CompletionConfig | None,
    ) -> CompletionResponse:
        """Return the configured response or raise the configured error."""
        del messages, model, tools, config
        if self._error is not None:
            raise self._error
        assert self._response is not None  # noqa: S101 -- ctor invariant
        return self._response


class ScriptedDriver(ImageGenerationMixin, BaseCompletionProvider):
    """Deterministic test / simulation completion provider.

    Construct directly with a ``strategy=`` for tests, or via
    ``ProviderRegistry.from_config`` (driver ``"scripted"``), which calls
    ``ScriptedDriver(provider_name, config)`` and uses the safe
    deterministic default strategy.

    ``config`` is accepted for factory-signature parity but ignored:
    behaviour is controlled by ``strategy=``, not provider config.
    ``super().__init__()`` is called with no arguments deliberately;
    a deterministic provider needs no retry handler or rate limiter
    (the base class defaults suffice and never fire on local replay).
    """

    def __init__(
        self,
        provider_name: str = _DEFAULT_PROVIDER_NAME,
        config: object | None = None,
        *,
        strategy: ScriptedResponseStrategy | None = None,
        capabilities: ModelCapabilities | None = None,
    ) -> None:
        super().__init__()
        del config
        self._provider_name = provider_name or _DEFAULT_PROVIDER_NAME
        self._strategy: ScriptedResponseStrategy = (
            strategy if strategy is not None else DeterministicResponseStrategy()
        )
        self._capabilities = capabilities
        logger.warning(
            PROVIDER_SCRIPTED_DRIVER_INSTANTIATED,
            provider=self._provider_name,
            strategy=type(self._strategy).__name__,
            note=(
                "deterministic test/simulation provider -- not a real "
                "LLM backend; do not use for production work"
            ),
        )

    @override
    async def _do_complete(
        self,
        messages: list[ChatMessage],
        model: str,
        *,
        tools: list[ToolDefinition] | None = None,
        config: CompletionConfig | None = None,
    ) -> CompletionResponse:
        """Return the strategy's next response."""
        return self._strategy.next_response(messages, model, tools, config)

    @override
    async def _do_stream(
        self,
        messages: list[ChatMessage],
        model: str,
        *,
        tools: list[ToolDefinition] | None = None,
        config: CompletionConfig | None = None,
    ) -> AsyncIterator[StreamChunk]:
        """Decompose the scripted completion into stream chunks.

        Returns:
            An async iterator of ``StreamChunk`` objects derived from the
            next scripted ``CompletionResponse``.
        """
        response = self._strategy.next_response(messages, model, tools, config)

        async def _chunks() -> AsyncIterator[StreamChunk]:
            """Yield the scripted response decomposed into stream chunks."""
            # Before the content, because that is the order a reasoning model
            # produces them, and because a scripted response carrying only
            # reasoning would otherwise stream as nothing and reassemble into
            # an empty turn the loop reads as an error.
            if response.reasoning is not None:
                yield StreamChunk(
                    event_type=StreamEventType.REASONING_DELTA,
                    content=response.reasoning,
                )
            if response.content is not None:
                yield StreamChunk(
                    event_type=StreamEventType.CONTENT_DELTA,
                    content=response.content,
                )
            for tool_call in response.tool_calls:
                yield StreamChunk(
                    event_type=StreamEventType.TOOL_CALL_DELTA,
                    tool_call_delta=tool_call,
                )
            yield StreamChunk(
                event_type=StreamEventType.USAGE,
                usage=response.usage,
            )
            # Carry the faithful finish reason on the terminal event so a
            # consumer reassembling the stream recovers it, matching the
            # real driver. The dropped-call flag rides the same event for
            # the same reason, and it has to survive the round trip or a
            # scripted turn cannot reproduce the shape at all: no chunk is
            # emitted for a call that never assembled.
            yield StreamChunk(
                event_type=StreamEventType.DONE,
                finish_reason=response.finish_reason,
                dropped_tool_calls=response.dropped_tool_calls,
            )

        return _chunks()

    @override
    async def _do_generate_image(
        self,
        prompt: str,
        model: str,
        *,
        config: ImageGenerationConfig | None = None,
    ) -> ImageGenerationResponse:
        """Render deterministic offline PNGs seeded by the prompt.

        No network, no vendor: exercises the full image-generation path
        for tests and air-gapped installs. Output is byte-stable for a
        given ``(prompt, size, index)``.

        Returns:
            An ``ImageGenerationResponse`` carrying ``config.n`` PNG images.
        """
        cfg = config if config is not None else ImageGenerationConfig()

        def _render_all() -> tuple[GeneratedImage, ...]:
            """Render every requested PNG (CPU-bound; run off the loop).

            Returns:
                The rendered images as ``GeneratedImage`` values.
            """
            return tuple(
                GeneratedImage(
                    b64_data=NotBlankStr(
                        base64.b64encode(
                            render_deterministic_png(prompt, size=cfg.size, index=i)
                        ).decode("ascii")
                    ),
                )
                for i in range(cfg.n)
            )

        # PIL render + PNG encode are CPU-bound; keep them off the event loop.
        images = await asyncio.to_thread(_render_all)
        return ImageGenerationResponse(
            images=images,
            usage=compute_image_cost(cfg.n, cost_per_image=_SCRIPTED_IMAGE_COST),
            model=NotBlankStr(model or _DEFAULT_MODEL_ID),
        )

    @override
    async def _do_get_model_capabilities(
        self,
        model: str,
    ) -> ModelCapabilities:
        """Return deterministic capabilities for any model id."""
        if self._capabilities is not None:
            return self._capabilities
        return ModelCapabilities(
            model_id=model or _DEFAULT_MODEL_ID,
            provider=self._provider_name,
            max_context_tokens=_CAP_MAX_CONTEXT_TOKENS,
            supports_tools=True,
            supports_streaming=True,
        )

    @override
    async def batch_get_capabilities(
        self,
        models: tuple[str, ...],
    ) -> Mapping[str, ModelCapabilities | None]:
        """Return deterministic capabilities for each requested model."""
        return {model: await self._do_get_model_capabilities(model) for model in models}
