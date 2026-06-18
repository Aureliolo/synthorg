"""Tests for the backup service factory.

The factory always constructs a real ``BackupService`` so the
registered ``backup.*`` settings have a live consumer at boot;
``BackupService.start()`` honours the ``enabled`` flag internally
without needing the factory to early-return ``None``.
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
from synthorg.persistence.config import PersistenceConfig, PostgresConfig


@pytest.mark.unit
class TestBuildBackupService:
    """build_backup_service always constructs a real service."""

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

        ``SYNTHORG_CONFIG_PATH`` is read exactly once at app boot
        (``api/app.py``) and the resolved ``Path`` is threaded in as
        ``resolved_config_path`` -- this module never re-reads the env
        var (config-soup dedupe, RFC#4 section 6).
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
