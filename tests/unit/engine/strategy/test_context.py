"""Unit tests for strategic context providers."""

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from typeguard import suppress_type_checks

from synthorg.core.enums import MemoryCategory
from synthorg.core.types import NotBlankStr
from synthorg.engine.strategy.context import (
    CompositeContextProvider,
    ConfigContextProvider,
    MemoryContextProvider,
    build_context,
)
from synthorg.engine.strategy.models import (
    ContextSource,
    StrategicContext,
    StrategicContextConfig,
    StrategyConfig,
)
from synthorg.memory.models import MemoryEntry, MemoryMetadata
from synthorg.memory.protocol import MemoryBackend


def _entry(content: str) -> MemoryEntry:
    """Build a MemoryEntry whose content is the JSON override payload."""
    return MemoryEntry(
        id=NotBlankStr("entry-1"),
        agent_id=NotBlankStr("system:strategy"),
        category=MemoryCategory.SEMANTIC,
        content=NotBlankStr(content),
        metadata=MemoryMetadata(tags=(NotBlankStr("strategic-context"),)),
        created_at=datetime(2026, 5, 9, 12, 0, 0, tzinfo=UTC),
    )


class TestConfigContextProvider:
    """Tests for ConfigContextProvider."""

    @pytest.mark.unit
    async def test_reads_from_config(
        self, default_strategy_config: StrategyConfig
    ) -> None:
        provider = ConfigContextProvider()
        ctx = await provider.provide(config=default_strategy_config)
        assert ctx.maturity_stage == "growth"
        assert ctx.industry == "technology"
        assert ctx.competitive_position == "challenger"

    @pytest.mark.unit
    async def test_custom_config(self) -> None:
        config = StrategyConfig(
            context=StrategicContextConfig(
                maturity_stage="seed",
                industry="fintech",
                competitive_position="niche",
            ),
        )
        provider = ConfigContextProvider()
        ctx = await provider.provide(config=config)
        assert ctx.maturity_stage == "seed"
        assert ctx.industry == "fintech"
        assert ctx.competitive_position == "niche"


