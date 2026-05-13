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

    def test_returns_backup_service_when_disabled_by_default(
        self,
        tmp_path: Path,
    ) -> None:
        """Disabled-by-default config still yields a real BackupService."""
        config = RootConfig(company_name="test-co")
        assert config.backup.enabled is False

        service = build_backup_service(
            config,
            resolved_db_path=tmp_path / "synthorg.db",
            resolved_config_path=tmp_path / "company.yaml",
        )

        assert isinstance(service, BackupService)
        assert service.scheduler.is_running is False

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

        handlers = build_backup_handlers(config, backup_config)

        assert isinstance(
            handlers[BackupComponent.PERSISTENCE],
            PostgresPersistenceComponentHandler,
        )
