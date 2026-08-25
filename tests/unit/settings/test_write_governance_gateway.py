"""Governance tests for the LLM gateway's enable toggle.

The gateway ships OFF, so the guarded transition is the FIRST stored ``true``:
an unset value is already the closed posture, which is the opposite of the
default-on shape the same key carried while an in-sandbox harness needed it.
The unset case is tested explicitly, because that is the transition a
default-on reading waves through.
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

_GATEWAY = ("providers", "gateway_enabled")


def _current(value: str | None) -> Callable[[str, str], Awaitable[str | None]]:
    async def _get(_namespace: str, _key: str) -> str | None:
        return value

    return _get


async def test_enabling_from_unset_is_rejected() -> None:
    # The toggle ships off, so an unset value is the closed posture and this
    # write is what opens an HTTP surface that dispatches billed LLM calls.
    with pytest.raises(SecurityToggleConfirmationRequiredError):
        await enforce_security_write_governance(
            [(*_GATEWAY, "true")], governance=None, get_current=_current(None)
        )


async def test_enabling_after_explicit_disable_is_rejected() -> None:
    with pytest.raises(SecurityToggleConfirmationRequiredError):
        await enforce_security_write_governance(
            [(*_GATEWAY, "true")], governance=None, get_current=_current("false")
        )


async def test_enabling_with_governance_is_allowed() -> None:
    await enforce_security_write_governance(
        [(*_GATEWAY, "true")], governance=_SATISFIED, get_current=_current(None)
    )


async def test_restating_an_enabled_gateway_is_unguarded() -> None:
    # Already open: writing the value it already holds opens nothing.
    await enforce_security_write_governance(
        [(*_GATEWAY, "true")], governance=None, get_current=_current("true")
    )


async def test_disabling_is_unguarded() -> None:
    await enforce_security_write_governance(
        [(*_GATEWAY, "false")], governance=None, get_current=_current("true")
    )


def test_registered_default_is_off() -> None:
    # Every transition test above reads "unset means off" from the policy,
    # which holds only while the registry agrees. Without this, flipping the
    # shipped default back to "true" passes every other test in this file
    # while leaving the first enable unguarded.
    defn = get_registry().get(*_GATEWAY)
    assert defn is not None
    assert defn.default == "false"
