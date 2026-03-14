"""Tests for persistence configuration models."""

import pytest
from pydantic import ValidationError

from synthorg.persistence.config import PersistenceConfig, SQLiteConfig


@pytest.mark.unit
class TestSQLiteConfig:
    def test_defaults(self) -> None:
        cfg = SQLiteConfig()
        assert cfg.path == "synthorg.db"
        assert cfg.wal_mode is True
        assert cfg.journal_size_limit == 67_108_864

    def test_custom_values(self) -> None:
        cfg = SQLiteConfig(
            path="/data/test.db",
            wal_mode=False,
            journal_size_limit=1024,
        )
        assert cfg.path == "/data/test.db"
        assert cfg.wal_mode is False
        assert cfg.journal_size_limit == 1024

    def test_memory_path(self) -> None:
        cfg = SQLiteConfig(path=":memory:")
        assert cfg.path == ":memory:"

    def test_frozen(self) -> None:
        cfg = SQLiteConfig()
        with pytest.raises(ValidationError):
            cfg.path = "other.db"  # type: ignore[misc]

    def test_blank_path_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SQLiteConfig(path="")

    def test_whitespace_path_rejected(self) -> None:
        with pytest.raises(ValidationError, match="whitespace-only"):
            SQLiteConfig(path="   ")

    def test_negative_journal_size_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SQLiteConfig(journal_size_limit=-1)

    def test_traversal_rejected(self) -> None:
        with pytest.raises(ValidationError, match="traversal"):
            SQLiteConfig(path="../escape/test.db")

    def test_embedded_traversal_rejected(self) -> None:
        with pytest.raises(ValidationError, match="traversal"):
            SQLiteConfig(path="data/../../../etc/test.db")


@pytest.mark.unit
class TestPersistenceConfig:
    def test_defaults(self) -> None:
        cfg = PersistenceConfig()
        assert cfg.backend == "sqlite"
        assert isinstance(cfg.sqlite, SQLiteConfig)

    def test_sqlite_backend_valid(self) -> None:
        cfg = PersistenceConfig(backend="sqlite")
        assert cfg.backend == "sqlite"

    def test_unknown_backend_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Unknown persistence backend"):
            PersistenceConfig(backend="postgres")

    def test_blank_backend_rejected(self) -> None:
        with pytest.raises(ValidationError):
            PersistenceConfig(backend="")

    def test_frozen(self) -> None:
        cfg = PersistenceConfig()
        with pytest.raises(ValidationError):
            cfg.backend = "other"  # type: ignore[misc]

    def test_custom_sqlite_config(self) -> None:
        cfg = PersistenceConfig(
            sqlite=SQLiteConfig(path="data/test.db", wal_mode=False),
        )
        assert cfg.sqlite.path == "data/test.db"
        assert cfg.sqlite.wal_mode is False
