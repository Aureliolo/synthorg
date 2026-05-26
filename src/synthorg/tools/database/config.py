"""Configuration models for database tools."""

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.observability import get_logger
from synthorg.observability.events.database import DB_CONFIG_DEFAULT_MISSING

logger = get_logger(__name__)


class DatabaseConnectionConfig(BaseModel):
    """Connection configuration for a database backend.

    Attributes:
        database_path: Path to the SQLite database file.
        query_timeout: Maximum query execution time in seconds.
        read_only: Whether the connection is read-only by default.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    database_path: NotBlankStr = Field(
        description="Path to the SQLite database file",
    )
    query_timeout: float = Field(
        default=30.0,
        gt=0,
        le=300.0,
        description="Maximum query execution time (seconds)",
    )
    read_only: bool = Field(
        default=True,
        description="Whether the connection is read-only",
    )
    max_rows: int = Field(
        default=1000,
        gt=0,
        le=100_000,
        description="Maximum rows to return from a query",
    )


class DatabaseConfig(BaseModel):
    """Top-level database tool configuration.

    Attributes:
        connections: Named database connections.
        default_connection: Name of the default connection.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    connections: dict[NotBlankStr, DatabaseConnectionConfig] = Field(
        default_factory=dict,
        description="Named database connections",
    )
    default_connection: NotBlankStr = Field(
        default="default",
        description="Name of the default connection",
    )

    @model_validator(mode="after")
    def _validate_default_connection(self) -> Self:
        """Warn when default_connection is missing from connections.

        Does not raise -- the tool factory falls back to the first
        available connection when the default is not found.

        Returns:
            Result of type ``Self``.
        """
        if self.connections and self.default_connection not in self.connections:
            logger.warning(
                DB_CONFIG_DEFAULT_MISSING,
                default_connection=self.default_connection,
                available=sorted(self.connections),
            )
        return self
