"""Tests for the backup service factory.

The ``enabled`` flag alone never makes the factory return ``None``: it
constructs a real ``BackupService`` regardless, so the registered ``backup.*``
settings have a live consumer at boot, and ``BackupService.start()`` honours
``enabled`` internally. A genuine handler-construction failure still yields
``None``, which is why callers null-check.
"""

from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import SecretStr

from synthorg.backup.config import BackupConfig
from synthorg.backup.factory import build_backup_handlers, build_backup_service
from synthorg.backup.handlers.config_handler import ConfigComponentHandler
from synthorg.backup.handlers.memory import MemoryComponentHandler
from synthorg.backup.handlers.postgres_persistence import (
    PostgresPersistenceComponentHandler,
)
from synthorg.backup.handlers.sqlite_persistence import (
    SQLitePersistenceComponentHandler,
)
from synthorg.backup.models import BackupComponent
from synthorg.backup.service import BackupService
from synthorg.config.schema import RootConfig
from synthorg.persistence.config import (
    PersistenceConfig,
    PostgresConfig,
    SQLiteConfig,
)
from synthorg.persistence.factory import create_backend
from synthorg.persistence.protocol import PersistenceBackend


@pytest.mark.unit
class TestBuildBackupService:
    """The ``enabled`` flag alone never causes an early ``None`` return."""

    def test_returns_backup_service_when_disabled(
        self,
        tmp_path: Path,
    ) -> None:
        """An explicitly-disabled config still yields a real BackupService."""
        config = RootConfig(
            company_name="test-co",
            backup=BackupConfig(enabled=False),
        )
        assert config.backup.enabled is False

        service = build_backup_service(
            config,
            resolved_db_path=tmp_path / "synthorg.db",
            resolved_config_path=tmp_path / "company.yaml",
        )

        assert isinstance(service, BackupService)
        assert service.scheduler.is_running is False

    def test_enabled_by_default(self) -> None:
        """Backups are enabled by default (data-safety out of the box)."""
        config = RootConfig(company_name="test-co")
        assert config.backup.enabled is True

    def test_returns_backup_service_when_enabled(self, tmp_path: Path) -> None:
        """Explicit ``backup.enabled=true`` still returns a real service."""
        config = RootConfig(
            company_name="test-co",
            backup=BackupConfig(enabled=True, path=str(tmp_path / "backups")),
        )

        service = build_backup_service(
            config,
            resolved_db_path=tmp_path / "synthorg.db",
            resolved_config_path=tmp_path / "company.yaml",
        )

        assert isinstance(service, BackupService)

    def test_returns_none_when_handler_construction_fails(
        self,
        tmp_path: Path,
    ) -> None:
        """Genuine handler-build failures still surface as ``None``."""
        config = RootConfig(company_name="test-co")

        with patch(
            "synthorg.backup.factory.build_backup_handlers",
            side_effect=ValueError("synthetic handler-build failure"),
        ):
            service = build_backup_service(
                config,
                resolved_db_path=tmp_path / "synthorg.db",
                resolved_config_path=tmp_path / "company.yaml",
            )

        assert service is None


@pytest.mark.unit
class TestBackendPluggableHandlers:
    """build_backup_handlers dispatches the persistence handler by backend."""

    def test_sqlite_backend_picks_sqlite_handler(self, tmp_path: Path) -> None:
        config = RootConfig(company_name="test-co")
        backup_config = BackupConfig(include=(BackupComponent.PERSISTENCE,))

        handlers = build_backup_handlers(
            config,
            backup_config,
            resolved_db_path=tmp_path / "synthorg.db",
        )

        assert isinstance(
            handlers[BackupComponent.PERSISTENCE],
            SQLitePersistenceComponentHandler,
        )

    def test_postgres_backend_picks_postgres_handler(self) -> None:
        config = RootConfig(
            company_name="test-co",
            persistence=PersistenceConfig(
                backend="postgres",
                postgres=PostgresConfig(
                    database="synthorg",
                    username="synthorg",
                    password=SecretStr("hunter2"),
                ),
            ),
        )
        backup_config = BackupConfig(include=(BackupComponent.PERSISTENCE,))

        # The factory dispatch verifies ``pg_dump`` / ``pg_restore`` are
        # on PATH so missing tooling surfaces during registration
        # instead of the first backup attempt; unit tests run on
        # workstations / CI workers without the postgres-client package
        # installed, so the binary check is patched to a no-op here.
        with patch(
            "synthorg.backup.registry.ensure_pg_tools_available",
            return_value=None,
        ):
            handlers = build_backup_handlers(config, backup_config)

        assert isinstance(
            handlers[BackupComponent.PERSISTENCE],
            PostgresPersistenceComponentHandler,
        )


