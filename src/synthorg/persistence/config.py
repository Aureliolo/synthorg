"""Persistence configuration models.

Frozen Pydantic models for persistence backend selection and
backend-specific settings.
"""

import re
from pathlib import PurePosixPath, PureWindowsPath
from typing import ClassVar, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)

from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.config import CONFIG_VALIDATION_FAILED

logger = get_logger(__name__)


class SQLiteConfig(BaseModel):
    """SQLite-specific persistence configuration.

    Attributes:
        path: Database file path.  Use ``":memory:"`` for in-memory
            databases (useful for testing).
        wal_mode: Whether to enable WAL journal mode for concurrent
            read performance.
        journal_size_limit: Maximum WAL journal size in bytes
            (default 64 MB).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    path: NotBlankStr = Field(
        default="synthorg.db",
        description="Database file path",
    )
    wal_mode: bool = Field(
        default=True,
        description="Enable WAL journal mode",
    )
    journal_size_limit: int = Field(
        default=67_108_864,
        ge=0,
        description="Maximum WAL journal size in bytes",
    )

    @model_validator(mode="after")
    def _reject_traversal(self) -> Self:
        """Reject parent-directory traversal to prevent path escapes.

        The special ``:memory:`` identifier is passed through unchanged.
        Paths containing ``..`` components are rejected to prevent
        path-traversal attacks in multi-tenant configs.  Absolute paths
        are allowed for operational flexibility.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails validation.
        """
        if self.path == ":memory:":
            return self
        parts = PureWindowsPath(self.path).parts + PurePosixPath(self.path).parts
        if ".." in parts:
            msg = "Database path must not contain parent-directory traversal (..)"
            logger.warning(
                CONFIG_VALIDATION_FAILED,
                field="path",
                value=self.path,
                reason=msg,
            )
            raise ValueError(msg)
        return self


PostgresSslMode = Literal[
    "disable",
    "allow",
    "prefer",
    "require",
    "verify-ca",
    "verify-full",
]


class PostgresConfig(BaseModel):
    """Postgres-specific persistence configuration.

    Credentials are carried as ``SecretStr`` so they are redacted from
    logs, ``repr`` output, and Pydantic serialization unless
    explicitly unwrapped via ``get_secret_value()``.

    Attributes:
        host: Database host.
        port: Database port (default 5432).
        database: Database name.
        username: Database username.
        password: Database password (redacted in logs).
        ssl_mode: libpq SSL mode.  Default ``"require"`` refuses
            plaintext connections; production deployments with
            managed certificates should use ``"verify-full"``.
        pool_min_size: Minimum pooled connections (warmed on connect).
        pool_max_size: Maximum pooled connections; must be
            ``>= pool_min_size``.
        pool_timeout_seconds: Seconds to wait for a pool checkout
            before raising.
        application_name: libpq ``application_name`` session parameter
            (appears in ``pg_stat_activity``).
        statement_timeout_ms: Postgres ``statement_timeout`` session
            parameter; 0 disables.  Default 30 seconds matches the
            pytest global timeout.
        connect_timeout_seconds: Seconds to wait for an initial
            connection attempt before raising.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    # Defaults target a local-loopback Postgres for development. The
    # Go CLI overrides both host and port by injecting a complete
    # ``SYNTHORG_DATABASE_URL`` (e.g.
    # ``postgresql://synthorg:<pw>@postgres:5432/synthorg``) into the
    # backend container's environment, where ``postgres`` resolves via
    # docker-compose internal DNS.
    host: NotBlankStr = Field(
        default="localhost",
        description="Database host",
    )
    port: int = Field(
        default=5432,
        ge=1,
        le=65535,
        description="Database port",
    )
    database: NotBlankStr = Field(description="Database name")
    username: NotBlankStr = Field(description="Database username")
    password: SecretStr = Field(description="Database password")
    ssl_mode: PostgresSslMode = Field(
        default="require",
        description="libpq SSL mode",
    )
    pool_min_size: int = Field(
        default=1,
        ge=1,
        description="Minimum pooled connections",
    )
    pool_max_size: int = Field(
        default=10,
        ge=1,
        description="Maximum pooled connections",
    )
    pool_timeout_seconds: float = Field(
        default=30.0,
        gt=0.0,
        description="Pool checkout timeout in seconds",
    )
    application_name: NotBlankStr = Field(
        default="synthorg",
        description="libpq application_name session parameter",
    )
    statement_timeout_ms: int = Field(
        default=30_000,
        ge=0,
        description="Postgres statement_timeout session param in ms (0 disables)",
    )
    connect_timeout_seconds: float = Field(
        default=10.0,
        gt=0.0,
        description="Initial connection timeout in seconds",
    )
    enable_timescaledb: bool = Field(
        default=False,
        description=(
            "Enable TimescaleDB hypertable conversion for "
            "append-only time-series tables (cost_records, "
            "audit_entries). Uses Apache-2.0 licensed hypertable "
            "features only; retention policies and compression "
            "are Timescale-License features and are not used. "
            "Requires the timescaledb extension on the Postgres "
            "server. Not supported on managed Postgres providers "
            "(AWS RDS, Cloud SQL, Azure Postgres)."
        ),
    )
    cost_records_chunk_interval: NotBlankStr = Field(
        default="1 day",
        description=(
            "Hypertable chunk interval for cost_records. Ignored "
            "when enable_timescaledb is False."
        ),
    )
    audit_entries_chunk_interval: NotBlankStr = Field(
        default="1 day",
        description=(
            "Hypertable chunk interval for audit_entries. Ignored "
            "when enable_timescaledb is False."
        ),
    )

    _INTERVAL_RE: ClassVar[re.Pattern[str]] = re.compile(
        r"^\d+\s+"
        r"(microseconds?|milliseconds?|seconds?|minutes?|hours?|days?|weeks?|months?|years?)$",
        re.IGNORECASE,
    )

    @field_validator("cost_records_chunk_interval", "audit_entries_chunk_interval")
    @classmethod
    def _validate_chunk_interval(cls, value: str) -> str:
        """Reject malformed Postgres interval literals at config time.

        Returns:
            Result of type ``str``.

        Raises:
            ValueError: If an argument fails validation.
        """
        if not cls._INTERVAL_RE.match(value.strip()):
            msg = (
                f"Invalid Postgres interval: {value!r}. "
                "Expected format: '<number> <unit>' "
                "(e.g. '1 day', '12 hours', '7 days')."
            )
            logger.warning(
                CONFIG_VALIDATION_FAILED,
                field="chunk_interval",
                value=value,
                reason=msg,
            )
            raise ValueError(msg)
        return value

    @field_validator("host", "username", "database")
    @classmethod
    def _reject_unsafe_connection_chars(cls, value: NotBlankStr) -> NotBlankStr:
        """Reject whitespace / control chars that could reach the pg-tool argv.

        ``host`` / ``username`` / ``database`` are interpolated into the
        ``pg_dump`` / ``pg_restore`` argv. None has a legitimate reason to
        carry whitespace or control characters; rejecting them at
        construction is a defence-in-depth guard against a newline-injected
        config (DB-precedence settings path) smuggling an extra option past
        the ``--flag=value`` framing.

        Returns:
            The validated value, unchanged.

        Raises:
            ValueError: If the value carries whitespace or a control char.
        """
        if any(char.isspace() or char < " " for char in value):
            msg = (
                "host, username, and database must not contain whitespace "
                f"or control characters: {value!r}"
            )
            raise ValueError(msg)
        return value

    @model_validator(mode="after")
    def _validate_pool_sizes(self) -> Self:
        """Ensure ``pool_max_size`` is not smaller than ``pool_min_size``.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails validation.
        """
        if self.pool_max_size < self.pool_min_size:
            msg = (
                f"pool_max_size ({self.pool_max_size}) must be >= "
                f"pool_min_size ({self.pool_min_size})"
            )
            logger.warning(
                CONFIG_VALIDATION_FAILED,
                field="pool_max_size",
                value=self.pool_max_size,
                reason=msg,
            )
            raise ValueError(msg)
        return self


