# module-kind: service
"""The gateway request pipeline: token -> binding -> budget -> dispatch.

``GatewayService`` is the OpenAI-compatible completion pipeline, kept
free of any HTTP/Litestar coupling so it is unit-testable with fakes. The
controller resolves the per-request, hot-swappable dependencies
(provider registry, cost tracker, enabled flag) from ``AppState`` and
passes them in; the stable ones (signer, ledger, clock) are held on the
service.

Ordering is load-bearing: verify the signed token, enforce Explicit
Provider Binding from the token (never the request's ``model``), scan
inbound content (advisory injection heuristics), enforce the hard run
ceiling, then dispatch under a cost-recording scope so cost and prompt
attribution flow through the single provider chokepoint.
"""

import contextlib
import json
import secrets
from collections.abc import AsyncGenerator
from typing import Final, Protocol, runtime_checkable

from synthorg.api.gateway.ledger import RunCostLedger
from synthorg.api.gateway.translation import (
    ParsedChatRequest,
    parse_chat_request,
    response_to_openai,
    stream_chunk_to_openai,
)
from synthorg.budget.call_category import LLMCallCategory
from synthorg.budget.tracker_protocol import CostTrackerProtocol
from synthorg.core.clock import Clock, SystemClock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import ServiceUnavailableError
from synthorg.engine.prompt_safety import scan_injection_heuristics
from synthorg.llm.gateway_errors import (
    GatewayBudgetExhaustedError,
    GatewayTokenInvalidError,
)
from synthorg.llm.gateway_token import GatewaySigner, GatewayTokenClaims
from synthorg.observability import (
    get_logger,
    safe_error_description,
    scrub_secret_tokens,
)
from synthorg.observability.events.gateway import (
    GATEWAY_BUDGET_KILL,
    GATEWAY_DISPATCH_FAILED,
    GATEWAY_INJECTION_SUSPECTED,
    GATEWAY_PROVIDER_UNAVAILABLE,
    GATEWAY_REQUEST_RECEIVED,
    GATEWAY_TOKEN_REJECTED,
)
from synthorg.providers._resilience import aclose_quietly
from synthorg.providers.cost_recording import cost_recording_scope
from synthorg.providers.enums import StreamEventType
from synthorg.providers.errors import DriverNotRegisteredError
from synthorg.providers.models import CompletionResponse, StreamChunk
from synthorg.providers.protocol import CompletionProvider

logger = get_logger(__name__)

_ID_TOKEN_BYTES: Final[int] = 12
_INJECTION_SAMPLE_CHARS: Final[int] = 200
_SSE_DONE: Final[str] = "data: [DONE]\n\n"


@runtime_checkable
class ProviderResolver(Protocol):
    """Minimal registry surface the gateway depends on.

    The concrete :class:`ProviderRegistry` satisfies this structurally; the
    service depends only on the ``get`` lookup so a hot-swapped registry or
    a test double slots in unchanged.
    """

    def get(self, name: str) -> CompletionProvider:
        """Return the driver bound to *name*, raising if unregistered."""
        ...


