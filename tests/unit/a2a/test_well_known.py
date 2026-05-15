"""Tests for well-known Agent Card cache helpers."""

from collections.abc import Iterator
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from synthorg.a2a import well_known
from synthorg.a2a.agent_card import AgentCardBuilder
from synthorg.a2a.well_known import (
    WellKnownAgentCardController,
    _agent_fingerprint,
    _assemble_company_card,
    _build_agent_card_payload,
    _get_cached_card,
    _put_cached_card,
    _resolve_agent_for_card,
    _resolve_company_name,
)
from synthorg.core.domain_errors import NotFoundError
from synthorg.hr.registry import AgentRegistryService
from synthorg.settings.errors import SettingNotFoundError
from synthorg.settings.resolver import ConfigResolver


class TestCacheHelpers:
    """Well-known Agent Card caching."""

    @pytest.mark.unit
    async def test_put_and_get(self) -> None:
        """Stored card data is retrievable."""
        await _put_cached_card("key-1", {"name": "test"}, ttl=60)
        result = await _get_cached_card("key-1", ttl=60)
        assert result == {"name": "test"}

    @pytest.mark.unit
    async def test_get_missing_key(self) -> None:
        """Missing key returns None."""
        result = await _get_cached_card("nonexistent", ttl=60)
        assert result is None

    @pytest.mark.unit
    async def test_ttl_zero_disables_caching(self) -> None:
        """TTL=0 disables caching (put is a no-op)."""
        await _put_cached_card("key-1", {"name": "test"}, ttl=0)
        result = await _get_cached_card("key-1", ttl=0)
        assert result is None

    @pytest.mark.unit
    async def test_host_scoped_keys_are_isolated(self) -> None:
        """Different host keys don't interfere."""
        await _put_cached_card(
            "__company__:https://host-a",
            {"host": "a"},
            ttl=60,
        )
        await _put_cached_card(
            "__company__:https://host-b",
            {"host": "b"},
            ttl=60,
        )
        a = await _get_cached_card("__company__:https://host-a", ttl=60)
        b = await _get_cached_card("__company__:https://host-b", ttl=60)
        assert a == {"host": "a"}
        assert b == {"host": "b"}

    @pytest.mark.unit
    async def test_expired_entry_returns_none(self) -> None:
        """Expired cache entry is evicted and returns None."""
        import time
        from unittest.mock import patch

        await _put_cached_card("key-1", {"name": "old"}, ttl=1)
        # Fast-forward monotonic time past TTL
        with patch.object(
            time,
            "monotonic",
            return_value=time.monotonic() + 10,
        ):
            result = await _get_cached_card("key-1", ttl=1)
        assert result is None

    @pytest.mark.unit
    async def test_fingerprint_invalidates_stale_cache(self) -> None:
        """Changed fingerprint invalidates cached entry."""
        await _put_cached_card(
            "agent-1",
            {"name": "v1"},
            ttl=60,
            fingerprint="fp-original",
        )
        # Same fingerprint: cache hit
        hit = await _get_cached_card(
            "agent-1",
            ttl=60,
            fingerprint="fp-original",
        )
        assert hit == {"name": "v1"}
        # Different fingerprint: cache miss (stale)
        miss = await _get_cached_card(
            "agent-1",
            ttl=60,
            fingerprint="fp-changed",
        )
        assert miss is None

    @pytest.mark.unit
    async def test_fingerprint_not_checked_when_empty(self) -> None:
        """Empty fingerprint on get skips staleness check."""
        await _put_cached_card(
            "key-1",
            {"name": "test"},
            ttl=60,
            fingerprint="some-fp",
        )
        # No fingerprint on get: always returns if within TTL
        result = await _get_cached_card("key-1", ttl=60)
        assert result == {"name": "test"}


