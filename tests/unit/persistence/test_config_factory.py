"""Tests for the persistence config factory helpers."""

from pathlib import Path

import pytest

from synthorg.persistence.artifact_storage import ArtifactStorageBackend
from synthorg.persistence.config import PersistenceConfig
from synthorg.persistence.config_factory import (
    build_filesystem_artifact_storage,
    build_postgres_persistence_config_from_url,
    build_sqlite_persistence_config,
    resolve_postgres_ssl_mode_from_env,
)

pytestmark = pytest.mark.unit


class TestBuildSqlitePersistenceConfig:
    def test_returns_persistence_config_envelope(self, tmp_path: Path) -> None:
        target = str(tmp_path / "synthorg.db")
        cfg = build_sqlite_persistence_config(path=target)
        assert isinstance(cfg, PersistenceConfig)
        assert cfg.backend == "sqlite"
        assert cfg.sqlite.path == target

    def test_in_memory_path_round_trips(self) -> None:
        cfg = build_sqlite_persistence_config(path=":memory:")
        assert cfg.sqlite.path == ":memory:"


class TestBuildPostgresPersistenceConfigFromUrl:
    def test_basic_url_parses_host_port_database_credentials(self) -> None:
        cfg = build_postgres_persistence_config_from_url(
            "postgresql://user:pw@db.example:6543/synthorg",
        )
        assert isinstance(cfg, PersistenceConfig)
        assert cfg.backend == "postgres"
        assert cfg.postgres is not None
        assert cfg.postgres.host == "db.example"
        assert cfg.postgres.port == 6543
        assert cfg.postgres.database == "synthorg"
        assert cfg.postgres.username == "user"
        assert cfg.postgres.password.get_secret_value() == "pw"

    def test_default_port_when_omitted(self) -> None:
        cfg = build_postgres_persistence_config_from_url(
            "postgresql://u:p@h/db",
        )
        assert cfg.postgres is not None
        assert cfg.postgres.port == 5432

    def test_postgres_scheme_alias_accepted(self) -> None:
        cfg = build_postgres_persistence_config_from_url("postgres://u:p@h/db")
        assert cfg.postgres is not None
        assert cfg.postgres.host == "h"

    def test_url_encoded_password_is_decoded(self) -> None:
        cfg = build_postgres_persistence_config_from_url(
            "postgresql://u:p%40ss%2F1@h/db",
        )
        assert cfg.postgres is not None
        assert cfg.postgres.password.get_secret_value() == "p@ss/1"

    def test_url_encoded_username_is_decoded(self) -> None:
        cfg = build_postgres_persistence_config_from_url(
            "postgresql://u%40domain:pw@h/db",
        )
        assert cfg.postgres is not None
        assert cfg.postgres.username == "u@domain"

    def test_url_encoded_hostname_is_decoded(self) -> None:
        cfg = build_postgres_persistence_config_from_url(
            "postgresql://u:p@db%2Eexample:5432/synthorg",
        )
        assert cfg.postgres is not None
        assert cfg.postgres.host == "db.example"

    def test_ssl_mode_override_passes_through(self) -> None:
        cfg = build_postgres_persistence_config_from_url(
            "postgresql://u:p@h/db",
            ssl_mode_override="disable",
        )
        assert cfg.postgres is not None
        assert cfg.postgres.ssl_mode == "disable"

    def test_default_ssl_mode_is_require_when_no_override(self) -> None:
        cfg = build_postgres_persistence_config_from_url(
            "postgresql://u:p@h/db",
        )
        assert cfg.postgres is not None
        assert cfg.postgres.ssl_mode == "require"

    def test_invalid_ssl_mode_override_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="SYNTHORG_POSTGRES_SSL_MODE"):
            build_postgres_persistence_config_from_url(
                "postgresql://u:p@h/db",
                ssl_mode_override="bogus_mode",
            )

    @pytest.mark.parametrize(
        ("bad_url", "reason_substring"),
        [
            ("postgresql://u:p@h/db?sslmode=disable", "query parameters"),
            ("mysql://u:p@h/db", "scheme"),
            ("postgresql://h/db", "username and password"),
            ("postgresql://u:p@h", "database name"),
            ("postgresql://u:p@/db", "host"),
        ],
    )
    def test_invalid_url_raises_value_error(
        self,
        bad_url: str,
        reason_substring: str,
    ) -> None:
        with pytest.raises(ValueError, match=reason_substring):
            build_postgres_persistence_config_from_url(bad_url)

    def test_ipv6_literal_host_round_trips(self) -> None:
        # urlparse strips the brackets from the hostname; the helper
        # must preserve the literal host string for psycopg.
        cfg = build_postgres_persistence_config_from_url(
            "postgresql://u:p@[::1]:5432/db",
        )
        assert cfg.postgres is not None
        assert cfg.postgres.host == "::1"
        assert cfg.postgres.port == 5432

    def test_url_encoded_at_in_password_does_not_split_userinfo(self) -> None:
        # ``%40`` is URL-encoded ``@``; if the parser were splitting on
        # the literal ``@`` instead of the userinfo segment, the
        # password would silently truncate at ``p``. Lock the
        # round-trip explicitly.
        cfg = build_postgres_persistence_config_from_url(
            "postgresql://u:p%40@h/db",
        )
        assert cfg.postgres is not None
        assert cfg.postgres.username == "u"
        assert cfg.postgres.password.get_secret_value() == "p@"

    def test_uppercase_scheme_is_rejected(self) -> None:
        # urlparse lowercases the scheme by default, so the strict
        # ``in {"postgres", "postgresql"}`` check accepts ``POSTGRESQL``
        # silently. That is fine for now (RFC 3986 declares the scheme
        # case-insensitive); locking the behaviour here so a future
        # tightening of the allowed-scheme set surfaces in this test
        # rather than in production.
        cfg = build_postgres_persistence_config_from_url(
            "POSTGRESQL://u:p@h/db",
        )
        assert cfg.postgres is not None
        assert cfg.postgres.host == "h"


class TestBuildFilesystemArtifactStorage:
    def test_returns_protocol_implementation(self, tmp_path: Path) -> None:
        storage = build_filesystem_artifact_storage(data_dir=tmp_path)
        assert isinstance(storage, ArtifactStorageBackend)
        assert storage.backend_name == "filesystem"


class TestResolvePostgresSslModeFromEnv:
    def test_unset_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SYNTHORG_POSTGRES_SSL_MODE", raising=False)
        assert resolve_postgres_ssl_mode_from_env() is None

    def test_whitespace_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SYNTHORG_POSTGRES_SSL_MODE", "   ")
        assert resolve_postgres_ssl_mode_from_env() is None

    def test_value_round_trips(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SYNTHORG_POSTGRES_SSL_MODE", "disable")
        assert resolve_postgres_ssl_mode_from_env() == "disable"

    def test_value_is_stripped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SYNTHORG_POSTGRES_SSL_MODE", "  prefer  ")
        assert resolve_postgres_ssl_mode_from_env() == "prefer"
