"""Unit tests for the cached principle-override provider + ambient seam."""

from collections.abc import Mapping

import pytest

from synthorg.engine.strategy.principle_override_provider import (
    CachedPrincipleOverrideProvider,
    PrincipleOverrideLoader,
    PrincipleOverrideProvider,
    current_principle_override_provider,
    set_principle_override_provider,
)

pytestmark = pytest.mark.unit


def _loader(data: dict[str, str]) -> PrincipleOverrideLoader:
    async def _load() -> Mapping[str, str]:
        return dict(data)

    return _load


async def test_refresh_loads_snapshot() -> None:
    provider = CachedPrincipleOverrideProvider(loader=_loader({"p1": "text-1"}))
    # Empty before the first refresh.
    assert dict(provider.overrides()) == {}
    await provider.refresh()
    assert dict(provider.overrides()) == {"p1": "text-1"}


async def test_refresh_replaces_snapshot() -> None:
    state = {"p1": "v1"}

    async def _load() -> Mapping[str, str]:
        return dict(state)

    provider = CachedPrincipleOverrideProvider(loader=_load)
    await provider.refresh()
    assert dict(provider.overrides()) == {"p1": "v1"}

    state["p1"] = "v2"
    state["p2"] = "w"
    await provider.refresh()
    assert dict(provider.overrides()) == {"p1": "v2", "p2": "w"}


async def test_overrides_snapshot_is_read_only() -> None:
    provider = CachedPrincipleOverrideProvider(loader=_loader({"p1": "t"}))
    await provider.refresh()
    with pytest.raises(TypeError):
        provider.overrides()["p2"] = "x"  # type: ignore[index]


def test_cached_provider_satisfies_protocol() -> None:
    provider = CachedPrincipleOverrideProvider(loader=_loader({}))
    assert isinstance(provider, PrincipleOverrideProvider)


def test_ambient_set_and_clear() -> None:
    assert current_principle_override_provider() is None
    provider = CachedPrincipleOverrideProvider(loader=_loader({}))
    set_principle_override_provider(provider)
    try:
        assert current_principle_override_provider() is provider
    finally:
        set_principle_override_provider(None)
    assert current_principle_override_provider() is None