def _make_resolver_stub(*, get_str_side_effect: object = None) -> AsyncMock:
    """Return an ``AsyncMock(spec=ConfigResolver)`` with ``get_str`` configured.

    ``get_str_side_effect`` can be a plain return value or an exception
    instance. When it is an Exception, it is wired as ``side_effect``;
    otherwise it is the ``return_value``.
    """
    resolver = AsyncMock(spec=ConfigResolver)
    if isinstance(get_str_side_effect, BaseException):
        resolver.get_str.side_effect = get_str_side_effect
    else:
        resolver.get_str.return_value = get_str_side_effect
    return resolver


class TestResolveCompanyName:
    """`_resolve_company_name` reads through ConfigResolver with snapshot fallback."""

    @pytest.mark.unit
    async def test_resolver_value_used_on_happy_path(self) -> None:
        """Resolver value beats the boot snapshot when present."""
        resolver = _make_resolver_stub(get_str_side_effect="Resolved Co")
        config = SimpleNamespace(company_name="Snapshot Co")
        app_state = SimpleNamespace(config_resolver=resolver, config=config)
        assert await _resolve_company_name(app_state) == "Resolved Co"
        resolver.get_str.assert_awaited_once_with("company", "company_name")

    @pytest.mark.unit
    async def test_setting_not_found_falls_back_to_snapshot(self) -> None:
        """A missing registered key falls back to the boot snapshot quietly."""
        resolver = _make_resolver_stub(
            get_str_side_effect=SettingNotFoundError("missing"),
        )
        config = SimpleNamespace(company_name="Snapshot Co")
        app_state = SimpleNamespace(config_resolver=resolver, config=config)
        assert await _resolve_company_name(app_state) == "Snapshot Co"

    @pytest.mark.unit
    async def test_unexpected_resolver_failure_falls_back_to_snapshot(self) -> None:
        """A persistence-backend outage logs and falls back to the snapshot."""
        resolver = _make_resolver_stub(
            get_str_side_effect=ConnectionError("db down"),
        )
        config = SimpleNamespace(company_name="Snapshot Co")
        app_state = SimpleNamespace(config_resolver=resolver, config=config)
        assert await _resolve_company_name(app_state) == "Snapshot Co"

    @pytest.mark.unit
    async def test_memory_error_propagates(self) -> None:
        """``MemoryError`` is re-raised, not swallowed by the fallback."""
        resolver = _make_resolver_stub(get_str_side_effect=MemoryError("oom"))
        config = SimpleNamespace(company_name="Snapshot Co")
        app_state = SimpleNamespace(config_resolver=resolver, config=config)
        with pytest.raises(MemoryError):
            await _resolve_company_name(app_state)

    @pytest.mark.unit
    async def test_recursion_error_propagates(self) -> None:
        """``RecursionError`` is re-raised, not swallowed by the fallback."""
        resolver = _make_resolver_stub(
            get_str_side_effect=RecursionError("too deep"),
        )
        config = SimpleNamespace(company_name="Snapshot Co")
        app_state = SimpleNamespace(config_resolver=resolver, config=config)
        with pytest.raises(RecursionError):
            await _resolve_company_name(app_state)


@pytest.fixture(autouse=True)
def _clear_card_cache() -> Iterator[None]:
    """Reset the module-level card cache around every test in this module."""
    well_known._card_cache.clear()
    yield
    well_known._card_cache.clear()


def _make_identity(
    *,
    agent_id: str = "agent-1",
    name: str = "Ada",
    role: str = "engineer",
    skills: tuple[str, ...] = ("python",),
    department: str = "RnD",
) -> SimpleNamespace:
    """Build an attribute-bag agent identity for card tests."""
    return SimpleNamespace(
        id=agent_id,
        name=name,
        role=role,
        skills=skills,
        department=department,
    )


