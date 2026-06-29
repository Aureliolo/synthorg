"""Coverage for the autonomous Chief-of-Staff capability gate."""

from unittest.mock import AsyncMock

import pytest

from synthorg.meta.chief_of_staff._capability_gate import resolve_cos_autonomous_cap

pytestmark = pytest.mark.unit


def _resolver(*, master: bool, cap: bool) -> AsyncMock:
    """Build a resolver returning ``master`` then ``cap`` by namespace."""
    resolver = AsyncMock()

    async def _get_bool(namespace: str, _key: str) -> bool:
        if namespace == "self_improvement":
            return master
        return cap

    resolver.get_bool = AsyncMock(side_effect=_get_bool)
    return resolver


async def test_falls_back_to_baked_when_resolver_missing() -> None:
    enabled = await resolve_cos_autonomous_cap(
        resolver=None,
        key="alerts_enabled",
        master_fallback=True,
        cap_fallback=True,
    )
    assert enabled is True

    disabled = await resolve_cos_autonomous_cap(
        resolver=None,
        key="alerts_enabled",
        master_fallback=True,
        cap_fallback=False,
    )
    assert disabled is False


async def test_requires_both_master_and_capability() -> None:
    both_on = await resolve_cos_autonomous_cap(
        resolver=_resolver(master=True, cap=True),
        key="alerts_enabled",
        master_fallback=False,
        cap_fallback=False,
    )
    assert both_on is True

    cap_off = await resolve_cos_autonomous_cap(
        resolver=_resolver(master=True, cap=False),
        key="alerts_enabled",
        master_fallback=False,
        cap_fallback=False,
    )
    assert cap_off is False


async def test_master_off_short_circuits_capability_read() -> None:
    """A disabled persona never spends on the capability lookup."""
    resolver = _resolver(master=False, cap=True)
    enabled = await resolve_cos_autonomous_cap(
        resolver=resolver,
        key="alerts_enabled",
        master_fallback=False,
        cap_fallback=False,
    )
    assert enabled is False
    resolver.get_bool.assert_awaited_once_with(
        "self_improvement", "chief_of_staff_enabled"
    )
