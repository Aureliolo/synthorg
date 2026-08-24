"""Integration tests for ``wire_sprint_recovery``.

The sweep is its own subsystem rather than a step inside the sprint
service's wiring, and these are the two properties that separation buys: a
failure to start it cannot leave the service's completion observer attached
to a service the slice never received, and the boot pass runs before the
cadence rather than an interval after it.
"""

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

from synthorg.api.lifecycle_helpers.sprint_recovery_wiring import (
    unwire_sprint_recovery,
    wire_sprint_recovery,
)
from synthorg.api.lifecycle_helpers.sprint_wiring import wire_sprint_service
from synthorg.api.subsystems.errors import SubsystemDeclinedError
from synthorg.core.types import NotBlankStr
from synthorg.engine.state import EngineStateSlice
from synthorg.engine.task_engine import TaskEngine
from synthorg.engine.workflow.enums import WorkflowType
from synthorg.engine.workflow.sprint_lifecycle import Sprint, SprintStatus
from synthorg.engine.workflow.sprint_recovery import SprintRecoveryReconciler
from synthorg.persistence import migrations
from synthorg.persistence.config import SQLiteConfig
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.persistence.sprint_factory import build_sprint_repository
from synthorg.persistence.sqlite.backend import SQLitePersistenceBackend
from synthorg.settings.resolver_protocol import ConfigResolverProtocol
from synthorg.settings.state import SettingsStateSlice
from tests._shared import make_app_state, mock_of

pytestmark = pytest.mark.integration

#: Configured mock, typed loosely for the unittest.mock API.
_Configured = Any  # type: ignore[explicit-any]

_START = "2026-05-22T12:00:00+00:00"
_INTERVAL_SECONDS = 600.0


@pytest.fixture
async def sqlite_backend(tmp_path: Path) -> AsyncIterator[PersistenceBackend]:
    db_path = tmp_path / "sprint_recovery_wiring.db"
    rev_path = migrations.copy_revisions(tmp_path / "revisions", backend="sqlite")
    await migrations.migrate_apply(
        migrations.to_sqlite_url(str(db_path)), revisions_path=rev_path
    )
    backend = SQLitePersistenceBackend(SQLiteConfig(path=str(db_path)))
    await backend.connect()
    try:
        yield backend
    finally:
        await backend.disconnect()


def _resolver() -> _Configured:
    return mock_of[ConfigResolverProtocol](
        get_bool=AsyncMock(return_value=True),
        get_float=AsyncMock(return_value=_INTERVAL_SECONDS),
        get_enum=AsyncMock(return_value=WorkflowType.AGILE_KANBAN),
    )


def _deps() -> dict[str, _Configured]:
    return {
        "task_engine": mock_of[TaskEngine](register_observer=Mock()),
        "config_resolver": _resolver(),
    }


def _stranded_sprint() -> Sprint:
    """A fully-delivered ACTIVE sprint whose tail never ran.

    Returns:
        The sprint a killed process leaves behind.
    """
    return Sprint(
        id=NotBlankStr("stranded"),
        project=NotBlankStr("proj-x"),
        name=NotBlankStr("Sprint One"),
        sprint_number=1,
        status=SprintStatus.ACTIVE,
        start_date=_START,
        task_ids=(NotBlankStr("task-a"),),
        completed_task_ids=(NotBlankStr("task-a"),),
        task_points={"task-a": 5.0},
        story_points_committed=5.0,
        story_points_completed=5.0,
    )


async def test_boot_pass_runs_before_the_cadence(
    sqlite_backend: PersistenceBackend,
) -> None:
    """A restart is when sprints are stranded, so the pass cannot wait.

    Deferring it to the scheduler's first tick would leave the board showing
    work in flight with nothing behind it for a whole resync interval.
    """
    repository = build_sprint_repository(sqlite_backend)
    assert repository is not None
    await repository.save(_stranded_sprint())

    app_state = make_app_state(persistence=sqlite_backend, **_deps())
    await wire_sprint_service(app_state)
    await wire_sprint_recovery(app_state)
    try:
        assert app_state.slice(EngineStateSlice).sprint_tail_scheduler is not None
        recovered = await repository.get(NotBlankStr("stranded"))
        assert recovered is not None
        assert recovered.status is SprintStatus.COMPLETED
    finally:
        await unwire_sprint_recovery(app_state)