def _make_app_state(
    *,
    registry: AsyncMock,
    builder: Mock,
    ttl: int = 60,
    company_name: str = "Snapshot Co",
    resolver: AsyncMock | None = None,
) -> SimpleNamespace:
    """Assemble the ``app_state`` attribute-bag the controller reads."""
    config = SimpleNamespace(
        a2a=SimpleNamespace(agent_card_cache_ttl_seconds=ttl),
        company_name=company_name,
    )
    return SimpleNamespace(
        agent_registry=registry,
        a2a_card_builder=builder,
        config=config,
        config_resolver=resolver,
    )


def _make_request(base_url: str = "http://test.example/") -> SimpleNamespace:
    """Minimal request stand-in exposing ``base_url``."""
    return SimpleNamespace(base_url=base_url)


def _state(app_state: SimpleNamespace) -> dict[str, object]:
    """Litestar ``State`` is a mapping; the handler only does ``state[...]``."""
    return {"app_state": app_state}


def _controller() -> WellKnownAgentCardController:
    """Construct the controller without mounting it on a real Router."""
    return WellKnownAgentCardController(
        owner=WellKnownAgentCardController,  # type: ignore[arg-type]
    )


class TestAgentCardHelpers:
    """Resolve / fingerprint / build split for the per-agent path."""

    @pytest.mark.unit
    async def test_resolve_prefers_id_then_name(self) -> None:
        """``get`` wins; ``get_by_name`` is the fallback."""
        identity = _make_identity()
        registry = AsyncMock(spec=AgentRegistryService)
        registry.get.return_value = identity
        app_state = SimpleNamespace(agent_registry=registry)
        assert await _resolve_agent_for_card(app_state, "agent-1") is identity
        registry.get_by_name.assert_not_awaited()

    @pytest.mark.unit
    async def test_resolve_falls_back_to_name(self) -> None:
        """A miss on ``get`` retries via ``get_by_name``."""
        identity = _make_identity()
        registry = AsyncMock(spec=AgentRegistryService)
        registry.get.return_value = None
        registry.get_by_name.return_value = identity
        app_state = SimpleNamespace(agent_registry=registry)
        assert await _resolve_agent_for_card(app_state, "Ada") is identity

    @pytest.mark.unit
    async def test_resolve_returns_none_when_unknown(self) -> None:
        """Both lookups missing yields ``None`` (caller maps to 404)."""
        registry = AsyncMock(spec=AgentRegistryService)
        registry.get.return_value = None
        registry.get_by_name.return_value = None
        app_state = SimpleNamespace(agent_registry=registry)
        assert await _resolve_agent_for_card(app_state, "ghost") is None

    @pytest.mark.unit
    def test_fingerprint_changes_with_identity(self) -> None:
        """Name/role/skill edits each change the fingerprint."""
        base = _agent_fingerprint(_make_identity())
        assert base == _agent_fingerprint(_make_identity())
        assert _agent_fingerprint(_make_identity(name="Grace")) != base
        assert _agent_fingerprint(_make_identity(role="lead")) != base
        assert _agent_fingerprint(_make_identity(skills=("go",))) != base

    @pytest.mark.unit
    def test_build_payload_uses_builder(self) -> None:
        """The payload is the builder's card ``model_dump``."""
        identity = _make_identity()
        builder = Mock(spec=AgentCardBuilder)
        builder.build.return_value = SimpleNamespace(
            model_dump=lambda: {"name": "Ada"},
        )
        payload = _build_agent_card_payload(
            SimpleNamespace(a2a_card_builder=builder),
            identity,
            "http://test.example",
        )
        assert payload == {"name": "Ada"}
        builder.build.assert_called_once_with(
            identity=identity,
            base_url="http://test.example/api/v1/a2a",
        )


