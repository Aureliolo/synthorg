"""Unit tests for ``CassetteCompletionProvider``.

The wrapper is the seam that makes "byte-identical replay with zero
real LLM calls" true. These tests pin: record delegates and persists;
replay serves the recorded outcome without ever touching the inner
driver; ``provider_metadata`` survives byte-identically (the
clobber guard); errors, streams and capabilities all round-trip; and
the unreachable ``_do_*`` guards fail loudly.
"""

from collections.abc import AsyncIterator
from pathlib import Path
from typing import override

import pytest

from synthorg.providers.base import BaseCompletionProvider
from synthorg.providers.capabilities import ModelCapabilities
from synthorg.providers.cassette.errors import (
    CassetteInternalError,
    CassetteReplayMissError,
)
from synthorg.providers.cassette.mode import CassetteMode
from synthorg.providers.cassette.provider import CassetteCompletionProvider
from synthorg.providers.cassette.redaction import NullRedactor
from synthorg.providers.cassette.store import CassetteSession
from synthorg.providers.drivers.scripted import (
    ScriptedDriver,
    SequencedResponseStrategy,
    SingleResponseStrategy,
)
from synthorg.providers.enums import (
    FinishReason,
    MessageRole,
    StreamEventType,
)
from synthorg.providers.errors import RateLimitError
from synthorg.providers.models import (
    ChatMessage,
    CompletionConfig,
    CompletionResponse,
    StreamChunk,
    TokenUsage,
    ToolDefinition,
)

pytestmark = pytest.mark.unit

_PROVIDER = "testprov"


def _msgs(text: str = "hello") -> list[ChatMessage]:
    return [ChatMessage(role=MessageRole.USER, content=text)]


def _response(text: str) -> CompletionResponse:
    return CompletionResponse(
        content=text,
        finish_reason=FinishReason.STOP,
        usage=TokenUsage(input_tokens=2, output_tokens=4, cost=0.02),
        model="m",
    )


class _RaisingInner(BaseCompletionProvider):
    """Inner driver whose every hook explodes if reached in replay."""

    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    @override
    async def _do_complete(
        self,
        messages: list[ChatMessage],
        model: str,
        *,
        tools: list[ToolDefinition] | None = None,
        config: CompletionConfig | None = None,
    ) -> CompletionResponse:
        del messages, model, tools, config
        self.calls += 1
        msg = "inner provider must not be called in replay"
        raise AssertionError(msg)

    @override
    async def _do_stream(
        self,
        messages: list[ChatMessage],
        model: str,
        *,
        tools: list[ToolDefinition] | None = None,
        config: CompletionConfig | None = None,
    ) -> AsyncIterator[StreamChunk]:
        del messages, model, tools, config
        self.calls += 1
        msg = "inner provider must not be called in replay"
        raise AssertionError(msg)

    @override
    async def _do_get_model_capabilities(
        self,
        model: str,
    ) -> ModelCapabilities:
        del model
        self.calls += 1
        msg = "inner provider must not be called in replay"
        raise AssertionError(msg)


def _record_session(path: Path) -> CassetteSession:
    return CassetteSession(
        mode=CassetteMode.RECORD,
        path=path,
        redactor=NullRedactor(),
    )


def _replay_session(path: Path) -> CassetteSession:
    return CassetteSession(
        mode=CassetteMode.REPLAY,
        path=path,
        redactor=NullRedactor(),
    )


class TestCompleteRoundTrip:
    """Record then replay a completion."""

    async def test_replay_is_byte_identical_with_no_inner(self, tmp_path: Path) -> None:
        path = tmp_path / "c.json"
        inner = ScriptedDriver(
            _PROVIDER,
            strategy=SingleResponseStrategy(response=_response("the answer")),
        )
        rec = CassetteCompletionProvider(
            inner=inner,
            session=_record_session(path),
            provider_name=_PROVIDER,
        )
        recorded = await rec.complete(_msgs(), "m")
        await rec._session.flush()

        # Pure replay: NO inner driver constructed at all.
        rep = CassetteCompletionProvider(
            inner=None,
            session=_replay_session(path),
            provider_name=_PROVIDER,
        )
        replayed = await rep.complete(_msgs(), "m")
        assert replayed.model_dump(mode="json") == recorded.model_dump(mode="json")

    async def test_provider_metadata_preserved_byte_identically(
        self, tmp_path: Path
    ) -> None:
        """The clobber guard: recorded ``_synthorg_*`` is not re-stamped."""
        path = tmp_path / "c.json"
        inner = ScriptedDriver(
            _PROVIDER,
            strategy=SingleResponseStrategy(response=_response("x")),
        )
        rec = CassetteCompletionProvider(
            inner=inner,
            session=_record_session(path),
            provider_name=_PROVIDER,
        )
        recorded = await rec.complete(_msgs(), "m")
        await rec._session.flush()
        # The inner ran through BaseCompletionProvider, so latency
        # metadata was injected; it must survive replay verbatim.
        assert "_synthorg_latency_ms" in recorded.provider_metadata

        rep = CassetteCompletionProvider(
            inner=None,
            session=_replay_session(path),
            provider_name=_PROVIDER,
        )
        replayed = await rep.complete(_msgs(), "m")
        assert replayed.provider_metadata == recorded.provider_metadata

    async def test_replay_miss_never_touches_inner(self, tmp_path: Path) -> None:
        path = tmp_path / "c.json"
        await _record_session(path).flush()
        spy = _RaisingInner()
        rep = CassetteCompletionProvider(
            inner=spy,
            session=_replay_session(path),
            provider_name=_PROVIDER,
        )
        with pytest.raises(CassetteReplayMissError):
            await rep.complete(_msgs("unrecorded"), "m")
        assert spy.calls == 0