async def test_a_timeout_from_inside_the_pass_is_not_the_boot_bound(
    sqlite_backend: PersistenceBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only the startup deadline is waived, and it names itself.

    ``TimeoutError`` is also what a socket timeout in the driver raises,
    so an unqualified handler would report "the boot pass hit its bound"
    for a store that never answered and carry startup on regardless.
    """

    async def _refuse(*_args: object, **_kwargs: object) -> None:
        msg = "store did not answer"
        raise TimeoutError(msg)

    monkeypatch.setattr(SprintRecoveryReconciler, "reconcile", _refuse)
    app_state = make_app_state(persistence=sqlite_backend, **_deps())
    await wire_sprint_service(app_state)

    with pytest.raises(TimeoutError, match="did not answer"):
        await wire_sprint_recovery(app_state)

    assert app_state.slice(EngineStateSlice).sprint_tail_scheduler is None


async def test_is_idempotent(sqlite_backend: PersistenceBackend) -> None:
    app_state = make_app_state(persistence=sqlite_backend, **_deps())
    await wire_sprint_service(app_state)
    await wire_sprint_recovery(app_state)
    first = app_state.slice(EngineStateSlice).sprint_tail_scheduler
    try:
        await wire_sprint_recovery(app_state)
        assert app_state.slice(EngineStateSlice).sprint_tail_scheduler is first
    finally:
        await unwire_sprint_recovery(app_state)


async def test_unwire_drops_the_scheduler(
    sqlite_backend: PersistenceBackend,
) -> None:
    app_state = make_app_state(persistence=sqlite_backend, **_deps())
    await wire_sprint_service(app_state)
    await wire_sprint_recovery(app_state)

    await unwire_sprint_recovery(app_state)

    assert app_state.slice(EngineStateSlice).sprint_tail_scheduler is None


async def test_declines_without_the_sprint_service(
    sqlite_backend: PersistenceBackend,
) -> None:
    """The sweep asks the service, live per pass, whether sprints apply.

    Declining by name is what the subsystem status surface reports, so a
    boot without one says which collaborator it is waiting on.
    """
    app_state = make_app_state(persistence=sqlite_backend, **_deps())

    with pytest.raises(SubsystemDeclinedError, match="sprint service"):
        await wire_sprint_recovery(app_state)

    assert app_state.slice(EngineStateSlice).sprint_tail_scheduler is None


async def test_service_wiring_leaves_no_observer_when_the_sweep_declines(
    sqlite_backend: PersistenceBackend,
) -> None:
    """The separation this subsystem exists for.

    Folded into the service's wiring, the sweep's start sat between
    registering the completion observer and publishing the service, so a
    failure there left a live observer bound to a service nothing could
    reach, which the next reconcile pass then registered a second time.
    """
    deps = _deps()
    app_state = make_app_state(persistence=sqlite_backend, **deps)

    await wire_sprint_service(app_state)
    assert app_state.slice(EngineStateSlice).sprint_service is not None
    assert deps["task_engine"].register_observer.call_count == 1

    # Drop the resolver the sweep reads its cadence from, so its wiring
    # declines with the service already published: the arrangement the
    # folded-in version could not produce without stranding the observer.
    settings_slice = app_state.slice(SettingsStateSlice)
    app_state.swap_slice(settings_slice.model_copy(update={"config_resolver": None}))

    with pytest.raises(SubsystemDeclinedError, match="settings resolver"):
        await wire_sprint_recovery(app_state)

    assert app_state.slice(EngineStateSlice).sprint_tail_scheduler is None
    # The service kept its slice entry, so its observer is reachable
    # rather than bound to something nothing can get to, and the decline
    # added no second registration.
    assert app_state.slice(EngineStateSlice).sprint_service is not None
    assert deps["task_engine"].register_observer.call_count == 1
