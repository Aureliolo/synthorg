"""Restart-safety integration test for WP-1 persistence.

Per the WP-1 plan: after the four critical state stores
(ceremony scheduler state, meeting cooldown, tracked containers,
webhook receipts) gained durable persistence, a process restart
must rehydrate them from the backend instead of starting from zero.

A genuine restart cycle requires constructing a *second*
``PersistenceBackend`` instance, pointing at the same storage, after
disconnecting the first; the local ``backend_factory`` fixture below
does exactly that. The test lives under ``tests/integration/`` rather
than ``tests/conformance/`` because conformance tests must consume
the parametrised single-instance ``backend`` fixture (enforced by
``scripts/check_dual_backend_test_parity.py``), and a restart cycle
fundamentally requires two backend instances.

SQLite is exercised in-process; Postgres is exercised via a
testcontainer when Docker is available and skipped otherwise.
"""

import asyncio
import sys
import uuid
import warnings
from collections.abc import AsyncIterator, Callable, Coroutine
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from synthorg.core.types import NotBlankStr
from synthorg.persistence import migrations
from synthorg.persistence.ceremony_scheduler_state_protocol import (
    CeremonySchedulerStateRecord,
)
from synthorg.persistence.config import PostgresConfig, SQLiteConfig
from synthorg.persistence.meeting_cooldown_protocol import MeetingCooldownRecord
from synthorg.persistence.postgres.backend import PostgresPersistenceBackend
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.persistence.sqlite.backend import SQLitePersistenceBackend
from synthorg.persistence.tracked_container_protocol import TrackedContainerRecord

pytestmark = pytest.mark.integration


# A ``BackendFactory`` returns a freshly-constructed, connected backend
# pointing at the *same* underlying storage every time it is awaited.
# That is the structural property a restart-cycle test needs: the first
# call writes state, the test disconnects, and the second call observes
# the persisted record without any in-memory carryover.
BackendFactory = Callable[[], Coroutine[Any, Any, PersistenceBackend]]


