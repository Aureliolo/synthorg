"""Hermetic unit tests for PostgresPersistenceBackend lifecycle.

These tests mock ``psycopg_pool.AsyncConnectionPool`` so no real
Postgres (or Docker) is required.  Integration tests against a real
``testcontainers.postgres.PostgresContainer`` live in
``tests/integration/persistence/test_postgres_backend.py``.
"""

from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import psycopg
import pytest
from pydantic import SecretStr

from synthorg.core.persistence_errors import PersistenceConnectionError
from synthorg.persistence.config import PostgresConfig
from synthorg.persistence.postgres.backend import PostgresPersistenceBackend


def _cfg(**overrides: object) -> PostgresConfig:
    defaults: dict[str, object] = {
        "database": "synthorg",
        "username": "postgres",
        "password": SecretStr("s3cret"),
        "ssl_mode": "disable",  # unit tests never hit a real server
    }
    defaults.update(overrides)
    return PostgresConfig(**defaults)  # type: ignore[arg-type]


class _FakePoolFactory:
    """Factory that returns ``AsyncMock`` pools and tracks construction.

    Patches ``psycopg_pool.AsyncConnectionPool`` so
    ``PostgresPersistenceBackend`` can be exercised without libpq or
    a running database.
    """

    def __init__(self) -> None:
        self.pools: list[AsyncMock] = []
        self.last_conninfo: str | None = None
        self.last_kwargs: dict[str, Any] = {}

    def __call__(self, conninfo: str, **kwargs: Any) -> AsyncMock:
        self.last_conninfo = conninfo
        self.last_kwargs = kwargs
        pool = AsyncMock()
        pool.open = AsyncMock()
        pool.close = AsyncMock()
        # connection() must return an async context manager.
        conn = AsyncMock()
        conn.execute = AsyncMock()
        cursor = AsyncMock()
        cursor.execute = AsyncMock()
        cursor.fetchone = AsyncMock(return_value=(1,))
        cursor_cm = MagicMock()
        cursor_cm.__aenter__ = AsyncMock(return_value=cursor)
        cursor_cm.__aexit__ = AsyncMock(return_value=None)
        conn.cursor = MagicMock(return_value=cursor_cm)
        conn_cm = MagicMock()
        conn_cm.__aenter__ = AsyncMock(return_value=conn)
        conn_cm.__aexit__ = AsyncMock(return_value=None)
        pool.connection = MagicMock(return_value=conn_cm)
        self.pools.append(pool)
        return pool


@pytest.fixture
def fake_pool_factory() -> _FakePoolFactory:
    return _FakePoolFactory()


@pytest.fixture
def patched_pool(
    fake_pool_factory: _FakePoolFactory,
) -> Any:
    """Patch ``AsyncConnectionPool`` inside the postgres backend module."""
    with patch(
        "synthorg.persistence.postgres.backend_connection.AsyncConnectionPool",
        side_effect=fake_pool_factory,
    ) as patched:
        yield patched


@pytest.mark.unit
class TestConstructor:
    def test_accepts_config_without_connecting(
        self,
        patched_pool: Any,
        fake_pool_factory: _FakePoolFactory,
    ) -> None:
        cfg = _cfg()
        backend = PostgresPersistenceBackend(cfg)
        assert backend.is_connected is False
        assert backend.backend_name == "postgres"
        # Construction must not open a pool.
        assert fake_pool_factory.pools == []

    def test_stores_config(self, patched_pool: Any) -> None:
        cfg = _cfg(host="db.example.com", port=6543)
        backend = PostgresPersistenceBackend(cfg)
        assert backend._config.host == "db.example.com"
        assert backend._config.port == 6543


