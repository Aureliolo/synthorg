"""The construction phase hands the backup factory the backend it built.

``build_backup_service`` honouring a supplied backend is covered in
``tests/unit/backup/test_factory.py``. This module covers the seam one level
up, which is the one that actually broke: the boot call site passing the
backend at all. Without a test here, dropping the argument silently restores a
Postgres deployment backing up a SQLite file that does not exist, and every
factory-level test keeps passing.
"""

from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import SecretStr

from synthorg.api.app_overrides import AppOverrides
from synthorg.api.boot_persistence import BootPersistence
from synthorg.api.config import ApiConfig
from synthorg.api.construction_phase import build_construction_services
from synthorg.backup.config import BackupConfig
from synthorg.backup.factory import (
    build_backup_service as real_build_backup_service,
)
from synthorg.backup.handlers.postgres_persistence import (
    PostgresPersistenceComponentHandler,
)
from synthorg.backup.models import BackupComponent
from synthorg.config.schema import RootConfig
from synthorg.persistence.config import PersistenceConfig, PostgresConfig
from synthorg.persistence.factory import create_backend
from synthorg.persistence.protocol import PersistenceBackend


def _env_driven_postgres_backend() -> PersistenceBackend:
    """Build the backend an env-driven boot would produce.

    ``create_backend`` returns a disconnected instance, so this touches no
    database.

    Returns:
        A disconnected ``PostgresBackend`` pointed at ``live_db``.
    """
    return create_backend(
        PersistenceConfig(
            backend="postgres",
            postgres=PostgresConfig(
                host="db.example.test",
                database="live_db",
                username="synthorg",
                password=SecretStr("hunter2"),
            ),
        ),
    )


def _boot(backend: PersistenceBackend | None, tmp_path: Path) -> BootPersistence:
    """Assemble the boot bundle for an env-driven Postgres deployment.

    ``resolved_db_path`` stays ``None`` and ``db_url`` is set, mirroring the
    compose template: the YAML names no database at all.

    Returns:
        The ``BootPersistence`` bundle the construction phase consumes.
    """
    return BootPersistence(
        persistence=backend,
        artifact_storage=None,
        resolved_db_path=None,
        resolved_config_path=tmp_path / "company.yaml",
        db_url="postgresql://synthorg@db.example.test/live_db",
        db_path="",
    )


def _config(tmp_path: Path) -> RootConfig:
    """Build a config whose persistence block is left at its defaults.

    This is the deployment shape that broke: ``backend`` is ``sqlite`` and
    ``postgres`` is ``None`` while a Postgres backend is live.

    Returns:
        A ``RootConfig`` requesting a persistence backup.
    """
    return RootConfig(
        company_name="test-co",
        backup=BackupConfig(
            include=(BackupComponent.PERSISTENCE,),
            path=str(tmp_path / "backups"),
        ),
    )


@pytest.mark.unit
class TestBackupBackendReachesTheFactory:
    """The boot call site is what binds the backup handler to reality."""

    def test_construction_binds_the_postgres_handler(self, tmp_path: Path) -> None:
        config = _config(tmp_path)
        assert config.persistence.backend == "sqlite"
        assert config.persistence.postgres is None

        with (
            # ``tests/unit/api/conftest.py`` stubs the factory to ``None`` for
            # every API unit test, so the real one has to be restored here or
            # this asserts against a stub. ``ensure_pg_tools_available`` is
            # patched because the factory verifies ``pg_dump`` is on PATH at
            # dispatch, and a workstation running the unit tier has no
            # postgresql-client installed.
            patch(
                "synthorg.api.construction_phase.build_backup_service",
                real_build_backup_service,
            ),
            patch(
                "synthorg.backup.registry.ensure_pg_tools_available",
                return_value=None,
            ),
        ):
            result = build_construction_services(
                effective_config=config,
                api_config=ApiConfig(),
                overrides=AppOverrides(),
                boot=_boot(_env_driven_postgres_backend(), tmp_path),
            )

        assert result.backup_service is not None
        handler = result.backup_service.handlers[BackupComponent.PERSISTENCE]
        assert isinstance(handler, PostgresPersistenceComponentHandler)
        assert handler.database == "live_db"

    def test_construction_passes_the_boot_backend_itself(self, tmp_path: Path) -> None:
        """The identity check, so a paraphrased argument cannot pass either."""
        backend = _env_driven_postgres_backend()

        with patch(
            "synthorg.api.construction_phase.build_backup_service",
            return_value=None,
        ) as spy:
            build_construction_services(
                effective_config=_config(tmp_path),
                api_config=ApiConfig(),
                overrides=AppOverrides(),
                boot=_boot(backend, tmp_path),
            )

        assert spy.call_args.kwargs["boot_backend"] is backend

    def test_a_persistence_less_boot_passes_none(self, tmp_path: Path) -> None:
        with patch(
            "synthorg.api.construction_phase.build_backup_service",
            return_value=None,
        ) as spy:
            build_construction_services(
                effective_config=_config(tmp_path),
                api_config=ApiConfig(),
                overrides=AppOverrides(),
                boot=_boot(None, tmp_path),
            )

        assert spy.call_args.kwargs["boot_backend"] is None
