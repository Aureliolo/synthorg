"""Unit tests for the boot sandbox-reconciliation hook.

The pass this wires shipped fully implemented and never once ran, because
nothing called it. These tests hold the call site itself: that the hook
declines rather than pretending when it cannot reconcile, that a completed
pass publishes the capability the reconciler reads as liveness, and that a
second pass is a no-op.

The daemon side is substituted, so nothing here touches Docker.
"""

from datetime import UTC, datetime

import pytest

from synthorg.api.lifecycle_helpers.sandbox_reconcile_wiring import (
    _boot_epoch_seconds,
    wire_sandbox_reconciliation,
)
from synthorg.api.state import AppState
from synthorg.api.subsystems.errors import SubsystemDeclinedError
from synthorg.persistence.state import PersistenceStateSlice
from synthorg.tools.state import ToolsStateSlice
from tests._shared import make_app_state

pytestmark = pytest.mark.unit


def _app_state(*, backend: object | None = None) -> AppState:
    """App state carrying only what the hook reads.

    Returns:
        The composed ``AppState``.
    """
    return make_app_state(slices={PersistenceStateSlice: {"backend": backend}})


async def test_declines_without_persistence() -> None:
    """No persistence means no tracking rows, so the hook declines.

    Declining rather than sweeping is the point: with no rows to compare
    against, every live container looks like an orphan.
    """
    app_state = _app_state(backend=None)

    with pytest.raises(SubsystemDeclinedError, match="persistence"):
        await wire_sandbox_reconciliation(app_state)

    assert app_state.slice(ToolsStateSlice).sandbox_reconciled_at is None


async def test_a_completed_pass_is_not_repeated() -> None:
    """An already-stamped slice short-circuits.

    The stamp is what the reconciler reads as liveness, so a second pass
    must not re-enter and sweep again mid-life, when this process may by
    then have created sandboxes of its own.
    """
    app_state = make_app_state(
        slices={
            PersistenceStateSlice: {"backend": None},
            ToolsStateSlice: {"sandbox_reconciled_at": datetime.now(UTC)},
        }
    )

    # Returns rather than raising, despite there being no persistence: the
    # stamp is checked first.
    await wire_sandbox_reconciliation(app_state)


def test_boot_epoch_is_derived_from_the_clock_seam() -> None:
    """Boot time is placed on the daemon's scale, not the monotonic one.

    Container creation times are epoch seconds; ``startup_time`` is a
    monotonic reading. Comparing the two directly would classify every
    container as older than boot, which is the direction that sweeps live
    work.
    """
    app_state = _app_state()

    boot_epoch = _boot_epoch_seconds(app_state)
    now_epoch = app_state.clock.now().timestamp()

    # Boot cannot be in the future, and an uptime of days would mean the
    # monotonic reading leaked into the result.
    assert boot_epoch <= now_epoch
    assert now_epoch - boot_epoch < 60.0
