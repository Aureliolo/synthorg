"""Tests for PostgresPersistenceComponentHandler.

Mocks the ``pg_dump`` / ``pg_restore`` helpers from
``synthorg.persistence.postgres.backup_utils`` so the unit suite never
shells out to a real ``pg_dump`` binary (which may or may not exist on
CI workers). Integration coverage of the real subprocess invocation
lives in ``tests/conformance/persistence/`` once Docker / Postgres are
available.
"""

from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import SecretStr

from synthorg.backup.errors import ComponentBackupError
from synthorg.backup.handlers.postgres_persistence import (
    PostgresPersistenceComponentHandler,
)
from synthorg.backup.models import BackupComponent
from synthorg.persistence.config import PostgresConfig
from synthorg.persistence.postgres.backup_utils import (
    PgToolFailedError,
    PgToolUnavailableError,
)


def _make_config() -> PostgresConfig:
    """Return a PostgresConfig with a redacted dummy password."""
    return PostgresConfig(
        host="db.example.com",
        port=5432,
        database="synthorg",
        username="synthorg",
        password=SecretStr("hunter2"),
    )


@pytest.mark.unit
class TestComponentProperty:
    """PostgresPersistenceComponentHandler.component returns PERSISTENCE."""

    def test_returns_persistence(self) -> None:
        handler = PostgresPersistenceComponentHandler(_make_config())
        assert handler.component is BackupComponent.PERSISTENCE


@pytest.mark.unit
class TestBackup:
    """``backup`` invokes pg_dump_to_file and propagates the file size."""

    async def test_backup_returns_pg_dump_size(self, tmp_path: Path) -> None:
        handler = PostgresPersistenceComponentHandler(_make_config())
        call_count = 0

        async def fake_dump(config: PostgresConfig, target: Path) -> int:
            nonlocal call_count
            call_count += 1
            del config
            import asyncio

            await asyncio.to_thread(target.write_bytes, b"x" * 4096)
            return 4096

        with patch(
            "synthorg.backup.handlers.postgres_persistence.pg_dump_to_file",
            side_effect=fake_dump,
        ):
            size = await handler.backup(tmp_path)

        assert size == 4096
        assert call_count == 1
        assert (tmp_path / "synthorg.pgdump").exists()

    async def test_backup_wraps_missing_binary(self, tmp_path: Path) -> None:
        handler = PostgresPersistenceComponentHandler(_make_config())

        async def raises_missing(config: PostgresConfig, target: Path) -> int:
            del config, target
            msg = "pg_dump not on PATH"
            raise PgToolUnavailableError(msg)

        with (
            patch(
                "synthorg.backup.handlers.postgres_persistence.pg_dump_to_file",
                side_effect=raises_missing,
            ),
            pytest.raises(ComponentBackupError, match="Failed to back up Postgres DB"),
        ):
            await handler.backup(tmp_path)

    async def test_backup_wraps_subprocess_failure(self, tmp_path: Path) -> None:
        handler = PostgresPersistenceComponentHandler(_make_config())

        async def raises_failed(config: PostgresConfig, target: Path) -> int:
            del config, target
            msg = "pg_dump exited 1"
            raise PgToolFailedError(msg)

        with (
            patch(
                "synthorg.backup.handlers.postgres_persistence.pg_dump_to_file",
                side_effect=raises_failed,
            ),
            pytest.raises(ComponentBackupError, match="Failed to back up Postgres DB"),
        ):
            await handler.backup(tmp_path)

    async def test_backup_wraps_timeout(self, tmp_path: Path) -> None:
        handler = PostgresPersistenceComponentHandler(_make_config())

        async def raises_timeout(config: PostgresConfig, target: Path) -> int:
            del config, target
            raise TimeoutError

        with (
            patch(
                "synthorg.backup.handlers.postgres_persistence.pg_dump_to_file",
                side_effect=raises_timeout,
            ),
            pytest.raises(ComponentBackupError, match="pg_dump timed out"),
        ):
            await handler.backup(tmp_path)


@pytest.mark.unit
class TestRestore:
    """``restore`` invokes pg_restore_from_file when the dump exists."""

    async def test_restore_calls_pg_restore(self, tmp_path: Path) -> None:
        handler = PostgresPersistenceComponentHandler(_make_config())
        dump = tmp_path / "synthorg.pgdump"
        dump.write_bytes(b"pgdumpdata")
        invocations = 0

        async def fake_restore(config: PostgresConfig, source: Path) -> None:
            nonlocal invocations
            invocations += 1
            del config, source

        with patch(
            "synthorg.backup.handlers.postgres_persistence.pg_restore_from_file",
            side_effect=fake_restore,
        ):
            await handler.restore(tmp_path)

        assert invocations == 1

    async def test_restore_missing_dump_raises(self, tmp_path: Path) -> None:
        handler = PostgresPersistenceComponentHandler(_make_config())
        with pytest.raises(ComponentBackupError, match="Postgres dump not found"):
            await handler.restore(tmp_path)


@pytest.mark.unit
class TestValidateSource:
    """``validate_source`` consults pg_restore --list output."""

    async def test_returns_true_when_toc_has_entries(self, tmp_path: Path) -> None:
        handler = PostgresPersistenceComponentHandler(_make_config())
        (tmp_path / "synthorg.pgdump").write_bytes(b"x")

        async def list_with_entries(source: Path) -> int:
            del source
            return 42

        with patch(
            "synthorg.backup.handlers.postgres_persistence.pg_restore_list",
            side_effect=list_with_entries,
        ):
            assert await handler.validate_source(tmp_path) is True

    async def test_returns_false_when_toc_empty(self, tmp_path: Path) -> None:
        handler = PostgresPersistenceComponentHandler(_make_config())
        (tmp_path / "synthorg.pgdump").write_bytes(b"x")

        async def list_empty(source: Path) -> int:
            del source
            return 0

        with patch(
            "synthorg.backup.handlers.postgres_persistence.pg_restore_list",
            side_effect=list_empty,
        ):
            assert await handler.validate_source(tmp_path) is False

    async def test_returns_false_when_dump_missing(self, tmp_path: Path) -> None:
        handler = PostgresPersistenceComponentHandler(_make_config())
        assert await handler.validate_source(tmp_path) is False

    async def test_pg_tool_failure_raises_component_error(
        self,
        tmp_path: Path,
    ) -> None:
        handler = PostgresPersistenceComponentHandler(_make_config())
        (tmp_path / "synthorg.pgdump").write_bytes(b"x")

        async def raises_failed(source: Path) -> int:
            del source
            msg = "pg_restore exited 1"
            raise PgToolFailedError(msg)

        with (
            patch(
                "synthorg.backup.handlers.postgres_persistence.pg_restore_list",
                side_effect=raises_failed,
            ),
            pytest.raises(ComponentBackupError),
        ):
            await handler.validate_source(tmp_path)