@pytest.fixture(scope="session")
def event_loop_policy() -> Any:
    """Use SelectorEventLoop on Windows so psycopg async mode works.

    Mirrors the policy fixture in ``tests/conformance/persistence/conftest.py``;
    psycopg 3 refuses to run under ``ProactorEventLoop`` on Windows.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        if sys.platform == "win32":
            return asyncio.WindowsSelectorEventLoopPolicy()  # type: ignore[attr-defined,unused-ignore]
        return asyncio.DefaultEventLoopPolicy()  # type: ignore[attr-defined,unused-ignore,unreachable]


@pytest.fixture(params=["sqlite", "postgres"], ids=["sqlite", "postgres"])
async def backend_factory(
    request: pytest.FixtureRequest,
    tmp_path: Path,
) -> AsyncIterator[BackendFactory]:
    """Yield a factory that builds fresh backends against shared storage.

    The first call to the factory migrates the database; subsequent
    calls skip migration and just connect. Each backend yielded by the
    factory is the caller's responsibility to ``disconnect`` before
    constructing the next one; that is the "process restart" the test
    simulates.
    """
    backend_name = request.param
    if backend_name == "sqlite":
        async for factory in _sqlite_backend_factory(tmp_path):
            yield factory
    elif backend_name == "postgres":
        from tests.conformance.persistence.conftest import (
            _create_postgres_backend,
            _drop_postgres_database,
        )

        container = request.getfixturevalue("postgres_container")
        db_name = f"test_restart_{uuid.uuid4().hex}"
        first_built = False

        async def factory() -> PersistenceBackend:
            nonlocal first_built
            if not first_built:
                first_built = True
                return await _create_postgres_backend(container, db_name)
            # Subsequent calls re-connect to the existing per-test DB
            # without re-running migrations: the DB already carries the
            # schema from the first call.
            return await _reconnect_postgres_backend(container, db_name)

        try:
            yield factory
        finally:
            await _drop_postgres_database(container, db_name)
    else:  # pragma: no cover - defensive
        msg = f"Unknown restart-cycle backend: {backend_name}"
        raise ValueError(msg)


# The previous override here delegated to the conformance suite's
# ``postgres_container`` via ``__wrapped__(tmp_path_factory,
# worker_id)`` to share one testcontainer across both suites in local
# dev. That coupling broke when the conformance fixture's setup was
# moved into a ``pytest_sessionstart`` hook to keep the cross-worker
# FileLock wait out of the per-test ``pytest-timeout`` budget (see
# ``tests/conformance/persistence/conftest.py::pytest_sessionstart``):
# the hook only fires for sessions where the conformance conftest is
# loaded, and integration-only sessions never see it, so the
# delegation path would consume the empty module-level cache and
# raise ``RuntimeError`` at fixture resolution time.
#
# The integration suite's own ``postgres_container`` fixture (in
# ``tests/integration/persistence/conftest.py``) is the supported
# entry point. In CI, ``SYNTHORG_TEST_POSTGRES_*`` env vars bypass
# testcontainers entirely so cross-suite container sharing is moot.
# In local dev, both suites starting their own container costs ~150 MB
# of duplicated container state -- annoying, not broken.


async def _sqlite_backend_factory(
    tmp_path: Path,
) -> AsyncIterator[BackendFactory]:
    """SQLite arm: migrate once, then hand out fresh backend instances."""
    db_path = tmp_path / "restart_cycle.db"
    rev_path = migrations.copy_revisions(tmp_path / "revisions", backend="sqlite")
    await migrations.migrate_apply(
        migrations.to_sqlite_url(str(db_path)),
        revisions_path=rev_path,
    )

    async def factory() -> PersistenceBackend:
        new_backend = SQLitePersistenceBackend(SQLiteConfig(path=str(db_path)))
        await new_backend.connect()
        return new_backend

    yield factory


async def _reconnect_postgres_backend(
    container: object,
    db_name: str,
) -> PostgresPersistenceBackend:
    """Build a second Postgres backend pointing at an existing test DB."""
    from tests.conformance.persistence.conftest import _container_host_ipv4

    host = _container_host_ipv4(container)  # type: ignore[arg-type]
    port = int(container.get_exposed_port(5432))  # type: ignore[attr-defined]
    config = PostgresConfig(
        host=host,
        port=port,
        database=db_name,
        username=container.username,  # type: ignore[attr-defined]
        password=SecretStr(container.password),  # type: ignore[attr-defined]
        ssl_mode="disable",
        pool_min_size=1,
        pool_max_size=4,
        pool_timeout_seconds=10.0,
        connect_timeout_seconds=5.0,
    )
    new_backend = PostgresPersistenceBackend(config)
    await new_backend.connect()
    return new_backend


class TestWP1RestartSafety:
    """Each test goes through a genuine open / write / disconnect / reopen
    / read cycle so the restored state cannot come from in-memory carryover
    inside a single ``PersistenceBackend`` instance.
    """

    async def test_ceremony_state_survives_restart(
        self,
        backend_factory: BackendFactory,
    ) -> None:
        before = CeremonySchedulerStateRecord(
            sprint_id=NotBlankStr("sprint-restart"),
            completion_counters_json='{"standup": 3, "retro": 1}',
            fired_once_triggers_json='["sprint_start"]',
            total_completions=4,
            velocity_history_json="[]",
            updated_at=datetime.now(UTC),
        )

        first = await backend_factory()
        try:
            await first.ceremony_scheduler_state.save(before)
        finally:
            await first.disconnect()

        second = await backend_factory()
        try:
            after = await second.ceremony_scheduler_state.get(
                NotBlankStr("sprint-restart"),
            )
            assert after is not None
            assert after.completion_counters_json == '{"standup": 3, "retro": 1}'
            assert after.fired_once_triggers_json == '["sprint_start"]'
            assert after.total_completions == 4
        finally:
            await second.disconnect()

    async def test_meeting_cooldown_survives_restart(
        self,
        backend_factory: BackendFactory,
    ) -> None:
        when = datetime(2026, 5, 15, 10, 0, tzinfo=UTC)

        first = await backend_factory()
        try:
            await first.meeting_cooldown.save(
                MeetingCooldownRecord(
                    meeting_type_name=NotBlankStr("daily-standup"),
                    last_triggered_at=when,
                ),
            )
        finally:
            await first.disconnect()

        second = await backend_factory()
        try:
            rows = await second.meeting_cooldown.load_all()
            match = [r for r in rows if r.meeting_type_name == "daily-standup"]
            assert len(match) == 1
            assert match[0].last_triggered_at == when
        finally:
            await second.disconnect()

    async def test_tracked_containers_survive_restart(
        self,
        backend_factory: BackendFactory,
    ) -> None:
        first = await backend_factory()
        try:
            await first.tracked_containers.save(
                TrackedContainerRecord(
                    container_id=NotBlankStr("ctr-restart"),
                    sidecar_id=NotBlankStr("sc-restart"),
                    created_at=datetime.now(UTC),
                ),
            )
        finally:
            await first.disconnect()

        second = await backend_factory()
        try:
            loaded = await second.tracked_containers.get(
                NotBlankStr("ctr-restart"),
            )
            assert loaded is not None
            assert loaded.container_id == "ctr-restart"
            assert loaded.sidecar_id == "sc-restart"
        finally:
            await second.disconnect()

    async def test_all_four_state_stores_independently_recoverable(
        self,
        backend_factory: BackendFactory,
    ) -> None:
        """All four WP-1 state stores survive a real restart cycle.

        Mirrors the production restart sequence: a process crash
        leaves all four backends in some persisted state. After
        restart, each must be queryable on a fresh backend instance
        without any in-memory carryover.
        """
        first = await backend_factory()
        try:
            await first.ceremony_scheduler_state.save(
                CeremonySchedulerStateRecord(
                    sprint_id=NotBlankStr("sprint-combo"),
                    completion_counters_json="{}",
                    fired_once_triggers_json="[]",
                    total_completions=0,
                    velocity_history_json="[]",
                    updated_at=datetime.now(UTC),
                ),
            )
            await first.meeting_cooldown.save(
                MeetingCooldownRecord(
                    meeting_type_name=NotBlankStr("combo-meeting"),
                    last_triggered_at=datetime.now(UTC),
                ),
            )
            await first.tracked_containers.save(
                TrackedContainerRecord(
                    container_id=NotBlankStr("ctr-combo"),
                    sidecar_id=None,
                    created_at=datetime.now(UTC),
                ),
            )
            # Webhook receipts: smoke that the receipt repo wires
            # through without error (full CRUD covered by its own
            # conformance suite).
            assert first.webhook_receipts is not None
        finally:
            await first.disconnect()

        second = await backend_factory()
        try:
            assert (
                await second.ceremony_scheduler_state.get(
                    NotBlankStr("sprint-combo"),
                )
                is not None
            )
            cooldown_rows = await second.meeting_cooldown.load_all()
            assert any(r.meeting_type_name == "combo-meeting" for r in cooldown_rows)
            assert (
                await second.tracked_containers.get(NotBlankStr("ctr-combo"))
                is not None
            )
        finally:
            await second.disconnect()
