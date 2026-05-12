"""Tests for persistence backend factory."""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import SecretStr

from synthorg.core.persistence_errors import PersistenceConnectionError
from synthorg.persistence.config import (
    PersistenceConfig,
    PostgresConfig,
    SQLiteConfig,
)
from synthorg.persistence.factory import create_backend
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.persistence.sqlite.backend import SQLitePersistenceBackend


def _minimal_postgres_config() -> PostgresConfig:
    return PostgresConfig(
        database="synthorg",
        username="postgres",
        password=SecretStr("s3cret"),
    )


@pytest.mark.unit
class TestCreateBackend:
    def test_creates_sqlite_backend(self) -> None:
        config = PersistenceConfig(
            backend="sqlite",
            sqlite=SQLiteConfig(path=":memory:"),
        )
        backend = create_backend(config)
        assert isinstance(backend, SQLitePersistenceBackend)
        assert backend.backend_name == "sqlite"
        assert backend.is_connected is False

    def test_returns_protocol_type(self) -> None:
        config = PersistenceConfig(
            sqlite=SQLiteConfig(path=":memory:"),
        )
        backend = create_backend(config)
        assert isinstance(backend, PersistenceBackend)

    def test_passes_sqlite_config(self) -> None:
        config = PersistenceConfig(
            sqlite=SQLiteConfig(path="data/company.db", wal_mode=False),
        )
        backend = create_backend(config)
        assert isinstance(backend, SQLitePersistenceBackend)

    def test_unknown_backend_raises(self) -> None:
        """Bypass validation via model_copy to test the factory guard."""
        config = PersistenceConfig()
        bad_config = config.model_copy(update={"backend": "cassandra"})
        with pytest.raises(
            PersistenceConnectionError,
            match="Unknown persistence backend",
        ):
            create_backend(bad_config)

    def test_postgres_backend_dispatches_to_postgres_class(self) -> None:
        """The factory routes backend='postgres' to PostgresPersistenceBackend.

        Construction must not open a pool -- that happens on connect().
        """
        from synthorg.persistence.postgres.backend import (
            PostgresPersistenceBackend,
        )

        config = PersistenceConfig(
            backend="postgres",
            postgres=_minimal_postgres_config(),
        )
        backend = create_backend(config)
        assert isinstance(backend, PostgresPersistenceBackend)
        assert backend.backend_name == "postgres"
        assert backend.is_connected is False

    def test_postgres_backend_missing_config_raises(self) -> None:
        """Factory guard catches missing postgres config after model_copy bypass."""
        config = PersistenceConfig(
            backend="postgres",
            postgres=_minimal_postgres_config(),
        )
        bad_config = config.model_copy(update={"postgres": None})
        with pytest.raises(
            PersistenceConnectionError,
            match="requires a PostgresConfig",
        ):
            create_backend(bad_config)

    def test_postgres_backend_missing_extra_raises(self) -> None:
        """Simulate the 'postgres' optional extra not being installed."""
        config = PersistenceConfig(
            backend="postgres",
            postgres=_minimal_postgres_config(),
        )
        # Remove the already-imported postgres backend module so the
        # deferred import inside create_backend raises ImportError.
        with patch.dict(sys.modules, {}, clear=False):
            sys.modules.pop("synthorg.persistence.postgres.backend", None)
            sys.modules.pop("synthorg.persistence.postgres", None)
            sys.modules["synthorg.persistence.postgres"] = None  # type: ignore[assignment]
            with pytest.raises(
                PersistenceConnectionError,
                match="requires the 'postgres' extra",
            ):
                create_backend(config)

    async def test_multi_tenancy_separate_databases(
        self,
        tmp_path: Path,
    ) -> None:
        """Each company config creates an isolated backend instance.

        Migrations are applied via ``migrations.migrate_apply`` against
        a per-test copy of the revisions directory.  Each xdist worker
        already has its own SQLite file (per ``tmp_path``), so yoyo's
        DB-level lock cannot contend across workers; the per-test
        revisions copy keeps the on-disk layout symmetric with
        production.
        """
        from synthorg.persistence import migrations

        path_a = str(tmp_path / "company-a.db")
        path_b = str(tmp_path / "company-b.db")

        rev_path = migrations.copy_revisions(tmp_path / "revisions")
        await migrations.migrate_apply(
            migrations.to_sqlite_url(path_a),
            revisions_path=rev_path,
        )
        await migrations.migrate_apply(
            migrations.to_sqlite_url(path_b),
            revisions_path=rev_path,
        )

        config_a = PersistenceConfig(sqlite=SQLiteConfig(path=path_a))
        config_b = PersistenceConfig(sqlite=SQLiteConfig(path=path_b))

        backend_a = create_backend(config_a)
        backend_b = create_backend(config_b)

        # They are separate instances
        assert backend_a is not backend_b

        # Each can connect against its already-migrated database
        await backend_a.connect()
        await backend_b.connect()

        # Verify isolation -- data in one doesn't affect the other
        from tests.unit.persistence.conftest import make_task

        await backend_a.tasks.save(make_task(task_id="company-a-task"))
        assert await backend_a.tasks.get("company-a-task") is not None
        assert await backend_b.tasks.get("company-a-task") is None

        await backend_a.disconnect()
        await backend_b.disconnect()
