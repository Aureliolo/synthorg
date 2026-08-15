"""Unit tests for the boot sandbox-reconciliation hook.

The pass this wires shipped fully implemented and never once ran, because
nothing called it. These tests hold the call site itself: that the hook
declines rather than pretending when it cannot reconcile, that a completed
pass publishes the capability the reconciler reads as liveness, and that a
second pass is a no-op.

The daemon side is substituted, so nothing here touches Docker.
"""

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import aiodocker
import pytest

from synthorg.api.lifecycle_helpers import sandbox_reconcile_wiring
from synthorg.api.lifecycle_helpers.sandbox_reconcile_wiring import (
    _boot_epoch_seconds,
    wire_sandbox_reconciliation,
)
from synthorg.api.state import AppState
from synthorg.api.subsystems.errors import SubsystemDeclinedError
from synthorg.engine.workspace.state import WorkspaceStateSlice
from synthorg.persistence.state import PersistenceStateSlice
from synthorg.tools.sandbox.deployment_identity import deployment_id_for
from synthorg.tools.sandbox.reconciliation import ReconciliationOutcome
from synthorg.tools.state import ToolsStateSlice
from tests._shared import make_app_state

pytestmark = pytest.mark.unit


def _app_state(*, backend: object | None = None) -> AppState:
    """App state carrying only what the hook reads.

    Returns:
        The composed ``AppState``.
    """
    return make_app_state(slices={PersistenceStateSlice: {"backend": backend}})


class _FakeDocker:
    """Stand-in for ``aiodocker.Docker`` as an async context manager.

    Records its own close so the test can assert the client is released on
    both the success and the failure path: the hook runs at boot on every
    process, so a leaked connection is one per restart, forever.
    """

    def __init__(self) -> None:
        self.closed = False

    async def __aenter__(self) -> _FakeDocker:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        self.closed = True


def _connected_state(workspace_root: Path) -> AppState:
    """App state with a connected persistence backend and a pinned workspace.

    Args:
        workspace_root: The root the hook should derive its identity from.

    Returns:
        The composed ``AppState``.
    """
    backend = SimpleNamespace(is_connected=True, tracked_containers=object())
    return make_app_state(
        slices={
            PersistenceStateSlice: {"backend": backend},
            WorkspaceStateSlice: {"agent_workspace_root": workspace_root},
        }
    )


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


async def test_a_completed_pass_stamps_the_slice(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A pass that ran publishes the stamp the reconciler reads as liveness.

    Without the stamp the subsystem never reads up, so the reconciler keeps
    re-driving activation and the sweep runs again later in the process's
    life, when its containers are no longer a predecessor's.
    """
    fake_docker = _FakeDocker()
    monkeypatch.setattr(aiodocker, "Docker", lambda: fake_docker)

    async def _fake_reconcile(
        *,
        repo: object,
        docker: object,
        deployment_id: str,
        started_at: float,
        workspace_root: Path,
    ) -> ReconciliationOutcome:
        return ReconciliationOutcome(
            kept=("c1",),
            db_only_dropped=("stale",),
            docker_only_killed=("orphan",),
            foreign_skipped=("theirs",),
        )

    monkeypatch.setattr(
        sandbox_reconcile_wiring, "reconcile_tracked_containers", _fake_reconcile
    )

    app_state = _connected_state(tmp_path)

    await wire_sandbox_reconciliation(app_state)

    assert app_state.slice(ToolsStateSlice).sandbox_reconciled_at is not None
    assert fake_docker.closed


async def test_the_pass_is_bound_to_this_deployments_workspace(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Identity and workspace both come from the resolved root, not a default.

    They are the two facts that decide which containers may be removed, and
    a hook that derived either from somewhere else would hand the pass
    another deployment's containers to sweep.
    """
    monkeypatch.setattr(aiodocker, "Docker", _FakeDocker)

    passed: dict[str, object] = {}

    async def _fake_reconcile(
        *,
        repo: object,
        docker: object,
        deployment_id: str,
        started_at: float,
        workspace_root: Path,
    ) -> ReconciliationOutcome:
        passed["deployment_id"] = deployment_id
        passed["workspace_root"] = workspace_root
        return ReconciliationOutcome(
            kept=(), db_only_dropped=(), docker_only_killed=(), foreign_skipped=()
        )

    monkeypatch.setattr(
        sandbox_reconcile_wiring, "reconcile_tracked_containers", _fake_reconcile
    )

    await wire_sandbox_reconciliation(_connected_state(tmp_path))

    assert passed["workspace_root"] == tmp_path
    assert passed["deployment_id"] == deployment_id_for(tmp_path)


async def test_an_unreachable_daemon_declines_rather_than_stamping(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A daemon that is not up yet is a condition, not a verdict.

    Stamping here would retire the question for the life of the process and
    leave every orphan in place; declining lets the next reconciler pass
    retry once Docker is answering.
    """

    def _refuse() -> _FakeDocker:
        raise aiodocker.DockerError(500, "connection refused")

    monkeypatch.setattr(aiodocker, "Docker", _refuse)

    app_state = _connected_state(tmp_path)

    with pytest.raises(SubsystemDeclinedError, match="unreachable"):
        await wire_sandbox_reconciliation(app_state)

    assert app_state.slice(ToolsStateSlice).sandbox_reconciled_at is None


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
