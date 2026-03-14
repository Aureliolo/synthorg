"""Persistence configuration models.

Frozen Pydantic models for persistence backend selection and
backend-specific settings.
"""

from pathlib import PurePosixPath, PureWindowsPath
from typing import ClassVar, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.types import NotBlankStr  # noqa: TC001
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

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

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


class PersistenceConfig(BaseModel):
    """Top-level persistence configuration.

    Attributes:
        backend: Backend name — currently only ``"sqlite"`` is
            implemented.
        sqlite: SQLite-specific settings (used when
            ``backend="sqlite"``).
    """

    model_config = ConfigDict(frozen=True)

    _VALID_BACKENDS: ClassVar[frozenset[str]] = frozenset({"sqlite"})

    backend: NotBlankStr = Field(
        default="sqlite",
        description="Persistence backend name",
    )
    sqlite: SQLiteConfig = Field(
        default_factory=SQLiteConfig,
        description="SQLite-specific settings",
    )

    @model_validator(mode="after")
    def _validate_backend_name(self) -> Self:
        """Ensure backend is a known persistence backend."""
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
        return self
