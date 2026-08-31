"""Unit tests for the per-run streaming-enablement resolution.

``AgentEngineRunMixin._resolve_streaming_enabled`` gates the streaming work
loop on the live ``engine.work_loop_streaming_enabled`` setting AND the
model's ``supports_streaming`` capability, failing safe to the non-streaming
path when the resolver is absent-then-unsupported or the capability lookup
faults. These branches drive whether a run streams at all, so each is pinned.
"""

from datetime import date
from typing import cast
from unittest.mock import AsyncMock

import pytest

from synthorg.core.agent import (
    AgentIdentity,
    ModelConfig,
    ToolPermissions,
)
from synthorg.engine._agent_engine_run import AgentEngineRunMixin
from synthorg.providers.capabilities import ModelCapabilities
from synthorg.providers.protocol import CompletionProvider
from synthorg.settings import kill_switch
from synthorg.settings.resolver import ConfigResolver
from tests._shared import mock_of
from tests._shared.ids import as_uuid

pytestmark = pytest.mark.unit

_MODEL_ID = "example-capable-001"


def _identity() -> AgentIdentity:
    return AgentIdentity(
        id=as_uuid("stream-agent"),
        name="Streamer",
        role="Engineer",
        department="Engineering",
        hiring_date=date(2026, 1, 1),
        model=ModelConfig(provider="example-provider", model_id=_MODEL_ID),
        tools=ToolPermissions(),
    )


def _caps(*, supports_streaming: bool) -> ModelCapabilities:
    return ModelCapabilities(
        model_id=_MODEL_ID,
        provider="example-provider",
        max_context_tokens=200_000,
        supports_streaming=supports_streaming,
    )


def _provider(
    *,
    supports_streaming: bool = True,
    raises: Exception | None = None,
) -> CompletionProvider:
    get_caps = AsyncMock(
        spec=CompletionProvider.get_model_capabilities,
        side_effect=raises,
        return_value=None if raises else _caps(supports_streaming=supports_streaming),
    )
    return cast(
        CompletionProvider,
        mock_of[CompletionProvider](get_model_capabilities=get_caps),
    )


class _StreamingEngine(AgentEngineRunMixin):
    """Minimal mixin instance carrying only the resolver the method reads."""

    def __init__(self, config_resolver: ConfigResolver | None) -> None:
        self._config_resolver = config_resolver


async def _resolve(engine: _StreamingEngine, provider: CompletionProvider) -> bool:
    return await engine._resolve_streaming_enabled(
        provider, _identity(), task_id="task-1"
    )


class TestResolveStreamingEnabled:
    async def test_streams_when_resolver_absent_and_model_supports(self) -> None:
        provider = _provider(supports_streaming=True)
        assert await _resolve(_StreamingEngine(None), provider) is True

    async def test_no_stream_when_model_lacks_support(self) -> None:
        provider = _provider(supports_streaming=False)
        assert await _resolve(_StreamingEngine(None), provider) is False

    async def test_no_stream_when_setting_disabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def disabled(**_kwargs: object) -> bool:
            return False

        monkeypatch.setattr(kill_switch, "resolve_bool_with_fallback", disabled)
        provider = _provider(supports_streaming=True)
        resolver = cast(ConfigResolver, object())

        assert await _resolve(_StreamingEngine(resolver), provider) is False
        # Short-circuits before the capability lookup when the setting is off.
        cast(AsyncMock, provider.get_model_capabilities).assert_not_awaited()

    async def test_streams_when_setting_enabled_and_supported(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def enabled(**_kwargs: object) -> bool:
            return True

        monkeypatch.setattr(kill_switch, "resolve_bool_with_fallback", enabled)
        provider = _provider(supports_streaming=True)
        resolver = cast(ConfigResolver, object())

        assert await _resolve(_StreamingEngine(resolver), provider) is True

    async def test_fails_safe_when_capability_lookup_raises(self) -> None:
        provider = _provider(raises=ConnectionError("model info unreachable"))
        assert await _resolve(_StreamingEngine(None), provider) is False
