"""Coverage for the shared kill-switch resolver helper."""

from unittest.mock import AsyncMock

import pytest

from synthorg.settings.kill_switch import resolve_bool_with_fallback

pytestmark = pytest.mark.unit


async def test_returns_fallback_when_resolver_missing() -> None:
    result = await resolve_bool_with_fallback(
        resolver=None,
        namespace="engine",
        key="evolution_enabled",
        fallback=True,
    )
    assert result is True


async def test_returns_resolver_value_when_wired() -> None:
    resolver = AsyncMock()
    resolver.get_bool = AsyncMock(return_value=False)
    result = await resolve_bool_with_fallback(
        resolver=resolver,
        namespace="engine",
        key="evolution_enabled",
        fallback=True,
    )
    assert result is False
    resolver.get_bool.assert_awaited_once_with("engine", "evolution_enabled")


async def test_resolver_outage_falls_back() -> None:
    resolver = AsyncMock()
    resolver.get_bool = AsyncMock(side_effect=RuntimeError("transient"))
    result = await resolve_bool_with_fallback(
        resolver=resolver,
        namespace="engine",
        key="evolution_enabled",
        fallback=True,
    )
    assert result is True


@pytest.mark.parametrize(
    "exc_type",
    [MemoryError, RecursionError],
    ids=["memory_error", "recursion_error"],
)
async def test_system_errors_propagate(exc_type: type[BaseException]) -> None:
    """Both ``MemoryError`` and ``RecursionError`` re-raise unchanged.

    The PEP 758 ``except MemoryError, RecursionError: raise`` clause in
    ``resolve_bool_with_fallback`` is the contract that lets the
    interpreter surface unrecoverable conditions; covering each type
    independently catches a regression that would otherwise demote one
    of them to a silent fallback.
    """
    resolver = AsyncMock()
    resolver.get_bool = AsyncMock(side_effect=exc_type())
    with pytest.raises(exc_type):
        await resolve_bool_with_fallback(
            resolver=resolver,
            namespace="engine",
            key="evolution_enabled",
            fallback=True,
        )