def _postgres_backend(database: str = "synthorg") -> PersistenceBackend:
    """Build a real, disconnected Postgres backend.

    A real instance rather than a double because ``kind`` and ``config`` are
    exactly the seam under test, and ``create_backend`` returns a disconnected
    object so nothing here touches a database.

    Returns:
        A disconnected ``PostgresBackend``.
    """
    return create_backend(
        PersistenceConfig(
            backend="postgres",
            postgres=PostgresConfig(
                host="db.example.test",
                database=database,
                username="synthorg",
                password=SecretStr("hunter2"),
            ),
        ),
    )


def _sqlite_backend(path: Path) -> PersistenceBackend:
    """Build a real, disconnected SQLite backend.

    Returns:
        A disconnected ``SQLiteBackend``.
    """
    return create_backend(
        PersistenceConfig(backend="sqlite", sqlite=SQLiteConfig(path=str(path))),
    )


@pytest.mark.unit
class TestBootBackendWinsOverConfig:
    """The handler follows the backend built at boot, not the YAML's intent.

    An env-driven deployment (``SYNTHORG_DATABASE_URL``) assembles its backend
    from a boot config built in ``api/boot_persistence`` and never writes that
    choice back into ``RootConfig``, whose ``persistence.backend`` defaults to
    ``sqlite`` and whose ``postgres`` block stays ``None``. Dispatching on the
    config alone hands a Postgres deployment a SQLite handler, and every
    scheduled backup then fails on a database file that does not exist.
    Backing up the wrong database is worse than not backing up, so reality
    wins over intent.
    """

    def test_boot_postgres_overrides_sqlite_config(self) -> None:
        config = RootConfig(
            company_name="test-co",
            persistence=PersistenceConfig(
                postgres=PostgresConfig(
                    database="synthorg",
                    username="synthorg",
                    password=SecretStr("hunter2"),
                ),
            ),
        )
        assert config.persistence.backend == "sqlite"
        backup_config = BackupConfig(include=(BackupComponent.PERSISTENCE,))

        with patch(
            "synthorg.backup.registry.ensure_pg_tools_available",
            return_value=None,
        ):
            handlers = build_backup_handlers(
                config,
                backup_config,
                boot_backend=_postgres_backend(),
            )

        assert isinstance(
            handlers[BackupComponent.PERSISTENCE],
            PostgresPersistenceComponentHandler,
        )

    def test_postgres_handler_uses_the_boot_connection_details(self) -> None:
        """The config's Postgres block is empty on the deployment this serves.

        ``SYNTHORG_DATABASE_URL`` is parsed into a boot config that never
        reaches ``RootConfig``, so ``persistence.postgres`` is ``None`` while a
        Postgres backend is live. Reading the details off the config alone
        raises and drops the whole service, and reading them off a *stale*
        config block would dump a different database and report success.
        """
        config = RootConfig(company_name="test-co")
        assert config.persistence.postgres is None
        backup_config = BackupConfig(include=(BackupComponent.PERSISTENCE,))

        with patch(
            "synthorg.backup.registry.ensure_pg_tools_available",
            return_value=None,
        ):
            handlers = build_backup_handlers(
                config,
                backup_config,
                boot_backend=_postgres_backend(database="live_db"),
            )

        handler = handlers[BackupComponent.PERSISTENCE]
        assert isinstance(handler, PostgresPersistenceComponentHandler)
        assert handler._config.database == "live_db"

    def test_boot_details_outrank_a_stale_config_block(self) -> None:
        """A half-migrated YAML must not redirect the dump."""
        config = RootConfig(
            company_name="test-co",
            persistence=PersistenceConfig(
                backend="postgres",
                postgres=PostgresConfig(
                    host="stale.example.test",
                    database="stale_db",
                    username="synthorg",
                    password=SecretStr("hunter2"),
                ),
            ),
        )
        backup_config = BackupConfig(include=(BackupComponent.PERSISTENCE,))

        with patch(
            "synthorg.backup.registry.ensure_pg_tools_available",
            return_value=None,
        ):
            handlers = build_backup_handlers(
                config,
                backup_config,
                boot_backend=_postgres_backend(database="live_db"),
            )

        handler = handlers[BackupComponent.PERSISTENCE]
        assert isinstance(handler, PostgresPersistenceComponentHandler)
        assert handler._config.database == "live_db"
        assert handler._config.host == "db.example.test"

    def test_boot_sqlite_overrides_postgres_config(self, tmp_path: Path) -> None:
        config = RootConfig(
            company_name="test-co",
            persistence=PersistenceConfig(
                backend="postgres",
                postgres=PostgresConfig(
                    database="synthorg",
                    username="synthorg",
                    password=SecretStr("hunter2"),
                ),
            ),
        )
        backup_config = BackupConfig(include=(BackupComponent.PERSISTENCE,))

        handlers = build_backup_handlers(
            config,
            backup_config,
            resolved_db_path=tmp_path / "synthorg.db",
            boot_backend=_sqlite_backend(tmp_path / "synthorg.db"),
        )

        assert isinstance(
            handlers[BackupComponent.PERSISTENCE],
            SQLitePersistenceComponentHandler,
        )

    def test_boot_sqlite_path_is_used_without_a_resolved_path(
        self,
        tmp_path: Path,
    ) -> None:
        """The live backend's own path beats the config's default."""
        live_db = tmp_path / "live.db"
        config = RootConfig(company_name="test-co")
        backup_config = BackupConfig(include=(BackupComponent.PERSISTENCE,))

        handlers = build_backup_handlers(
            config,
            backup_config,
            boot_backend=_sqlite_backend(live_db),
        )

        handler = handlers[BackupComponent.PERSISTENCE]
        assert isinstance(handler, SQLitePersistenceComponentHandler)
        assert handler._db_path == live_db

    def test_falls_back_to_config_when_no_backend_was_built(
        self,
        tmp_path: Path,
    ) -> None:
        """A persistence-less boot has no reality to defer to."""
        config = RootConfig(company_name="test-co")
        backup_config = BackupConfig(include=(BackupComponent.PERSISTENCE,))

        handlers = build_backup_handlers(
            config,
            backup_config,
            resolved_db_path=tmp_path / "synthorg.db",
            boot_backend=None,
        )

        assert isinstance(
            handlers[BackupComponent.PERSISTENCE],
            SQLitePersistenceComponentHandler,
        )

    def test_service_threads_the_boot_backend_through(self, tmp_path: Path) -> None:
        """``build_backup_service`` is the seam ``construction_phase`` calls."""
        config = RootConfig(
            company_name="test-co",
            backup=BackupConfig(
                include=(BackupComponent.PERSISTENCE,),
                path=str(tmp_path / "backups"),
            ),
        )

        with patch(
            "synthorg.backup.registry.ensure_pg_tools_available",
            return_value=None,
        ):
            service = build_backup_service(
                config,
                boot_backend=_postgres_backend(database="live_db"),
            )

        assert service is not None
        handler = service.handlers[BackupComponent.PERSISTENCE]
        assert isinstance(handler, PostgresPersistenceComponentHandler)
        assert handler.database == "live_db"