class TestMemoryContextProvider:
    """Tests for MemoryContextProvider memory-driven overrides."""

    @pytest.mark.unit
    async def test_falls_back_when_no_memory_backend(
        self,
        default_strategy_config: StrategyConfig,
    ) -> None:
        fallback = ConfigContextProvider()
        provider = MemoryContextProvider(fallback=fallback)
        ctx = await provider.provide(config=default_strategy_config)
        assert ctx.maturity_stage == "growth"

    @pytest.mark.unit
    async def test_falls_back_when_backend_returns_no_entries(
        self,
        default_strategy_config: StrategyConfig,
    ) -> None:
        backend = AsyncMock(spec=MemoryBackend)
        backend.retrieve.return_value = ()
        provider = MemoryContextProvider(
            fallback=ConfigContextProvider(),
            memory_backend=backend,
        )
        ctx = await provider.provide(config=default_strategy_config)
        assert ctx.maturity_stage == "growth"
        backend.retrieve.assert_called_once()

    @pytest.mark.unit
    async def test_falls_back_when_backend_raises(
        self,
        default_strategy_config: StrategyConfig,
    ) -> None:
        backend = AsyncMock(spec=MemoryBackend)
        backend.retrieve.side_effect = RuntimeError("backend unavailable")
        provider = MemoryContextProvider(
            fallback=ConfigContextProvider(),
            memory_backend=backend,
        )
        ctx = await provider.provide(config=default_strategy_config)
        assert ctx.maturity_stage == "growth"

    @pytest.mark.unit
    async def test_falls_back_when_content_is_not_json(
        self,
        default_strategy_config: StrategyConfig,
    ) -> None:
        backend = AsyncMock(spec=MemoryBackend)
        backend.retrieve.return_value = (_entry("not-json-at-all"),)
        provider = MemoryContextProvider(
            fallback=ConfigContextProvider(),
            memory_backend=backend,
        )
        ctx = await provider.provide(config=default_strategy_config)
        assert ctx.maturity_stage == "growth"

    @pytest.mark.unit
    async def test_falls_back_when_content_is_not_object(
        self,
        default_strategy_config: StrategyConfig,
    ) -> None:
        backend = AsyncMock(spec=MemoryBackend)
        backend.retrieve.return_value = (_entry(json.dumps(["scaleup"])),)
        provider = MemoryContextProvider(
            fallback=ConfigContextProvider(),
            memory_backend=backend,
        )
        with suppress_type_checks():
            ctx = await provider.provide(config=default_strategy_config)
        assert ctx.maturity_stage == "growth"

    @pytest.mark.unit
    async def test_applies_full_overrides(
        self,
        default_strategy_config: StrategyConfig,
    ) -> None:
        payload = {
            "maturity_stage": "scaleup",
            "industry": "fintech",
            "competitive_position": "leader",
        }
        backend = AsyncMock(spec=MemoryBackend)
        backend.retrieve.return_value = (_entry(json.dumps(payload)),)
        provider = MemoryContextProvider(
            fallback=ConfigContextProvider(),
            memory_backend=backend,
        )
        ctx = await provider.provide(config=default_strategy_config)
        assert ctx.maturity_stage == "scaleup"
        assert ctx.industry == "fintech"
        assert ctx.competitive_position == "leader"

    @pytest.mark.unit
    async def test_applies_partial_overrides(
        self,
        default_strategy_config: StrategyConfig,
    ) -> None:
        # Only ``maturity_stage`` is overridden; the other fields keep
        # the fallback config values.
        payload = {"maturity_stage": "scaleup"}
        backend = AsyncMock(spec=MemoryBackend)
        backend.retrieve.return_value = (_entry(json.dumps(payload)),)
        provider = MemoryContextProvider(
            fallback=ConfigContextProvider(),
            memory_backend=backend,
        )
        ctx = await provider.provide(config=default_strategy_config)
        assert ctx.maturity_stage == "scaleup"
        assert ctx.industry == "technology"
        assert ctx.competitive_position == "challenger"

    @pytest.mark.unit
    async def test_falls_back_when_any_field_is_blank_or_non_string(
        self,
        default_strategy_config: StrategyConfig,
    ) -> None:
        # Strict args-model validation rejects the whole payload as soon
        # as any field is blank or non-string, so even the otherwise-
        # valid ``competitive_position`` is dropped.  This is the right
        # contract for boundary validation: garbage in -> total fall
        # back, no partial application.
        payload = {
            "maturity_stage": "   ",
            "industry": 42,
            "competitive_position": "leader",
        }
        backend = AsyncMock(spec=MemoryBackend)
        backend.retrieve.return_value = (_entry(json.dumps(payload)),)
        provider = MemoryContextProvider(
            fallback=ConfigContextProvider(),
            memory_backend=backend,
        )
        ctx = await provider.provide(config=default_strategy_config)
        assert ctx.maturity_stage == "growth"
        assert ctx.industry == "technology"
        assert ctx.competitive_position == "challenger"

    @pytest.mark.unit
    async def test_ignores_unknown_payload_fields(
        self,
        default_strategy_config: StrategyConfig,
    ) -> None:
        # Forward-compatible: extra fields the args model doesn't know
        # about are dropped silently, the rest still apply.
        payload = {
            "maturity_stage": "scaleup",
            "future_field": {"experimental": True},
        }
        backend = AsyncMock(spec=MemoryBackend)
        backend.retrieve.return_value = (_entry(json.dumps(payload)),)
        provider = MemoryContextProvider(
            fallback=ConfigContextProvider(),
            memory_backend=backend,
        )
        ctx = await provider.provide(config=default_strategy_config)
        assert ctx.maturity_stage == "scaleup"
        assert ctx.industry == "technology"


