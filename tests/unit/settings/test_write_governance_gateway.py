"""Governance tests for the default-on capability toggles and loop routing.

The three capability toggles share one transition matrix, so they are driven
from one parametrized table rather than three hand-copied sets: a missing
cell in a copied set reads as "covered" while proving nothing, which is how
the credentialed-MCP unset case went untested.
"""

from collections.abc import Awaitable, Callable

import pytest

from synthorg.settings.errors import SecurityToggleConfirmationRequiredError
from synthorg.settings.registry import get_registry
from synthorg.settings.write_governance import (
    SettingsWriteGovernance,
    enforce_security_write_governance,
)

pytestmark = pytest.mark.unit

_SATISFIED = SettingsWriteGovernance(confirm=True, reason="ops", actor="admin")

# Every capability toggle that ships ON. Each must behave identically:
# unset restates the running posture, a stored "false" returning to "true"
# reopens the surface, and turning it off tightens.
_DEFAULT_ON_TOGGLES = [
    ("providers", "gateway_enabled"),
    ("tools", "openhands_enabled"),
    ("tools", "credentialed_mcp_enabled"),
]


def _current(value: str | None) -> Callable[[str, str], Awaitable[str | None]]:
    async def _get(_namespace: str, _key: str) -> str | None:
        return value

    return _get


@pytest.mark.parametrize(("namespace", "key"), _DEFAULT_ON_TOGGLES)
async def test_reenabling_after_explicit_disable_is_rejected(
    namespace: str, key: str
) -> None:
    with pytest.raises(SecurityToggleConfirmationRequiredError):
        await enforce_security_write_governance(
            [(namespace, key, "true")], governance=None, get_current=_current("false")
        )


@pytest.mark.parametrize(("namespace", "key"), _DEFAULT_ON_TOGGLES)
async def test_enabling_from_unset_is_unguarded(namespace: str, key: str) -> None:
    # The toggle ships on, so an unset value is already the running posture:
    # writing "true" restates it rather than opening anything.
    await enforce_security_write_governance(
        [(namespace, key, "true")], governance=None, get_current=_current(None)
    )


@pytest.mark.parametrize(("namespace", "key"), _DEFAULT_ON_TOGGLES)
async def test_reenabling_with_governance_is_allowed(namespace: str, key: str) -> None:
    await enforce_security_write_governance(
        [(namespace, key, "true")], governance=_SATISFIED, get_current=_current("false")
    )


@pytest.mark.parametrize(("namespace", "key"), _DEFAULT_ON_TOGGLES)
async def test_disabling_is_unguarded(namespace: str, key: str) -> None:
    await enforce_security_write_governance(
        [(namespace, key, "false")], governance=None, get_current=_current("true")
    )


@pytest.mark.parametrize(("namespace", "key"), _DEFAULT_ON_TOGGLES)
def test_registered_default_is_on(namespace: str, key: str) -> None:
    # The transition tests above all read "unset means on" from the policy,
    # which is only true while the registry agrees. Without this, a revert of
    # the shipped default passes every other test in this file.
    defn = get_registry().get(namespace, key)
    assert defn is not None
    assert defn.default == "true"


_AUTO_SELECT = ("engine", "loop_auto_select_enabled")
_DEFAULT_LOOP = ("engine", "default_loop_type")
_OVERRIDES = ("engine", "loop_complexity_overrides")


async def test_enabling_loop_auto_select_without_governance_is_rejected() -> None:
    # Shipping the sandboxed loop and routing real tasks into it are separate
    # decisions; this is the one that spawns a container running generated
    # code, so it cannot be the less guarded of the two.
    with pytest.raises(SecurityToggleConfirmationRequiredError):
        await enforce_security_write_governance(
            [(*_AUTO_SELECT, "true")], governance=None, get_current=_current("false")
        )


async def test_disabling_loop_auto_select_is_unguarded() -> None:
    await enforce_security_write_governance(
        [(*_AUTO_SELECT, "false")], governance=None, get_current=_current("true")
    )


@pytest.mark.parametrize(
    ("setting", "current", "new"),
    [
        (_DEFAULT_LOOP, "react", "openhands"),
        (_OVERRIDES, "complex:hybrid", "complex:openhands"),
        (_OVERRIDES, None, "epic:openhands"),
        # Widening an EXISTING route set: the value already named the
        # sandboxed loop, so a presence test would wave this through even
        # though a second complexity band now reaches it.
        (_OVERRIDES, "complex:openhands", "complex:openhands,epic:openhands"),
    ],
)
async def test_routing_a_task_to_the_sandboxed_loop_is_rejected(
    setting: tuple[str, str], current: str | None, new: str
) -> None:
    with pytest.raises(SecurityToggleConfirmationRequiredError):
        await enforce_security_write_governance(
            [(*setting, new)], governance=None, get_current=_current(current)
        )


@pytest.mark.parametrize(
    ("setting", "current", "new"),
    [
        (_DEFAULT_LOOP, "openhands", "react"),
        (_OVERRIDES, "complex:openhands", "complex:hybrid"),
        (_DEFAULT_LOOP, "react", "hybrid"),
        # Narrowing an existing route set tightens and stays unguarded.
        (_OVERRIDES, "complex:openhands,epic:openhands", "complex:openhands"),
        # Reordering the same routes changes nothing.
        (
            _OVERRIDES,
            "complex:openhands,epic:openhands",
            "epic:openhands,complex:openhands",
        ),
    ],
)
async def test_routing_away_from_the_sandboxed_loop_is_unguarded(
    setting: tuple[str, str], current: str | None, new: str
) -> None:
    await enforce_security_write_governance(
        [(*setting, new)], governance=None, get_current=_current(current)
    )


_CAPS = "tools", "credentialed_mcp_capabilities"


async def test_widening_capabilities_empty_to_star_is_rejected() -> None:
    with pytest.raises(SecurityToggleConfirmationRequiredError):
        await enforce_security_write_governance(
            [(*_CAPS, "*")], governance=None, get_current=_current("")
        )


async def test_widening_capabilities_adds_write_is_rejected() -> None:
    with pytest.raises(SecurityToggleConfirmationRequiredError):
        await enforce_security_write_governance(
            [(*_CAPS, "forge:read,forge:write")],
            governance=None,
            get_current=_current("forge:read"),
        )


async def test_widening_capabilities_with_governance_is_allowed() -> None:
    await enforce_security_write_governance(
        [(*_CAPS, "*")], governance=_SATISFIED, get_current=_current("")
    )


async def test_narrowing_capabilities_is_unguarded() -> None:
    # Dropping a pattern (forge:read,forge:write -> forge:read) is a
    # narrowing and needs no confirm+reason+actor.
    await enforce_security_write_governance(
        [(*_CAPS, "forge:read")],
        governance=None,
        get_current=_current("forge:read,forge:write"),
    )
