"""Governance tests for the webhook-receipt retention guard.

The guarded direction here is shortening, not disabling: a sweep destroys stored
delivery evidence irreversibly, so the confirm+reason+actor path is what stands
between a bulk settings import and gone records.
"""

from collections.abc import Awaitable, Callable

import pytest

from synthorg.settings.errors import SecurityToggleConfirmationRequiredError
from synthorg.settings.write_governance import (
    SettingsWriteGovernance,
    enforce_security_write_governance,
)

pytestmark = pytest.mark.unit

_KEY = ("integrations", "webhook_receipt_retention_days")
_SATISFIED = SettingsWriteGovernance(confirm=True, reason="ops", actor="admin")


def _current(value: str | None) -> Callable[[str, str], Awaitable[str | None]]:
    async def _get(_namespace: str, _key: str) -> str | None:
        return value

    return _get


async def test_introducing_a_window_over_never_sweep_is_rejected() -> None:
    # 0 -> 1 turns "keep everything" into "discard after a day".
    with pytest.raises(SecurityToggleConfirmationRequiredError):
        await enforce_security_write_governance(
            [(*_KEY, "1")], governance=None, get_current=_current("0")
        )


async def test_introducing_a_window_from_unset_is_rejected() -> None:
    # Unset resolves to the registered never-sweep default, so the first
    # explicit write of a finite window is guarded too.
    with pytest.raises(SecurityToggleConfirmationRequiredError):
        await enforce_security_write_governance(
            [(*_KEY, "30")], governance=None, get_current=_current(None)
        )


async def test_shortening_an_existing_window_is_rejected() -> None:
    with pytest.raises(SecurityToggleConfirmationRequiredError):
        await enforce_security_write_governance(
            [(*_KEY, "30")], governance=None, get_current=_current("90")
        )


async def test_shortening_with_governance_is_allowed() -> None:
    await enforce_security_write_governance(
        [(*_KEY, "30")], governance=_SATISFIED, get_current=_current("90")
    )


async def test_lengthening_a_window_is_unguarded() -> None:
    await enforce_security_write_governance(
        [(*_KEY, "90")], governance=None, get_current=_current("30")
    )


async def test_returning_to_never_sweep_is_unguarded() -> None:
    # Retains strictly more than any finite window, so there is nothing to
    # deliberate over.
    await enforce_security_write_governance(
        [(*_KEY, "0")], governance=None, get_current=_current("30")
    )


async def test_an_unchanged_window_is_unguarded() -> None:
    await enforce_security_write_governance(
        [(*_KEY, "30")], governance=None, get_current=_current("30")
    )


async def test_a_malformed_window_is_left_to_the_type_validator() -> None:
    # The guard must not turn an invalid value into a confirmation prompt: the
    # INTEGER validator rejects it with a message that names the real problem.
    await enforce_security_write_governance(
        [(*_KEY, "not-a-number")], governance=None, get_current=_current("0")
    )


@pytest.mark.parametrize("stored", ["not-a-number", "", "  "])
async def test_a_malformed_stored_window_does_not_break_the_guard(
    stored: str,
) -> None:
    # The stored side is read before any validator has seen it, so an
    # unparsable one must resolve to "cannot compare" rather than escaping the
    # guard as an error on a write that is itself perfectly valid.
    await enforce_security_write_governance(
        [(*_KEY, "30")], governance=None, get_current=_current(stored)
    )


async def test_an_unrelated_integrations_key_is_unguarded() -> None:
    await enforce_security_write_governance(
        [("integrations", "secret_capture_ttl_seconds", "30")],
        governance=None,
        get_current=_current("600"),
    )