class PersistenceConfig(BaseModel):
    """Top-level persistence configuration.

    Attributes:
        backend: Backend name.  One of ``"sqlite"`` or ``"postgres"``.
        sqlite: SQLite-specific settings (used when
            ``backend="sqlite"``).
        postgres: Postgres-specific settings (required when
            ``backend="postgres"``).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    _VALID_BACKENDS: ClassVar[frozenset[str]] = frozenset({"sqlite", "postgres"})

    backend: NotBlankStr = Field(
        default="sqlite",
        description="Persistence backend name",
    )
    sqlite: SQLiteConfig = Field(
        default_factory=SQLiteConfig,
        description="SQLite-specific settings",
    )
    postgres: PostgresConfig | None = Field(
        default=None,
        description="Postgres-specific settings (required when backend=postgres)",
    )

    @model_validator(mode="after")
    def _validate_backend_name(self) -> Self:
        """Ensure backend is known and backend-specific config is present.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails validation.
        """
        if self.backend not in self._VALID_BACKENDS:
            msg = (
                f"Unknown persistence backend {self.backend!r}. "
                f"Valid backends: {sorted(self._VALID_BACKENDS)}"
            )
            logger.warning(
                CONFIG_VALIDATION_FAILED,
                field="backend",
                value=self.backend,
                reason=msg,
            )
            raise ValueError(msg)
        if self.backend == "postgres" and self.postgres is None:
            msg = (
                "backend='postgres' requires a PostgresConfig to be provided "
                "via the 'postgres' field"
            )
            logger.warning(
                CONFIG_VALIDATION_FAILED,
                field="postgres",
                value=None,
                reason=msg,
            )
            raise ValueError(msg)
        return self
