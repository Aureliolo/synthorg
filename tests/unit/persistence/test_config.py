"""Tests for persistence configuration models."""

import pytest
from pydantic import SecretStr, ValidationError

from synthorg.persistence.config import (
    PersistenceConfig,
    PostgresConfig,
    SQLiteConfig,
)


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


def _minimal_postgres_config(**overrides: object) -> PostgresConfig:
    """Build a PostgresConfig with required fields filled in.

    Tests override individual fields via keyword arguments.
    """
    defaults: dict[str, object] = {
        "database": "synthorg",
        "username": "postgres",
        "password": SecretStr("s3cret"),
    }
    defaults.update(overrides)
    return PostgresConfig(**defaults)  # type: ignore[arg-type]


@pytest.mark.unit
class TestPostgresConfig:
    def test_defaults(self) -> None:
        cfg = _minimal_postgres_config()
        assert cfg.host == "localhost"
        assert cfg.port == 5432
        assert cfg.database == "synthorg"
        assert cfg.username == "postgres"
        assert cfg.password.get_secret_value() == "s3cret"
        assert cfg.ssl_mode == "require"
        assert cfg.pool_min_size == 1
        assert cfg.pool_max_size == 10
        assert cfg.pool_timeout_seconds == 30.0
        assert cfg.application_name == "synthorg"
        assert cfg.statement_timeout_ms == 30_000
        assert cfg.connect_timeout_seconds == 10.0

    @pytest.mark.parametrize("field", ["host", "username", "database"])
    @pytest.mark.parametrize("bad", ["db\nhost", "na\tme", "x\x00y", "a\x7fb"])
    def test_control_chars_rejected(self, field: str, bad: str) -> None:
        with pytest.raises(ValidationError, match="control characters"):
            _minimal_postgres_config(**{field: bad})

    @pytest.mark.parametrize("field", ["host", "username", "database"])
    @pytest.mark.parametrize("ok", ["my db", "u@svc", "a/b"])
    def test_printable_specials_accepted(self, field: str, ok: str) -> None:
        # Spaces / ``@`` / ``/`` are URL-encoded by ``to_postgres_url`` and
        # glued into a single ``--flag=value`` argv element, so they round-trip
        # safely and must not be rejected at construction.
        cfg = _minimal_postgres_config(**{field: ok})
        assert getattr(cfg, field) == ok

    def test_custom_values(self) -> None:
        cfg = _minimal_postgres_config(
            host="db.internal",
            port=6432,
            ssl_mode="verify-full",
            pool_min_size=2,
            pool_max_size=20,
            pool_timeout_seconds=5.0,
            application_name="synthorg-api",
            statement_timeout_ms=60_000,
            connect_timeout_seconds=3.0,
        )
        assert cfg.host == "db.internal"
        assert cfg.port == 6432
        assert cfg.ssl_mode == "verify-full"
        assert cfg.pool_min_size == 2
        assert cfg.pool_max_size == 20
        assert cfg.pool_timeout_seconds == 5.0
        assert cfg.application_name == "synthorg-api"
        assert cfg.statement_timeout_ms == 60_000
        assert cfg.connect_timeout_seconds == 3.0

    def test_frozen(self) -> None:
        cfg = _minimal_postgres_config()
        with pytest.raises(ValidationError):
            cfg.host = "other"  # type: ignore[misc]

    def test_password_is_redacted_in_repr(self) -> None:
        cfg = _minimal_postgres_config(password=SecretStr("supersecret"))
        rendered = repr(cfg)
        assert "supersecret" not in rendered
        assert "SecretStr" in rendered or "**" in rendered

    def test_password_roundtrip_via_get_secret_value(self) -> None:
        cfg = _minimal_postgres_config(password=SecretStr("has !@#$%^&*()"))
        assert cfg.password.get_secret_value() == "has !@#$%^&*()"

    def test_missing_database_rejected(self) -> None:
        with pytest.raises(ValidationError):
            PostgresConfig(  # type: ignore[call-arg]
                username="postgres",
                password=SecretStr("x"),
            )

    def test_missing_username_rejected(self) -> None:
        with pytest.raises(ValidationError):
            PostgresConfig(  # type: ignore[call-arg]
                database="synthorg",
                password=SecretStr("x"),
            )

    def test_missing_password_rejected(self) -> None:
        with pytest.raises(ValidationError):
            PostgresConfig(  # type: ignore[call-arg]
                database="synthorg",
                username="postgres",
            )

    def test_blank_host_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _minimal_postgres_config(host="")

    def test_blank_database_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _minimal_postgres_config(database="")

    def test_blank_username_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _minimal_postgres_config(username="")

    @pytest.mark.parametrize("port", [0, -1, 65536, 70_000])
    def test_invalid_port_rejected(self, port: int) -> None:
        with pytest.raises(ValidationError):
            _minimal_postgres_config(port=port)

    def test_invalid_ssl_mode_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _minimal_postgres_config(ssl_mode="bogus")

    @pytest.mark.parametrize(
        "ssl_mode",
        ["disable", "allow", "prefer", "require", "verify-ca", "verify-full"],
    )
    def test_all_ssl_modes_accepted(self, ssl_mode: str) -> None:
        cfg = _minimal_postgres_config(ssl_mode=ssl_mode)
        assert cfg.ssl_mode == ssl_mode

    def test_pool_min_zero_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _minimal_postgres_config(pool_min_size=0)

    def test_pool_max_zero_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _minimal_postgres_config(pool_max_size=0)

    def test_pool_max_less_than_min_rejected(self) -> None:
        with pytest.raises(ValidationError, match="pool_max_size"):
            _minimal_postgres_config(pool_min_size=5, pool_max_size=2)

    def test_pool_max_equal_to_min_accepted(self) -> None:
        cfg = _minimal_postgres_config(pool_min_size=3, pool_max_size=3)
        assert cfg.pool_max_size == 3

    def test_zero_pool_timeout_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _minimal_postgres_config(pool_timeout_seconds=0.0)

    def test_negative_pool_timeout_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _minimal_postgres_config(pool_timeout_seconds=-1.0)

    def test_zero_connect_timeout_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _minimal_postgres_config(connect_timeout_seconds=0.0)

    def test_negative_statement_timeout_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _minimal_postgres_config(statement_timeout_ms=-1)

    def test_zero_statement_timeout_accepted(self) -> None:
        cfg = _minimal_postgres_config(statement_timeout_ms=0)
        assert cfg.statement_timeout_ms == 0


