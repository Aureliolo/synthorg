"""Observability configuration models.

Frozen Pydantic models for log sinks, rotation, and top-level logging
configuration.  All models are immutable and validated on construction.

.. note::

    ``DEFAULT_SINKS`` provides the standard seven-sink layout described
    in the design spec (console + six file sinks).
"""

from collections import Counter
from pathlib import PurePath, PurePosixPath, PureWindowsPath
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai_company.core.types import NotBlankStr  # noqa: TC001
from ai_company.observability.enums import LogLevel, RotationStrategy, SinkType


class RotationConfig(BaseModel):
    """Log file rotation configuration.

    Attributes:
        strategy: Rotation mechanism to use.
        max_bytes: Maximum file size in bytes before rotation.
            Only used when ``strategy`` is
            :attr:`RotationStrategy.BUILTIN`.
        backup_count: Number of rotated backup files to keep.
    """

    model_config = ConfigDict(frozen=True)

    strategy: RotationStrategy = Field(
        default=RotationStrategy.BUILTIN,
        description="Rotation mechanism",
    )
    max_bytes: int = Field(
        default=10 * 1024 * 1024,
        gt=0,
        description="Maximum file size in bytes before rotation",
    )
    backup_count: int = Field(
        default=5,
        ge=0,
        description="Number of rotated backup files to keep",
    )


class SinkConfig(BaseModel):
    """Configuration for a single log output destination.

    Attributes:
        sink_type: Where to send log output (console or file).
        level: Minimum log level for this sink.
        file_path: Relative path for FILE sinks (within ``log_dir``).
            Must be ``None`` for CONSOLE sinks, required for FILE sinks.
        rotation: Rotation settings for FILE sinks.
        json_format: Whether to format output as JSON.
    """

    model_config = ConfigDict(frozen=True)

    sink_type: SinkType = Field(
        description="Log output destination type",
    )
    level: LogLevel = Field(
        default=LogLevel.INFO,
        description="Minimum log level for this sink",
    )
    file_path: str | None = Field(
        default=None,
        description="Relative path for FILE sinks (within log_dir)",
    )
    rotation: RotationConfig | None = Field(
        default=None,
        description="Rotation settings for FILE sinks",
    )
    json_format: bool = Field(
        default=True,
        description="Whether to format output as JSON",
    )

    @model_validator(mode="after")
    def _validate_file_sink_requires_path(self) -> Self:
        """Ensure FILE sinks have a non-blank, safe ``file_path``."""
        if self.sink_type == SinkType.FILE:
            if self.file_path is None:
                msg = "file_path is required for FILE sinks"
                raise ValueError(msg)
            if not self.file_path.strip():
                msg = "file_path must not be empty or whitespace-only"
                raise ValueError(msg)
            path = PurePath(self.file_path)
            if (
                path.is_absolute()
                or PurePosixPath(self.file_path).is_absolute()
                or PureWindowsPath(self.file_path).is_absolute()
            ):
                msg = f"file_path must be relative: {self.file_path}"
                raise ValueError(msg)
            if ".." in path.parts:
                msg = f"file_path must not contain '..' components: {self.file_path}"
                raise ValueError(msg)
        else:
            if self.file_path is not None:
                msg = "file_path must be None for CONSOLE sinks"
                raise ValueError(msg)
            if self.rotation is not None:
                msg = "rotation must be None for CONSOLE sinks"
                raise ValueError(msg)
        return self


class LogConfig(BaseModel):
    """Top-level logging configuration.

    Attributes:
        root_level: Root logger level (handlers filter individually).
        logger_levels: Per-logger level overrides as ``(name, level)`` pairs.
        sinks: Tuple of sink configurations.
        enable_correlation: Whether to enable correlation ID tracking.
        log_dir: Directory for log files.
    """

    model_config = ConfigDict(frozen=True)

    root_level: LogLevel = Field(
        default=LogLevel.DEBUG,
        description="Root logger level",
    )
    logger_levels: tuple[tuple[NotBlankStr, LogLevel], ...] = Field(
        default=(),
        description="Per-logger level overrides as (name, level) pairs",
    )
    sinks: tuple[SinkConfig, ...] = Field(
        description="Log output destinations",
    )
    enable_correlation: bool = Field(
        default=True,
        description="Whether to enable correlation ID tracking",
    )
    log_dir: NotBlankStr = Field(
        default="logs",
        description="Directory for log files",
    )

    @model_validator(mode="after")
    def _validate_at_least_one_sink(self) -> Self:
        """Ensure at least one sink is configured."""
        if not self.sinks:
            msg = "At least one sink must be configured"
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_no_duplicate_logger_names(self) -> Self:
        """Ensure no duplicate logger names in ``logger_levels``."""
        names = [name for name, _ in self.logger_levels]
        counts = Counter(names)
        dupes = sorted(n for n, c in counts.items() if c > 1)
        if dupes:
            msg = f"Duplicate logger names in logger_levels: {dupes}"
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_no_duplicate_file_paths(self) -> Self:
        """Ensure no duplicate file paths across FILE sinks."""
        paths = [
            s.file_path
            for s in self.sinks
            if s.sink_type == SinkType.FILE and s.file_path is not None
        ]
        counts = Counter(paths)
        dupes = sorted(p for p, c in counts.items() if c > 1)
        if dupes:
            msg = f"Duplicate file paths across sinks: {dupes}"
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_log_dir_safe(self) -> Self:
        """Ensure ``log_dir`` has no path traversal."""
        path = PurePath(self.log_dir)
        if ".." in path.parts:
            msg = f"log_dir must not contain '..' components: {self.log_dir}"
            raise ValueError(msg)
        return self


DEFAULT_SINKS: tuple[SinkConfig, ...] = (
    SinkConfig(
        sink_type=SinkType.CONSOLE,
        level=LogLevel.INFO,
        json_format=False,
    ),
    SinkConfig(
        sink_type=SinkType.FILE,
        level=LogLevel.INFO,
        file_path="ai_company.log",
        rotation=RotationConfig(),
        json_format=True,
    ),
    SinkConfig(
        sink_type=SinkType.FILE,
        level=LogLevel.INFO,
        file_path="audit.log",
        rotation=RotationConfig(),
        json_format=True,
    ),
    SinkConfig(
        sink_type=SinkType.FILE,
        level=LogLevel.ERROR,
        file_path="errors.log",
        rotation=RotationConfig(),
        json_format=True,
    ),
    SinkConfig(
        sink_type=SinkType.FILE,
        level=LogLevel.DEBUG,
        file_path="agent_activity.log",
        rotation=RotationConfig(),
        json_format=True,
    ),
    SinkConfig(
        sink_type=SinkType.FILE,
        level=LogLevel.INFO,
        file_path="cost_usage.log",
        rotation=RotationConfig(),
        json_format=True,
    ),
    SinkConfig(
        sink_type=SinkType.FILE,
        level=LogLevel.DEBUG,
        file_path="debug.log",
        rotation=RotationConfig(),
        json_format=True,
    ),
)