class TestCompositeContextProvider:
    """Tests for CompositeContextProvider."""

    @pytest.mark.unit
    async def test_returns_first_success(
        self,
        default_strategy_config: StrategyConfig,
    ) -> None:
        provider = CompositeContextProvider(
            providers=(ConfigContextProvider(),),
        )
        ctx = await provider.provide(config=default_strategy_config)
        assert ctx.maturity_stage == "growth"

    @pytest.mark.unit
    def test_empty_providers_raises(self) -> None:
        with pytest.raises(ValueError, match="at least one"):
            CompositeContextProvider(providers=())

    @pytest.mark.unit
    async def test_falls_back_on_first_provider_failure(
        self,
        default_strategy_config: StrategyConfig,
    ) -> None:
        """When first provider fails, second provider is used."""

        class FailingProvider:
            async def provide(self, *, config: StrategyConfig) -> StrategicContext:
                msg = "provider failed"
                raise RuntimeError(msg)

        provider = CompositeContextProvider(
            providers=(FailingProvider(), ConfigContextProvider()),
        )
        ctx = await provider.provide(config=default_strategy_config)
        assert ctx.maturity_stage == "growth"

    @pytest.mark.unit
    async def test_all_providers_fail_raises_runtime_error(
        self,
        default_strategy_config: StrategyConfig,
    ) -> None:
        """When all providers fail, RuntimeError is raised."""

        class FailingProvider:
            async def provide(self, *, config: StrategyConfig) -> StrategicContext:
                msg = "provider failed"
                raise RuntimeError(msg)

        provider = CompositeContextProvider(
            providers=(FailingProvider(), FailingProvider()),
        )
        with pytest.raises(RuntimeError, match="All context providers failed"):
            await provider.provide(config=default_strategy_config)


class TestBuildContext:
    """Tests for the build_context convenience factory."""

    @pytest.mark.unit
    async def test_config_source(self) -> None:
        config = StrategyConfig(
            context=StrategicContextConfig(source=ContextSource.CONFIG),
        )
        ctx = await build_context(config)
        assert ctx.maturity_stage == "growth"

    @pytest.mark.unit
    async def test_memory_source_without_backend_falls_back(self) -> None:
        config = StrategyConfig(
            context=StrategicContextConfig(source=ContextSource.MEMORY),
        )
        ctx = await build_context(config)
        # Without a memory backend the provider degrades to config.
        assert ctx.maturity_stage == "growth"

    @pytest.mark.unit
    async def test_memory_source_with_backend_applies_overrides(self) -> None:
        config = StrategyConfig(
            context=StrategicContextConfig(source=ContextSource.MEMORY),
        )
        backend = AsyncMock(spec=MemoryBackend)
        backend.retrieve.return_value = (
            _entry(json.dumps({"maturity_stage": "scaleup"})),
        )
        ctx = await build_context(config, memory_backend=backend)
        assert ctx.maturity_stage == "scaleup"

    @pytest.mark.unit
    async def test_composite_source_without_backend_falls_back(self) -> None:
        config = StrategyConfig(
            context=StrategicContextConfig(source=ContextSource.COMPOSITE),
        )
        ctx = await build_context(config)
        assert ctx.maturity_stage == "growth"

    @pytest.mark.unit
    async def test_composite_source_with_backend_applies_overrides(self) -> None:
        # Pins the COMPOSITE branch end-to-end with a real backend so a
        # regression in ``CompositeContextProvider`` (e.g. wrapping the
        # wrong fallback chain) fails this test rather than slipping
        # through under the no-backend degraded path.
        config = StrategyConfig(
            context=StrategicContextConfig(source=ContextSource.COMPOSITE),
        )
        backend = AsyncMock(spec=MemoryBackend)
        backend.retrieve.return_value = (
            _entry(json.dumps({"maturity_stage": "scaleup"})),
        )
        ctx = await build_context(config, memory_backend=backend)
        assert ctx.maturity_stage == "scaleup"
        assert ctx.industry == "technology"