class TestErrorReplay:
    """A recorded provider error re-raises on replay."""

    async def test_recorded_rate_limit_error_replays(self, tmp_path: Path) -> None:
        path = tmp_path / "c.json"
        inner = ScriptedDriver(
            _PROVIDER,
            strategy=SingleResponseStrategy(
                error=RateLimitError("slow down"),
            ),
        )
        rec = CassetteCompletionProvider(
            inner=inner,
            session=_record_session(path),
            provider_name=_PROVIDER,
        )
        with pytest.raises(RateLimitError):
            await rec.complete(_msgs(), "m")
        await rec._session.flush()

        spy = _RaisingInner()
        rep = CassetteCompletionProvider(
            inner=spy,
            session=_replay_session(path),
            provider_name=_PROVIDER,
        )
        with pytest.raises(RateLimitError):
            await rep.complete(_msgs(), "m")
        assert spy.calls == 0


class TestStreamRoundTrip:
    """Record then replay a stream; chunks identical and ordered."""

    async def test_stream_chunks_replay_identically(self, tmp_path: Path) -> None:
        path = tmp_path / "c.json"
        inner = ScriptedDriver(
            _PROVIDER,
            strategy=SingleResponseStrategy(response=_response("streamed")),
        )
        rec = CassetteCompletionProvider(
            inner=inner,
            session=_record_session(path),
            provider_name=_PROVIDER,
        )
        recorded_stream = await rec.stream(_msgs(), "m")
        recorded = [c async for c in recorded_stream]
        await rec._session.flush()

        rep = CassetteCompletionProvider(
            inner=None,
            session=_replay_session(path),
            provider_name=_PROVIDER,
        )
        replayed_stream = await rep.stream(_msgs(), "m")
        replayed = [c async for c in replayed_stream]
        assert [c.model_dump(mode="json") for c in replayed] == [
            c.model_dump(mode="json") for c in recorded
        ]
        assert recorded[-1].event_type is StreamEventType.DONE


class TestCapabilitiesRoundTrip:
    """Record then replay capability lookups."""

    async def test_single_and_batch_replay(self, tmp_path: Path) -> None:
        path = tmp_path / "c.json"
        inner = ScriptedDriver(_PROVIDER)
        rec = CassetteCompletionProvider(
            inner=inner,
            session=_record_session(path),
            provider_name=_PROVIDER,
        )
        single = await rec.get_model_capabilities("m1")
        batch = await rec.batch_get_capabilities(("m1", "m2"))
        await rec._session.flush()

        spy = _RaisingInner()
        rep = CassetteCompletionProvider(
            inner=spy,
            session=_replay_session(path),
            provider_name=_PROVIDER,
        )
        rep_single = await rep.get_model_capabilities("m1")
        rep_batch = await rep.batch_get_capabilities(("m1", "m2"))
        assert spy.calls == 0
        assert rep_single.model_dump() == single.model_dump()
        rep_m2 = rep_batch["m2"]
        rec_m2 = batch["m2"]
        assert rep_m2 is not None
        assert rec_m2 is not None
        assert rep_m2.model_dump() == rec_m2.model_dump()

    async def test_batch_miss_propagates_not_swallowed(self, tmp_path: Path) -> None:
        """A replay miss must not degrade to a ``None`` entry."""
        path = tmp_path / "c.json"
        rec = CassetteCompletionProvider(
            inner=ScriptedDriver(_PROVIDER),
            session=_record_session(path),
            provider_name=_PROVIDER,
        )
        await rec.get_model_capabilities("only")
        await rec._session.flush()

        rep = CassetteCompletionProvider(
            inner=None,
            session=_replay_session(path),
            provider_name=_PROVIDER,
        )
        with pytest.raises(CassetteReplayMissError):
            await rep.batch_get_capabilities(("only", "absent"))


class TestUnreachableHooks:
    """The ``_do_*`` guards are unreachable; they fail loudly if hit."""

    async def test_do_hooks_raise_internal_error(self) -> None:
        path = Path("unused")
        wrapper = CassetteCompletionProvider(
            inner=None,
            session=CassetteSession(
                mode=CassetteMode.RECORD,
                path=path,
                redactor=NullRedactor(),
            ),
            provider_name=_PROVIDER,
        )
        with pytest.raises(CassetteInternalError):
            await wrapper._do_complete(_msgs(), "m")
        with pytest.raises(CassetteInternalError):
            await wrapper._do_stream(_msgs(), "m")
        with pytest.raises(CassetteInternalError):
            await wrapper._do_get_model_capabilities("m")


class TestSequencedReplayOrder:
    """Repeated identical requests replay in recorded FIFO order."""

    async def test_same_request_twice_replays_in_order(self, tmp_path: Path) -> None:
        path = tmp_path / "c.json"
        inner = ScriptedDriver(
            _PROVIDER,
            strategy=SequencedResponseStrategy(
                (_response("first"), _response("second")),
            ),
        )
        rec = CassetteCompletionProvider(
            inner=inner,
            session=_record_session(path),
            provider_name=_PROVIDER,
        )
        await rec.complete(_msgs("same"), "m")
        await rec.complete(_msgs("same"), "m")
        await rec._session.flush()

        rep = CassetteCompletionProvider(
            inner=None,
            session=_replay_session(path),
            provider_name=_PROVIDER,
        )
        first = await rep.complete(_msgs("same"), "m")
        second = await rep.complete(_msgs("same"), "m")
        assert first.content == "first"
        assert second.content == "second"
