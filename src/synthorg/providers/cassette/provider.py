"""The cassette provider wrapper.

``CassetteCompletionProvider`` decorates an inner driver and overrides
every public method. It overrides the **public** API (``complete`` /
``stream`` / capability lookups), never the ``_do_*`` hooks: the base
class merges fresh ``_synthorg_latency_ms`` / ``_synthorg_retry_count``
into ``provider_metadata`` *after* ``_do_complete`` returns, so
replaying through ``_do_complete`` would clobber the recorded metadata
with the replay run's zeroes and break byte-identical replay. Returning
the recorded :class:`CompletionResponse` verbatim from an overridden
``complete`` preserves it exactly.

Record mode delegates to the inner driver (real path, real spend) and
persists the verbatim outcome. Replay mode serves the recorded outcome
and **never touches the inner driver** -- the inner driver may even be
``None`` in a pure replay run -- which is what makes "zero real LLM
calls in replay" structurally true rather than best-effort.
"""

from collections.abc import (  # noqa: TC003 -- runtime override signatures
    AsyncIterator,
    Mapping,
)
from typing import Any

from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.provider import (
    PROVIDER_CASSETTE_RECORDED,
    PROVIDER_CASSETTE_REPLAYED,
)
from synthorg.providers.base import BaseCompletionProvider
from synthorg.providers.capabilities import (  # noqa: TC001 -- override return type
    ModelCapabilities,
)
from synthorg.providers.errors import ProviderError
from synthorg.providers.models import (  # noqa: TC001 -- override signatures
    ChatMessage,
    CompletionConfig,
    CompletionResponse,
    StreamChunk,
    ToolDefinition,
)

from .errors import CassetteFormatError, CassetteInternalError, provider_error_for
from .keying import CassetteMethod, request_hash
from .mode import CassetteMode
from .store import CassetteOutcome, CassetteOutcomeKind, CassetteSession

logger = get_logger(__name__)