@pytest.mark.unit
class TestConnect:
    async def test_opens_pool_and_marks_connected(
        self,
        patched_pool: Any,
        fake_pool_factory: _FakePoolFactory,
    ) -> None:
        backend = PostgresPersistenceBackend(_cfg())
        await backend.connect()
        assert backend.is_connected is True
        assert len(fake_pool_factory.pools) == 1
        fake_pool_factory.pools[0].open.assert_awaited_once()

    async def test_builds_conninfo_from_config(
        self,
        patched_pool: Any,
        fake_pool_factory: _FakePoolFactory,
    ) -> None:
        backend = PostgresPersistenceBackend(
            _cfg(
                host="db.internal",
                port=6432,
                database="tenant_a",
                username="service",
                password=SecretStr("p@ss!"),
                ssl_mode="verify-full",
                application_name="synthorg-api",
                connect_timeout_seconds=5.0,
            )
        )
        await backend.connect()
        conninfo = fake_pool_factory.last_conninfo or ""
        assert "host=db.internal" in conninfo
        assert "port=6432" in conninfo
        assert "dbname=tenant_a" in conninfo
        assert "user=service" in conninfo
        assert "sslmode=verify-full" in conninfo
        assert "application_name=synthorg-api" in conninfo
        assert "connect_timeout=5" in conninfo

    async def test_passes_pool_sizing_to_pool(
        self,
        patched_pool: Any,
        fake_pool_factory: _FakePoolFactory,
    ) -> None:
        backend = PostgresPersistenceBackend(_cfg(pool_min_size=3, pool_max_size=15))
        await backend.connect()
        assert fake_pool_factory.last_kwargs["min_size"] == 3
        assert fake_pool_factory.last_kwargs["max_size"] == 15

    async def test_is_idempotent(
        self,
        patched_pool: Any,
        fake_pool_factory: _FakePoolFactory,
    ) -> None:
        backend = PostgresPersistenceBackend(_cfg())
        await backend.connect()
        await backend.connect()
        # Second connect must not open a new pool.
        assert len(fake_pool_factory.pools) == 1

    async def test_raises_on_pool_open_failure(
        self,
        patched_pool: Any,
        fake_pool_factory: _FakePoolFactory,
    ) -> None:
        # Make the next pool.open() raise to simulate a connection failure.
        original_call = fake_pool_factory.__call__

        def failing_factory(conninfo: str, **kwargs: Any) -> AsyncMock:
            pool = original_call(conninfo, **kwargs)
            pool.open = AsyncMock(side_effect=OSError("connection refused"))
            return pool

        with patch(
            "synthorg.persistence.postgres.backend_connection.AsyncConnectionPool",
            side_effect=failing_factory,
        ):
            backend = PostgresPersistenceBackend(_cfg())
            with pytest.raises(PersistenceConnectionError):
                await backend.connect()
            assert backend.is_connected is False


@pytest.mark.unit
class TestDisconnect:
    async def test_closes_pool_and_clears_state(
        self,
        patched_pool: Any,
        fake_pool_factory: _FakePoolFactory,
    ) -> None:
        backend = PostgresPersistenceBackend(_cfg())
        await backend.connect()
        pool = fake_pool_factory.pools[0]

        await backend.disconnect()

        pool.close.assert_awaited_once()
        assert backend.is_connected is False

    async def test_safe_when_never_connected(
        self,
        patched_pool: Any,
    ) -> None:
        backend = PostgresPersistenceBackend(_cfg())
        # Should not raise.
        await backend.disconnect()
        assert backend.is_connected is False

    async def test_idempotent(
        self,
        patched_pool: Any,
        fake_pool_factory: _FakePoolFactory,
    ) -> None:
        backend = PostgresPersistenceBackend(_cfg())
        await backend.connect()
        await backend.disconnect()
        await backend.disconnect()
        # Pool.close called exactly once.
        assert fake_pool_factory.pools[0].close.await_count == 1


@pytest.mark.unit
class TestHealthCheck:
    async def test_returns_false_when_disconnected(
        self,
        patched_pool: Any,
    ) -> None:
        backend = PostgresPersistenceBackend(_cfg())
        assert await backend.health_check() is False

    async def test_returns_true_when_select_one_succeeds(
        self,
        patched_pool: Any,
    ) -> None:
        backend = PostgresPersistenceBackend(_cfg())
        await backend.connect()
        assert await backend.health_check() is True

    async def test_returns_false_when_query_raises(
        self,
        patched_pool: Any,
        fake_pool_factory: _FakePoolFactory,
    ) -> None:
        backend = PostgresPersistenceBackend(_cfg())
        await backend.connect()
        # Make the next cursor execute raise a psycopg.Error -- that
        # is what the production code catches.
        pool = fake_pool_factory.pools[0]
        conn_cm = pool.connection.return_value
        conn = conn_cm.__aenter__.return_value
        cursor_cm = conn.cursor.return_value
        cursor = cursor_cm.__aenter__.return_value
        cursor.execute.side_effect = psycopg.OperationalError("connection reset")
        assert await backend.health_check() is False

    async def test_returns_false_when_pool_checkout_times_out(
        self,
        patched_pool: Any,
        fake_pool_factory: _FakePoolFactory,
    ) -> None:
        """pool.connection() hang is bounded by pool_timeout_seconds.

        The production code wraps the probe in asyncio.timeout so a
        stuck pool checkout surfaces as TimeoutError, which is caught
        and reported as unhealthy.
        """
        backend = PostgresPersistenceBackend(_cfg())
        await backend.connect()
        pool = fake_pool_factory.pools[0]
        # Replace pool.connection() with a context manager whose
        # __aenter__ never returns -- simulates pool exhaustion.
        stuck = AsyncMock()
        stuck.__aenter__ = AsyncMock(side_effect=TimeoutError("pool timeout"))
        stuck.__aexit__ = AsyncMock(return_value=None)
        pool.connection = MagicMock(return_value=stuck)
        assert await backend.health_check() is False