@pytest.mark.unit
class TestBackupComponentDispatch:
    """build_backup_handlers covers every BackupComponent branch."""

    def test_memory_component_dispatches_memory_handler(
        self,
        tmp_path: Path,
    ) -> None:
        """MEMORY in ``include`` yields a MemoryComponentHandler."""
        config = RootConfig(company_name="test-co")
        backup_config = BackupConfig(include=(BackupComponent.MEMORY,))

        handlers = build_backup_handlers(
            config,
            backup_config,
            resolved_db_path=tmp_path / "synthorg.db",
        )

        assert isinstance(handlers[BackupComponent.MEMORY], MemoryComponentHandler)

    def test_config_component_uses_resolved_path_when_supplied(
        self,
        tmp_path: Path,
    ) -> None:
        """``resolved_config_path`` overrides the env-var fallback."""
        config = RootConfig(company_name="test-co")
        backup_config = BackupConfig(include=(BackupComponent.CONFIG,))
        explicit = tmp_path / "explicit-company.yaml"

        handlers = build_backup_handlers(
            config,
            backup_config,
            resolved_config_path=explicit,
        )

        handler = handlers[BackupComponent.CONFIG]
        assert isinstance(handler, ConfigComponentHandler)
        assert handler._config_path == explicit

    def test_config_component_uses_injected_resolved_path(
        self,
        tmp_path: Path,
    ) -> None:
        """The factory consumes the resolved path injected by boot.

        ``SYNTHORG_CONFIG_PATH`` is read exactly once, in
        ``api/boot_persistence``, and the resolved ``Path`` is threaded in as
        ``resolved_config_path`` -- the factory never re-reads the env var.
        """
        config = RootConfig(company_name="test-co")
        backup_config = BackupConfig(include=(BackupComponent.CONFIG,))
        resolved = tmp_path / "resolved-company.yaml"

        handlers = build_backup_handlers(
            config,
            backup_config,
            resolved_config_path=resolved,
        )

        handler = handlers[BackupComponent.CONFIG]
        assert isinstance(handler, ConfigComponentHandler)
        assert handler._config_path == resolved

    def test_config_component_defaults_to_company_yaml(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Missing env var and resolved path leaves ``company.yaml``."""
        config = RootConfig(company_name="test-co")
        backup_config = BackupConfig(include=(BackupComponent.CONFIG,))
        monkeypatch.delenv("SYNTHORG_CONFIG_PATH", raising=False)

        handlers = build_backup_handlers(config, backup_config)

        handler = handlers[BackupComponent.CONFIG]
        assert isinstance(handler, ConfigComponentHandler)
        assert handler._config_path == Path("company.yaml")


@pytest.mark.unit
class TestBuildBackupServiceErrorPropagation:
    """The fatal-error re-raise contract is exercised explicitly."""

    def test_memory_error_propagates_without_logging(
        self,
        tmp_path: Path,
    ) -> None:
        """``MemoryError`` from handler-build is re-raised, not swallowed."""
        config = RootConfig(company_name="test-co")

        with (
            patch(
                "synthorg.backup.factory.build_backup_handlers",
                side_effect=MemoryError("out of memory"),
            ),
            pytest.raises(MemoryError, match="out of memory"),
        ):
            build_backup_service(
                config,
                resolved_db_path=tmp_path / "synthorg.db",
                resolved_config_path=tmp_path / "company.yaml",
            )

    def test_recursion_error_propagates_without_logging(
        self,
        tmp_path: Path,
    ) -> None:
        """``RecursionError`` is re-raised so the interpreter unwinds."""
        config = RootConfig(company_name="test-co")

        with (
            patch(
                "synthorg.backup.factory.build_backup_handlers",
                side_effect=RecursionError("stack overflow"),
            ),
            pytest.raises(RecursionError, match="stack overflow"),
        ):
            build_backup_service(
                config,
                resolved_db_path=tmp_path / "synthorg.db",
                resolved_config_path=tmp_path / "company.yaml",
            )
