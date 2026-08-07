"""Integration tests for ``wire_sprint_service``.

Uses a real connected + migrated SQLite backend (so the sprint factory
builds a live repository) and asserts the best-effort / idempotent wiring
contract: the service comes online when its deps exist, the completion
observer is registered exactly once across re-runs, and a missing
dependency leaves the service unwired.
"""

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest

from synthorg.api.lifecycle_helpers.sprint_wiring import wire_sprint_service
from synthorg.api.subsystems.errors import SubsystemDeclinedError
from synthorg.engine.state import EngineStateSlice
from synthorg.engine.task_engine import TaskEngine
from synthorg.engine.workflow.ceremony_scheduler import CeremonyScheduler
from synthorg.persistence import migrations
from synthorg.persistence.config import SQLiteConfig
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.persistence.sqlite.backend import SQLitePersistenceBackend
from synthorg.settings.resolver_protocol import ConfigResolverProtocol
from tests._shared import make_app_state, mock_of

pytestmark = pytest.mark.integration

#: Configured mock, typed loosely for the unittest.mock API.
_Configured = Any  # type: ignore[explicit-any]


@pytest.fixture
async def sqlite_backend(tmp_path: Path) -> AsyncIterator[PersistenceBackend]:
    db_path = tmp_path / "sprint_wiring.db"
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


def _task_engine() -> _Configured:
    return mock_of[TaskEngine](register_observer=Mock())


def _deps() -> dict[str, _Configured]:
    return {
        "task_engine": _task_engine(),
        "ceremony_scheduler": mock_of[CeremonyScheduler](),
        "config_resolver": mock_of[ConfigResolverProtocol](),
    }


async def test_wires_service_and_registers_observer_once(
    sqlite_backend: PersistenceBackend,
) -> None:
    deps = _deps()
    app_state = make_app_state(persistence=sqlite_backend, **deps)

    await wire_sprint_service(app_state)
    assert app_state.slice(EngineStateSlice).sprint_service is not None
    assert deps["task_engine"].register_observer.call_count == 1

    # Idempotent re-run must not double-register the completion observer.
    await wire_sprint_service(app_state)
    assert deps["task_engine"].register_observer.call_count == 1


async def test_unwired_without_persistence() -> None:
    """A missing dependency declines by name, and wires nothing.

    The decline is the reconciler's BLOCKED reason, so naming the absent
    collaborator is the point: a silent return would leave the subsystem
    endpoint reporting 503 with nothing to say about why.
    """
    app_state = make_app_state(**_deps())

    with pytest.raises(SubsystemDeclinedError, match="persistence backend"):
        await wire_sprint_service(app_state)

    assert app_state.slice(EngineStateSlice).sprint_service is None


async def test_unwired_without_ceremony_scheduler(
    sqlite_backend: PersistenceBackend,
) -> None:
    app_state = make_app_state(
        persistence=sqlite_backend,
        task_engine=_task_engine(),
        config_resolver=mock_of[ConfigResolverProtocol](),
    )

    with pytest.raises(SubsystemDeclinedError, match="ceremony scheduler"):
        await wire_sprint_service(app_state)

    assert app_state.slice(EngineStateSlice).sprint_service is None