@pytest.mark.unit
class TestGetDb:
    async def test_returns_pool_when_connected(
        self,
        patched_pool: Any,
        fake_pool_factory: _FakePoolFactory,
    ) -> None:
        backend = PostgresPersistenceBackend(_cfg())
        await backend.connect()
        assert backend.get_db() is fake_pool_factory.pools[0]

    def test_raises_when_disconnected(
        self,
        patched_pool: Any,
    ) -> None:
        backend = PostgresPersistenceBackend(_cfg())
        with pytest.raises(PersistenceConnectionError):
            backend.get_db()


@pytest.mark.unit
class TestRepositoryPropertiesUnimplementedBranch:
    """The connected-but-repo-None branch surfaces a distinct error message.

    ``_require_connected`` distinguishes the disconnected case (pool is
    ``None``) from the case where the pool is open but a particular
    repository attribute has not been wired up yet. The latter only
    fires when a future port leaves a repository attribute as ``None``
    after ``connect()``, so the test stubs that state directly to
    cover the otherwise-unreachable branch.
    """

    async def test_raises_with_not_yet_implemented_message(
        self,
        patched_pool: Any,
    ) -> None:
        backend = PostgresPersistenceBackend(_cfg())
        await backend.connect()
        backend._tasks = None
        with pytest.raises(PersistenceConnectionError) as excinfo:
            _ = backend.tasks
        assert "not yet implemented" in str(excinfo.value)


@pytest.mark.unit
class TestRepositoryPropertiesRaiseWhenDisconnected:
    """Every repo property must raise when the pool is closed."""

    @pytest.mark.parametrize(
        "name",
        [
            "tasks",
            "cost_records",
            "messages",
            "lifecycle_events",
            "task_metrics",
            "collaboration_metrics",
            "parked_contexts",
            "audit_entries",
            "decision_records",
            "users",
            "api_keys",
            "checkpoints",
            "heartbeats",
            "agent_states",
            "settings",
            "artifacts",
            "projects",
            "custom_presets",
            "workflow_definitions",
            "workflow_executions",
            "workflow_versions",
            "identity_versions",
            "evaluation_config_versions",
            "budget_config_versions",
            "company_versions",
            "role_versions",
            "risk_overrides",
            "ssrf_violations",
            "circuit_breaker_state",
        ],
    )
    def test_property_raises(
        self,
        patched_pool: Any,
        name: str,
    ) -> None:
        backend = PostgresPersistenceBackend(_cfg())
        with pytest.raises(PersistenceConnectionError):
            getattr(backend, name)


@pytest.mark.unit
class TestMigrate:
    async def test_raises_when_disconnected(
        self,
        patched_pool: Any,
    ) -> None:
        backend = PostgresPersistenceBackend(_cfg())
        with pytest.raises(PersistenceConnectionError):
            await backend.migrate()

    async def test_calls_atlas_with_postgres_backend(
        self,
        patched_pool: Any,
    ) -> None:
        backend = PostgresPersistenceBackend(_cfg())
        await backend.connect()

        with patch(
            "synthorg.persistence.postgres.backend_migration.atlas.migrate_apply",
            new=AsyncMock(return_value=None),
        ) as migrate_apply:
            await backend.migrate()

        migrate_apply.assert_awaited_once()
        await_args = migrate_apply.await_args
        assert await_args is not None
        assert await_args.kwargs["backend"] == "postgres"
        # Positional arg 0 is the db URL built from the PostgresConfig.
        url = cast("str", await_args.args[0])
        assert url.startswith("postgres://")
        assert "synthorg" in url  # database name


@pytest.mark.unit
class TestBackendName:
    def test_is_postgres(self, patched_pool: Any) -> None:
        backend = PostgresPersistenceBackend(_cfg())
        assert backend.backend_name == "postgres"