class TestCompanyAgentCardEndpoint:
    """`company_agent_card` cache / build / failure behaviour."""

    @pytest.mark.unit
    async def test_miss_then_hit(self) -> None:
        """First call assembles + caches; second call serves the cache."""
        registry = AsyncMock(spec=AgentRegistryService)
        registry.list_active.return_value = (_make_identity(),)
        builder = Mock(spec=AgentCardBuilder)
        builder.build_company_card.return_value = SimpleNamespace(
            model_dump=lambda: {"name": "Snapshot Co"},
        )
        resolver = AsyncMock(spec=ConfigResolver)
        resolver.get_str.return_value = "Snapshot Co"
        app_state = _make_app_state(
            registry=registry, builder=builder, resolver=resolver
        )
        controller = _controller()

        first = await controller.company_agent_card.fn(
            controller, _state(app_state), _make_request()
        )
        assert first.content == {"name": "Snapshot Co"}

        second = await controller.company_agent_card.fn(
            controller, _state(app_state), _make_request()
        )
        assert second.content == {"name": "Snapshot Co"}
        builder.build_company_card.assert_called_once()

    @pytest.mark.unit
    async def test_assemble_failure_returns_503_sanitized(self) -> None:
        """A build failure logs sanitized error fields and returns 503."""
        registry = AsyncMock(spec=AgentRegistryService)
        registry.list_active.side_effect = RuntimeError("db down")
        builder = Mock(spec=AgentCardBuilder)
        app_state = _make_app_state(registry=registry, builder=builder)
        controller = _controller()

        with patch.object(well_known, "logger") as mock_logger:
            response = await controller.company_agent_card.fn(
                controller, _state(app_state), _make_request()
            )

        assert response.status_code == 503
        mock_logger.exception.assert_not_called()
        mock_logger.error.assert_called_once()
        kwargs = mock_logger.error.call_args.kwargs
        assert kwargs["error_type"] == "RuntimeError"
        assert kwargs["error"] == "RuntimeError: db down"
        assert "exc_info" not in kwargs


