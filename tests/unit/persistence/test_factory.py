"""Tests for persistence backend factory."""

import pytest

from synthorg.persistence.config import PersistenceConfig, SQLiteConfig
from synthorg.persistence.errors import PersistenceConnectionError
from synthorg.persistence.factory import create_backend
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.persistence.sqlite.backend import SQLitePersistenceBackend


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
        bad_config = config.model_copy(update={"backend": "postgres"})
        with pytest.raises(
            PersistenceConnectionError,
            match="Unknown persistence backend",
        ):
            create_backend(bad_config)

    async def test_multi_tenancy_separate_databases(self) -> None:
        """Each company config creates an isolated backend instance."""
        config_a = PersistenceConfig(
            sqlite=SQLiteConfig(path=":memory:"),
        )
        config_b = PersistenceConfig(
            sqlite=SQLiteConfig(path=":memory:"),
        )

        backend_a = create_backend(config_a)
        backend_b = create_backend(config_b)

        # They are separate instances
        assert backend_a is not backend_b

        # Each can connect and operate independently
        await backend_a.connect()
        await backend_a.migrate()
        await backend_b.connect()
        await backend_b.migrate()

        # Verify isolation — data in one doesn't affect the other
        from tests.unit.persistence.conftest import make_task

        await backend_a.tasks.save(make_task(task_id="company-a-task"))
        assert await backend_a.tasks.get("company-a-task") is not None
        assert await backend_b.tasks.get("company-a-task") is None

        await backend_a.disconnect()
        await backend_b.disconnect()