@pytest.mark.unit
class TestPersistenceConfig:
    def test_defaults(self) -> None:
        cfg = PersistenceConfig()
        assert cfg.backend == "sqlite"
        assert isinstance(cfg.sqlite, SQLiteConfig)
        assert cfg.postgres is None

    def test_sqlite_backend_valid(self) -> None:
        cfg = PersistenceConfig(backend="sqlite")
        assert cfg.backend == "sqlite"

    def test_postgres_backend_valid(self) -> None:
        cfg = PersistenceConfig(
            backend="postgres",
            postgres=_minimal_postgres_config(),
        )
        assert cfg.backend == "postgres"
        assert cfg.postgres is not None
        assert cfg.postgres.database == "synthorg"

    def test_postgres_backend_without_config_rejected(self) -> None:
        with pytest.raises(
            ValidationError,
            match="backend='postgres' requires a PostgresConfig",
        ):
            PersistenceConfig(backend="postgres")

    def test_unknown_backend_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Unknown persistence backend"):
            PersistenceConfig(backend="cassandra")

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

    def test_sqlite_backend_ignores_postgres_field(self) -> None:
        """Providing a postgres config with backend=sqlite is allowed but unused."""
        cfg = PersistenceConfig(
            backend="sqlite",
            postgres=_minimal_postgres_config(),
        )
        assert cfg.backend == "sqlite"
        assert cfg.postgres is not None