class TestAgentCardEndpoint:
    """`agent_card` resolve-first / fingerprint / failure behaviour."""

    @pytest.mark.unit
    async def test_unknown_agent_raises_not_found(self) -> None:
        """No matching identity surfaces as a 404."""
        registry = AsyncMock(spec=AgentRegistryService)
        registry.get.return_value = None
        registry.get_by_name.return_value = None
        builder = Mock(spec=AgentCardBuilder)
        app_state = _make_app_state(registry=registry, builder=builder)
        controller = _controller()
        with pytest.raises(NotFoundError):
            await controller.agent_card.fn(
                controller, _state(app_state), _make_request(), "ghost"
            )

    @pytest.mark.unit
    async def test_resolution_failure_returns_503_sanitized(self) -> None:
        """A registry outage logs sanitized fields and returns 503."""
        registry = AsyncMock(spec=AgentRegistryService)
        registry.get.side_effect = RuntimeError("registry boom")
        builder = Mock(spec=AgentCardBuilder)
        app_state = _make_app_state(registry=registry, builder=builder)
        controller = _controller()

        with patch.object(well_known, "logger") as mock_logger:
            response = await controller.agent_card.fn(
                controller, _state(app_state), _make_request(), "agent-1"
            )

        assert response.status_code == 503
        mock_logger.exception.assert_not_called()
        kwargs = mock_logger.error.call_args.kwargs
        assert kwargs["error_type"] == "RuntimeError"
        assert kwargs["error"] == "RuntimeError: registry boom"
        assert "exc_info" not in kwargs

    @pytest.mark.unit
    async def test_fresh_build_then_fingerprint_hit(self) -> None:
        """A second call with an unchanged identity skips the builder."""
        identity = _make_identity()
        registry = AsyncMock(spec=AgentRegistryService)
        registry.get.return_value = identity
        builder = Mock(spec=AgentCardBuilder)
        builder.build.return_value = SimpleNamespace(
            model_dump=lambda: {"name": "Ada"},
        )
        app_state = _make_app_state(registry=registry, builder=builder)
        controller = _controller()

        first = await controller.agent_card.fn(
            controller, _state(app_state), _make_request(), "agent-1"
        )
        assert first.content == {"name": "Ada"}

        second = await controller.agent_card.fn(
            controller, _state(app_state), _make_request(), "agent-1"
        )
        assert second.content == {"name": "Ada"}
        builder.build.assert_called_once()

    @pytest.mark.unit
    async def test_identity_change_invalidates_cached_card(self) -> None:
        """A rename changes the fingerprint, forcing a rebuild."""
        registry = AsyncMock(spec=AgentRegistryService)
        registry.get.return_value = _make_identity(name="Ada")
        builder = Mock(spec=AgentCardBuilder)
        builder.build.side_effect = [
            SimpleNamespace(model_dump=lambda: {"name": "Ada"}),
            SimpleNamespace(model_dump=lambda: {"name": "Grace"}),
        ]
        app_state = _make_app_state(registry=registry, builder=builder)
        controller = _controller()

        first = await controller.agent_card.fn(
            controller, _state(app_state), _make_request(), "agent-1"
        )
        assert first.content == {"name": "Ada"}

        registry.get.return_value = _make_identity(name="Grace")
        second = await controller.agent_card.fn(
            controller, _state(app_state), _make_request(), "agent-1"
        )
        assert second.content == {"name": "Grace"}
        assert builder.build.call_count == 2

    @pytest.mark.unit
    async def test_build_failure_returns_503_sanitized(self) -> None:
        """A card-build failure after resolution returns a sanitized 503."""
        registry = AsyncMock(spec=AgentRegistryService)
        registry.get.return_value = _make_identity()
        builder = Mock(spec=AgentCardBuilder)
        builder.build.side_effect = RuntimeError("build boom")
        app_state = _make_app_state(registry=registry, builder=builder)
        controller = _controller()

        with patch.object(well_known, "logger") as mock_logger:
            response = await controller.agent_card.fn(
                controller, _state(app_state), _make_request(), "agent-1"
            )

        assert response.status_code == 503
        mock_logger.exception.assert_not_called()
        kwargs = mock_logger.error.call_args.kwargs
        assert kwargs["error_type"] == "RuntimeError"
        assert kwargs["error"] == "RuntimeError: build boom"
        assert "exc_info" not in kwargs

    @pytest.mark.unit
    async def test_memory_error_propagates_from_resolution(self) -> None:
        """``MemoryError`` is never swallowed into a 503."""
        registry = AsyncMock(spec=AgentRegistryService)
        registry.get.side_effect = MemoryError("oom")
        builder = Mock(spec=AgentCardBuilder)
        app_state = _make_app_state(registry=registry, builder=builder)
        controller = _controller()
        with pytest.raises(MemoryError):
            await controller.agent_card.fn(
                controller, _state(app_state), _make_request(), "agent-1"
            )


class TestAssembleCompanyCard:
    """`_assemble_company_card` payload + fingerprint."""

    @pytest.mark.unit
    async def test_returns_payload_fingerprint_and_count(self) -> None:
        """Fingerprint is derived from the sorted identity ids."""
        registry = AsyncMock(spec=AgentRegistryService)
        registry.list_active.return_value = (
            _make_identity(agent_id="b"),
            _make_identity(agent_id="a"),
        )
        builder = Mock(spec=AgentCardBuilder)
        builder.build_company_card.return_value = SimpleNamespace(
            model_dump=lambda: {"name": "Snapshot Co"},
        )
        resolver = AsyncMock(spec=ConfigResolver)
        resolver.get_str.return_value = "Snapshot Co"
        app_state = _make_app_state(
            registry=registry, builder=builder, resolver=resolver
        )

        card_data, fingerprint, count = await _assemble_company_card(
            app_state, "http://test.example"
        )
        assert card_data == {"name": "Snapshot Co"}
        assert count == 2
        assert len(fingerprint) == 16
        builder.build_company_card.assert_called_once()