class CassetteCompletionProvider(BaseCompletionProvider):
    """Record/replay decorator around an inner completion provider.

    Args:
        inner: The wrapped driver. Required in record mode; may be
            ``None`` in replay mode (a pure replay run constructs no
            real driver).
        session: Shared cassette session (lanes, FIFO, persistence).
        provider_name: Stable provider label used for request keying;
            must be identical between the record and replay runs.
    """

    def __init__(
        self,
        *,
        inner: BaseCompletionProvider | None,
        session: CassetteSession,
        provider_name: str,
    ) -> None:
        super().__init__()
        self._inner = inner
        self._session = session
        self._provider_name = provider_name

    @property
    def provider_name(self) -> str:
        """The stable provider label used for request keying."""
        return self._provider_name

    @property
    def session(self) -> CassetteSession:
        """The shared cassette session backing this wrapper."""
        return self._session

    def _provider_label(self) -> str:
        """Return the stable label used for keying and metrics."""
        return self._provider_name

    def _require_inner(self) -> BaseCompletionProvider:
        """Return the inner driver or fail loudly (record-mode only)."""
        if self._inner is None:
            msg = "record mode requires a wrapped inner provider"
            raise CassetteInternalError(
                msg,
                context={"provider": self._provider_name},
            )
        return self._inner

    def _request_repr(
        self,
        *,
        method: CassetteMethod,
        model: str,
        messages: tuple[ChatMessage, ...],
        tools: tuple[ToolDefinition, ...],
        config: CompletionConfig | None,
    ) -> dict[str, Any]:
        """Build the human-readable (later redacted) request copy."""
        return {
            "provider": self._provider_name,
            "method": method.value,
            "model": model,
            "messages": [m.model_dump(mode="json") for m in messages],
            "tools": [t.model_dump(mode="json") for t in tools],
            "config": config.model_dump(mode="json") if config else None,
        }

    # -- complete -----------------------------------------------------

    async def complete(
        self,
        messages: list[ChatMessage],
        model: str,
        *,
        tools: list[ToolDefinition] | None = None,
        config: CompletionConfig | None = None,
    ) -> CompletionResponse:
        """Record or replay a non-streaming completion."""
        self._validate_messages(messages)
        self._validate_model(model)
        msgs = tuple(messages)
        tls = tuple(tools or ())
        digest = request_hash(
            method=CassetteMethod.COMPLETE,
            provider=self._provider_name,
            model=model,
            messages=msgs,
            tools=tls,
            config=config,
        )
        if self._session.mode is CassetteMode.REPLAY:
            outcome = self._session.take(request_hash=digest)
            logger.debug(
                PROVIDER_CASSETTE_REPLAYED,
                provider=self._provider_name,
                model=model,
                method=CassetteMethod.COMPLETE.value,
            )
            return self._replay_response(outcome)

        request_repr = self._request_repr(
            method=CassetteMethod.COMPLETE,
            model=model,
            messages=msgs,
            tools=tls,
            config=config,
        )
        try:
            response = await self._require_inner().complete(
                messages,
                model,
                tools=tools,
                config=config,
            )
        except MemoryError, RecursionError:
            raise
        except ProviderError as exc:
            self._session.record_interaction(
                method=CassetteMethod.COMPLETE,
                request_hash=digest,
                request_repr=request_repr,
                outcome=CassetteOutcome.from_error(
                    error_class=type(exc).__name__,
                    message=safe_error_description(exc),
                ),
            )
            raise
        self._session.record_interaction(
            method=CassetteMethod.COMPLETE,
            request_hash=digest,
            request_repr=request_repr,
            outcome=CassetteOutcome.from_response(response),
        )
        logger.debug(
            PROVIDER_CASSETTE_RECORDED,
            provider=self._provider_name,
            model=model,
            method=CassetteMethod.COMPLETE.value,
        )
        return response

    def _replay_response(self, outcome: CassetteOutcome) -> CompletionResponse:
        """Return the recorded response or re-raise the recorded error."""
        if outcome.kind is CassetteOutcomeKind.ERROR and outcome.error is not None:
            raise provider_error_for(
                outcome.error.error_class,
                outcome.error.message,
            )
        if outcome.kind is CassetteOutcomeKind.RESPONSE and (
            outcome.response is not None
        ):
            return outcome.response
        msg = f"cassette outcome kind {outcome.kind.value!r} is not a completion"
        raise CassetteFormatError(msg, context={"kind": outcome.kind.value})

    # -- stream -------------------------------------------------------

    async def stream(
        self,
        messages: list[ChatMessage],
        model: str,
        *,
        tools: list[ToolDefinition] | None = None,
        config: CompletionConfig | None = None,
    ) -> AsyncIterator[StreamChunk]:
        """Record or replay a streaming completion.

        Recording fully consumes the inner stream to capture every
        chunk, then re-emits the recorded chunks so the caller still
        receives a stream. Replay re-emits the recorded chunks with
        identical content and order (inter-chunk timing is not
        preserved and is irrelevant to replay determinism).
        """
        self._validate_messages(messages)
        self._validate_model(model)
        msgs = tuple(messages)
        tls = tuple(tools or ())
        digest = request_hash(
            method=CassetteMethod.STREAM,
            provider=self._provider_name,
            model=model,
            messages=msgs,
            tools=tls,
            config=config,
        )
        if self._session.mode is CassetteMode.REPLAY:
            outcome = self._session.take(request_hash=digest)
            chunks = self._replay_stream_chunks(outcome)
            logger.debug(
                PROVIDER_CASSETTE_REPLAYED,
                provider=self._provider_name,
                model=model,
                method=CassetteMethod.STREAM.value,
            )
            return _aiter(chunks)

        request_repr = self._request_repr(
            method=CassetteMethod.STREAM,
            model=model,
            messages=msgs,
            tools=tls,
            config=config,
        )
        try:
            inner_stream = await self._require_inner().stream(
                messages,
                model,
                tools=tools,
                config=config,
            )
            recorded: list[StreamChunk] = [c async for c in inner_stream]
        except MemoryError, RecursionError:
            raise
        except ProviderError as exc:
            self._session.record_interaction(
                method=CassetteMethod.STREAM,
                request_hash=digest,
                request_repr=request_repr,
                outcome=CassetteOutcome.from_error(
                    error_class=type(exc).__name__,
                    message=safe_error_description(exc),
                ),
            )
            raise
        self._session.record_interaction(
            method=CassetteMethod.STREAM,
            request_hash=digest,
            request_repr=request_repr,
            outcome=CassetteOutcome.from_stream(tuple(recorded)),
        )
        logger.debug(
            PROVIDER_CASSETTE_RECORDED,
            provider=self._provider_name,
            model=model,
            method=CassetteMethod.STREAM.value,
        )
        return _aiter(recorded)

    def _replay_stream_chunks(
        self,
        outcome: CassetteOutcome,
    ) -> list[StreamChunk]:
        """Return recorded chunks or re-raise the recorded error."""
        if outcome.kind is CassetteOutcomeKind.ERROR and outcome.error is not None:
            raise provider_error_for(
                outcome.error.error_class,
                outcome.error.message,
            )
        if outcome.kind is CassetteOutcomeKind.STREAM and (
            outcome.stream_chunks is not None
        ):
            return list(outcome.stream_chunks)
        msg = f"cassette outcome kind {outcome.kind.value!r} is not a stream"
        raise CassetteFormatError(msg, context={"kind": outcome.kind.value})

    # -- capabilities -------------------------------------------------

    async def get_model_capabilities(self, model: str) -> ModelCapabilities:
        """Record or replay a single-model capability lookup."""
        self._validate_model(model)
        digest = request_hash(
            method=CassetteMethod.CAPABILITIES,
            provider=self._provider_name,
            model=model,
        )
        if self._session.mode is CassetteMode.REPLAY:
            outcome = self._session.take(request_hash=digest)
            if outcome.kind is CassetteOutcomeKind.ERROR and (
                outcome.error is not None
            ):
                raise provider_error_for(
                    outcome.error.error_class,
                    outcome.error.message,
                )
            if outcome.kind is CassetteOutcomeKind.CAPABILITIES and (
                outcome.capabilities is not None
            ):
                return outcome.capabilities
            msg = (
                f"cassette outcome kind {outcome.kind.value!r} "
                f"is not a capability lookup"
            )
            raise CassetteFormatError(msg, context={"kind": outcome.kind.value})

        request_repr = {"provider": self._provider_name, "model": model}
        try:
            capabilities = await self._require_inner().get_model_capabilities(
                model,
            )
        except MemoryError, RecursionError:
            raise
        except ProviderError as exc:
            self._session.record_interaction(
                method=CassetteMethod.CAPABILITIES,
                request_hash=digest,
                request_repr=request_repr,
                outcome=CassetteOutcome.from_error(
                    error_class=type(exc).__name__,
                    message=safe_error_description(exc),
                ),
            )
            raise
        self._session.record_interaction(
            method=CassetteMethod.CAPABILITIES,
            request_hash=digest,
            request_repr=request_repr,
            outcome=CassetteOutcome.from_capabilities(capabilities),
        )
        return capabilities

    async def batch_get_capabilities(
        self,
        models: tuple[str, ...],
    ) -> Mapping[str, ModelCapabilities | None]:
        """Resolve many capability lookups, each through the cassette.

        Overridden so a cassette miss/exhaustion propagates loudly
        instead of being swallowed to ``None`` by the base class's
        per-model degradation (which would silently hide a broken
        replay).
        """
        return {m: await self.get_model_capabilities(m) for m in models}

    # -- unreachable hooks --------------------------------------------

    async def _do_complete(
        self,
        messages: list[ChatMessage],
        model: str,
        *,
        tools: list[ToolDefinition] | None = None,
        config: CompletionConfig | None = None,
    ) -> CompletionResponse:
        """Unreachable: ``complete`` is fully overridden."""
        del messages, model, tools, config
        raise CassetteInternalError(CassetteInternalError.default_message)

    async def _do_stream(
        self,
        messages: list[ChatMessage],
        model: str,
        *,
        tools: list[ToolDefinition] | None = None,
        config: CompletionConfig | None = None,
    ) -> AsyncIterator[StreamChunk]:
        """Unreachable: ``stream`` is fully overridden."""
        del messages, model, tools, config
        raise CassetteInternalError(CassetteInternalError.default_message)

    async def _do_get_model_capabilities(
        self,
        model: str,
    ) -> ModelCapabilities:
        """Unreachable: ``get_model_capabilities`` is fully overridden."""
        del model
        raise CassetteInternalError(CassetteInternalError.default_message)


async def _aiter(chunks: list[StreamChunk]) -> AsyncIterator[StreamChunk]:
    """Re-emit a recorded chunk list as an async iterator."""
    for chunk in chunks:
        yield chunk


__all__ = ["CassetteCompletionProvider"]