class GatewayService:
    """OpenAI-compatible completion pipeline over the provider registry.

    Args:
        signer: Verifies the per-run bearer token.
        ledger: Tracks per-run cost for the hard token kill.
        clock: Time source for response timestamps.
    """

    def __init__(
        self,
        *,
        signer: GatewaySigner,
        ledger: RunCostLedger,
        clock: Clock | None = None,
    ) -> None:
        self._signer = signer
        self._ledger = ledger
        self._clock: Clock = clock if clock is not None else SystemClock()

    async def complete(
        self,
        *,
        token: str,
        raw_request: dict[str, object],
        registry: ProviderResolver,
        cost_tracker: CostTrackerProtocol | None,
        enabled: bool,
    ) -> dict[str, object]:
        """Run one buffered completion and return an OpenAI response dict.

        Args:
            token: The per-run bearer token.
            raw_request: The parsed OpenAI request body.
            registry: The live provider registry.
            cost_tracker: The cost sink, or ``None``.
            enabled: Whether the gateway is enabled.

        Returns:
            An OpenAI ``chat.completion`` object.

        Raises:
            GatewayTokenInvalidError: If the token is invalid.
            GatewayBudgetExhaustedError: If the run ceiling is spent.
            ServiceUnavailableError: If the gateway is off or the bound
                provider is unregistered.
            ValidationError: If the request is malformed.
        """
        claims, parsed, provider = self._prepare(
            token, raw_request, registry, enabled=enabled
        )
        await self._enforce_budget(claims)
        response = await self._dispatch(provider, parsed, claims, cost_tracker)
        await self._ledger.add(claims.execution_id, response.usage.cost)
        return response_to_openai(
            response, response_id=self._new_id(), created=self._now_epoch()
        )

    async def stream(
        self,
        *,
        token: str,
        raw_request: dict[str, object],
        registry: ProviderResolver,
        cost_tracker: CostTrackerProtocol | None,
        enabled: bool,
    ) -> AsyncGenerator[str]:
        """Run one streaming completion, yielding OpenAI SSE frames.

        Args:
            token: The per-run bearer token.
            raw_request: The parsed OpenAI request body.
            registry: The live provider registry.
            cost_tracker: The cost sink, or ``None``.
            enabled: Whether the gateway is enabled.

        Yields:
            SSE ``data:`` frames, terminated by a ``[DONE]`` sentinel.

        Raises:
            GatewayTokenInvalidError: If the token is invalid.
            GatewayBudgetExhaustedError: If the run ceiling is spent.
            ServiceUnavailableError: If the gateway is off or the bound
                provider is unregistered.
            ValidationError: If the request is malformed.
        """
        claims, parsed, provider = self._prepare(
            token, raw_request, registry, enabled=enabled
        )
        await self._enforce_budget(claims)
        response_id = self._new_id()
        created = self._now_epoch()
        # aclosing drives ``_drive_stream``'s finally (cost-scope exit + provider
        # stream close) promptly on an early consumer stop (client disconnect),
        # rather than deferring it to async-generator GC and leaking the open
        # provider connection / skewing cost attribution.
        async with contextlib.aclosing(
            self._drive_stream(
                provider,
                parsed=parsed,
                claims=claims,
                cost_tracker=cost_tracker,
                response_id=response_id,
                created=created,
            )
        ) as frames:
            async for frame in frames:
                yield frame
        yield _SSE_DONE

    async def _drive_stream(
        self,
        provider: CompletionProvider,
        *,
        parsed: ParsedChatRequest,
        claims: GatewayTokenClaims,
        cost_tracker: CostTrackerProtocol | None,
        response_id: str,
        created: int,
    ) -> AsyncGenerator[str]:
        """Open the provider stream and yield OpenAI SSE frames."""
        async with cost_recording_scope(
            cost_tracker=cost_tracker,
            agent_id=claims.agent_id,
            task_id=claims.task_id,
            project_id=claims.project_id,
            purpose=None,
            call_category=LLMCallCategory.PRODUCTIVE,
            currency=claims.currency,
        ):
            stream = await provider.stream(
                list(parsed.messages),
                claims.model_id,
                tools=list(parsed.tools) or None,
                config=parsed.config,
            )
            # Per response stream: the position a client associates a call's
            # fragments by, which is meaningless across streams.
            tool_call_indices: dict[str, int] = {}
            try:
                async for chunk in stream:
                    if chunk.event_type is StreamEventType.USAGE and (
                        chunk.usage is not None
                    ):
                        total = await self._ledger.add(
                            claims.execution_id, chunk.usage.cost
                        )
                        # Enforce the hard token budget mid-stream: the cost
                        # chokepoint only fires on terminal drain, so a single
                        # long stream must be cut the moment its running total
                        # crosses the ceiling rather than blowing past it.
                        if self._ceiling_crossed(claims, total):
                            logger.warning(
                                GATEWAY_BUDGET_KILL,
                                execution_id=claims.execution_id,
                                spent=total,
                                ceiling=claims.cost_ceiling,
                                surface="gateway-stream",
                            )
                            # Latch (do not reset): a reset would zero the ledger
                            # and let the same bearer respend the ceiling on its
                            # next call; the latch keeps the run rejected.
                            await self._ledger.kill(claims.execution_id, total)
                            # Signal the kill to the consumer: without a terminal
                            # error frame the stream would look like a normal stop
                            # and the harness would treat a budget cut as success.
                            yield _sse(
                                _budget_kill_chunk(
                                    response_id, created, claims.model_id
                                )
                            )
                            break
                    frame = self._stream_frame(
                        chunk, response_id, created, claims, tool_call_indices
                    )
                    if frame is not None:
                        yield frame
            except Exception as exc:
                reraise_critical(exc)
                # A provider failure after the first frame otherwise reaches
                # the client as a truncated stream with no server-side trace.
                logger.warning(
                    GATEWAY_DISPATCH_FAILED,
                    surface="gateway-stream",
                    execution_id=claims.execution_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise
            finally:
                await aclose_quietly(stream, model=claims.model_id)

    @staticmethod
    def _ceiling_crossed(claims: GatewayTokenClaims, total: float) -> bool:
        """Return whether *total* has reached the run's hard cost ceiling.

        Returns:
            ``True`` when a ceiling is set and the running total meets it.
        """
        return claims.cost_ceiling is not None and total >= claims.cost_ceiling

    def _prepare(
        self,
        token: str,
        raw_request: dict[str, object],
        registry: ProviderResolver,
        *,
        enabled: bool,
    ) -> tuple[GatewayTokenClaims, ParsedChatRequest, CompletionProvider]:
        """Verify, parse, resolve and scan before dispatch.

        Returns:
            The ``(claims, parsed_request, provider)`` triple.

        Raises:
            ServiceUnavailableError: If the gateway is disabled or the
                bound provider is unregistered.
            GatewayTokenInvalidError: If the token is invalid.
            ValidationError: If the request is malformed.
        """
        if not enabled:
            msg = "LLM gateway is disabled"
            raise ServiceUnavailableError(msg)
        try:
            claims = self._signer.verify(token)
        except GatewayTokenInvalidError:
            logger.warning(GATEWAY_TOKEN_REJECTED, surface="gateway")
            raise
        parsed = parse_chat_request(raw_request)
        provider = self._resolve_provider(registry, claims.provider)
        self._scan_inbound(parsed, claims)
        logger.info(
            GATEWAY_REQUEST_RECEIVED,
            execution_id=claims.execution_id,
            provider=claims.provider,
            model=claims.model_id,
            message_count=len(parsed.messages),
            stream=parsed.stream,
        )
        return claims, parsed, provider

    @staticmethod
    def _resolve_provider(
        registry: ProviderResolver, provider_name: str
    ) -> CompletionProvider:
        """Resolve the explicitly bound provider from the registry.

        Returns:
            The bound provider driver.

        Raises:
            ServiceUnavailableError: If no driver is registered for the
                token's bound provider (a server misconfiguration).
        """
        try:
            return registry.get(provider_name)
        except DriverNotRegisteredError as exc:
            logger.warning(GATEWAY_PROVIDER_UNAVAILABLE, provider=provider_name)
            msg = f"gateway provider {provider_name!r} is not registered"
            raise ServiceUnavailableError(msg) from exc

    async def _enforce_budget(self, claims: GatewayTokenClaims) -> None:
        """Reject the call when the run has already spent its ceiling.

        Raises:
            GatewayBudgetExhaustedError: If accumulated cost meets or
                exceeds the token's ``cost_ceiling``.
        """
        if claims.cost_ceiling is None:
            return
        msg = (
            f"run {claims.execution_id} exhausted its "
            f"{claims.cost_ceiling} cost ceiling"
        )
        # A latched-killed run stays rejected for the bearer's lifetime: the
        # ledger is never zeroed on a kill, so reusing the still-valid token
        # cannot respend the ceiling.
        if await self._ledger.is_killed(claims.execution_id):
            logger.warning(
                GATEWAY_BUDGET_KILL,
                execution_id=claims.execution_id,
                ceiling=claims.cost_ceiling,
                note="rejected reuse of a budget-killed run token",
            )
            raise GatewayBudgetExhaustedError(msg)
        spent = await self._ledger.total(claims.execution_id)
        if spent >= claims.cost_ceiling:
            logger.warning(
                GATEWAY_BUDGET_KILL,
                execution_id=claims.execution_id,
                spent=spent,
                ceiling=claims.cost_ceiling,
            )
            # Latch (do not reset): the kill must survive so the next call on
            # the same still-valid bearer is rejected rather than re-admitted.
            await self._ledger.kill(claims.execution_id, spent)
            raise GatewayBudgetExhaustedError(msg)

    async def _dispatch(
        self,
        provider: CompletionProvider,
        parsed: ParsedChatRequest,
        claims: GatewayTokenClaims,
        cost_tracker: CostTrackerProtocol | None,
    ) -> CompletionResponse:
        """Dispatch a buffered completion under a cost-recording scope.

        Returns:
            The provider completion response.
        """
        async with cost_recording_scope(
            cost_tracker=cost_tracker,
            agent_id=claims.agent_id,
            task_id=claims.task_id,
            project_id=claims.project_id,
            purpose=None,
            call_category=LLMCallCategory.PRODUCTIVE,
            currency=claims.currency,
        ):
            return await provider.complete(
                list(parsed.messages),
                claims.model_id,
                tools=list(parsed.tools) or None,
                config=parsed.config,
            )

    def _stream_frame(
        self,
        chunk: StreamChunk,
        response_id: str,
        created: int,
        claims: GatewayTokenClaims,
        tool_call_indices: dict[str, int],
    ) -> str | None:
        """Serialise one stream chunk into an SSE frame, or ``None``.

        Args:
            chunk: The provider stream chunk.
            response_id: The ``chatcmpl-*`` id, stable across the stream.
            created: Unix epoch seconds, stable across the stream.
            claims: The verified per-run token claims.
            tool_call_indices: Per-stream call id to position, extended here
                as new calls appear.

        Returns:
            An SSE ``data:`` frame, or ``None`` for chunks with no
            client-visible delta.
        """
        if chunk.event_type is StreamEventType.ERROR:
            return _sse(_error_chunk(chunk, response_id, created, claims.model_id))
        body = stream_chunk_to_openai(
            chunk,
            response_id=response_id,
            created=created,
            model=claims.model_id,
            tool_call_index=_tool_call_index(chunk, tool_call_indices),
        )
        return None if body is None else _sse(body)

    def _scan_inbound(
        self, parsed: ParsedChatRequest, claims: GatewayTokenClaims
    ) -> None:
        """Advisory injection-heuristic scan of inbound message content.

        Logs a scrubbed sample when a message matches an injection
        heuristic. Advisory only: it never blocks the request (the
        load-bearing fence is applied at the credentialed-MCP source).
        """
        for message in parsed.messages:
            if message.content is None:
                continue
            matched = scan_injection_heuristics(message.content)
            if matched is not None:
                logger.warning(
                    GATEWAY_INJECTION_SUSPECTED,
                    execution_id=claims.execution_id,
                    role=message.role,
                    pattern=matched,
                    sample=scrub_secret_tokens(
                        message.content[:_INJECTION_SAMPLE_CHARS]
                    ),
                )

    def _new_id(self) -> str:
        """Return a fresh ``chatcmpl-*`` response id.

        Returns:
            A unique response identifier.
        """
        return f"chatcmpl-{secrets.token_hex(_ID_TOKEN_BYTES)}"

    def _now_epoch(self) -> int:
        """Return the current wall-clock time as Unix epoch seconds.

        Returns:
            Integer epoch seconds.
        """
        return int(self._clock.now().timestamp())


def _tool_call_index(chunk: StreamChunk, indices: dict[str, int]) -> int | None:
    """Resolve this chunk's position among the response's tool calls.

    Assigned in first-seen order and remembered by call id, so every fragment
    of one call carries the same position and two calls never share one.

    Args:
        chunk: The provider stream chunk.
        indices: Call id to position, extended here as new calls appear.

    Returns:
        The position, or ``None`` when the chunk carries no tool call.
    """
    if chunk.tool_call_delta is None:
        return None
    call_id = chunk.tool_call_delta.id
    if call_id not in indices:
        indices[call_id] = len(indices)
    return indices[call_id]


def _sse(body: dict[str, object]) -> str:
    """Frame *body* as an SSE ``data:`` event.

    Returns:
        The SSE ``data:`` frame text.
    """
    return f"data: {json.dumps(body, separators=(',', ':'))}\n\n"


def _error_chunk(
    chunk: StreamChunk, response_id: str, created: int, model: str
) -> dict[str, object]:
    """Build an OpenAI-style error chunk from a provider error chunk.

    Returns:
        An OpenAI ``chat.completion.chunk`` carrying an ``error`` object.
    """
    return {
        "id": response_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        "error": {
            "message": chunk.error_message or "stream error",
            "type": "gateway_stream_error",
        },
    }


def _budget_kill_chunk(response_id: str, created: int, model: str) -> dict[str, object]:
    """Build the terminal chunk emitted when a run's cost ceiling is hit.

    The buffered path surfaces budget exhaustion as a hard ``402`` error; the
    streaming path must give the consuming harness an equally unambiguous
    signal rather than a truncated-but-otherwise-normal stream, so it carries
    both a ``finish_reason="length"`` and an explicit ``error`` object.

    Returns:
        An OpenAI ``chat.completion.chunk`` marking a budget-exhaustion kill.
    """
    return {
        "id": response_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "length"}],
        "error": {
            "message": "run cost ceiling exceeded; stream terminated",
            "type": "gateway_budget_exhausted",
        },
    }
