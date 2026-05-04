"""Fresh-install Postgres end-to-end integration test.

Closes the coverage gap surfaced by issue #1443: until the dual-backend
auth stores shipped, ``synthorg start --persistence-backend postgres`` on
a freshly-provisioned container crashed with an ``AttributeError`` at
``LockoutStore(pool)``.  The existing Postgres integration tests exercise
the repository layer; this one drives the full lifecycle wire-up so any
regression in `_build_*_store` dispatch, protocol-compat, or migration
ordering trips a loud test failure.

The test uses the programmatic lifecycle entry point rather than spawning
a subprocess -- all the failure modes (import errors, handle-type
mismatches, Atlas migration failures) surface synchronously and are easy
to diagnose.
"""

import re
from typing import TYPE_CHECKING

import pytest

from synthorg.persistence.postgres.session_repo import (
    PostgresSessionRepository as PostgresSessionStore,
)

pytestmark = [pytest.mark.integration, pytest.mark.slow]

if TYPE_CHECKING:
    from synthorg.persistence.postgres.backend import PostgresPersistenceBackend


class TestFreshInstallPostgresLifecycle:
    """Exercise the full persistence wire-up on a freshly-migrated Postgres db."""

    async def test_dual_backend_stores_instantiate(
        self,
        postgres_backend: PostgresPersistenceBackend,
    ) -> None:
        """Every auth store must instantiate against AsyncConnectionPool.

        Before #1443, ``LockoutStore`` and ``RefreshStore`` were hardcoded
        to ``aiosqlite.Connection`` and crashed at construction when
        ``PersistenceBackend.get_db()`` returned the Postgres pool.  The
        dispatchers in ``synthorg.api.lifecycle`` now pick the concrete
        ``Postgres*`` variant via type dispatch; this test proves the
        happy path end-to-end.
        """
        from synthorg.core.auth.config import AuthConfig
        from synthorg.persistence.postgres.lockout_repo import (
            PostgresLockoutRepository,
        )
        from synthorg.persistence.postgres.refresh_repo import (
            PostgresRefreshTokenRepository,
        )

        session_store = postgres_backend.sessions
        lockout_store = postgres_backend.build_lockouts(AuthConfig())
        refresh_store = postgres_backend.refresh_tokens

        assert isinstance(session_store, PostgresSessionStore)
        assert isinstance(lockout_store, PostgresLockoutRepository)
        assert isinstance(refresh_store, PostgresRefreshTokenRepository)

        await session_store.load_revoked()
        await lockout_store.load_locked()

    async def test_currency_column_present_on_fresh_migration(
        self,
        postgres_backend: PostgresPersistenceBackend,
    ) -> None:
        """Phase 5 migration must stamp currency on cost_records, task_metrics,
        and agent_states.  The Postgres post-migrate path applies the
        currency column + approvals + custom_rules tables in one shot; this
        verifies every new column exists and defaults to 'USD'.
        """
        pool = postgres_backend.get_db()
        async with pool.connection() as conn, conn.cursor() as cur:
            for table in ("cost_records", "task_metrics", "agent_states"):
                await cur.execute(
                    "SELECT column_name, is_nullable, column_default "
                    "FROM information_schema.columns "
                    "WHERE table_name = %s AND column_name = 'currency'",
                    (table,),
                )
                row = await cur.fetchone()
                assert row is not None, (
                    f"Table {table!r} is missing the currency column"
                )
                _, is_nullable, default = row
                assert is_nullable == "NO", f"{table}.currency must be NOT NULL"
                # Postgres ``column_default`` rendering is
                # ``'USD'::text`` for a literal text default.  Match the
                # quoted literal precisely rather than any occurrence of
                # the three letters, which would pass for
                # ``coalesce(op_get_currency(), 'USD')`` style defaults
                # that we do not want to be installed.
                assert re.search(r"'USD'(::|$)", default or ""), (
                    f"{table}.currency must default to 'USD', got {default!r}"
                )

    async def test_approvals_and_custom_rules_tables_created(
        self,
        postgres_backend: PostgresPersistenceBackend,
    ) -> None:
        """approvals and custom_rules existed only in SQLite before #1443.

        The new Postgres migration adds them with translated types.  This
        test confirms both tables are present after a fresh migration so
        controllers that query them do not 42P01 on first boot.
        """
        pool = postgres_backend.get_db()
        async with pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' "
                "AND table_name IN ('approvals', 'custom_rules') "
                "ORDER BY table_name"
            )
            rows = await cur.fetchall()
        table_names = {row[0] for row in rows}
        assert table_names == {"approvals", "custom_rules"}
