"""Tests for well-known Agent Card cache helpers."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from synthorg.a2a.well_known import (
    _get_cached_card,
    _put_cached_card,
    _resolve_company_name,
)
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
