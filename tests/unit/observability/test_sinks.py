"""Tests for log handler factory."""

import logging
import logging.handlers
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
import structlog
from structlog.stdlib import ProcessorFormatter

from synthorg.observability.config import RotationConfig, SinkConfig
from synthorg.observability.enums import LogLevel, RotationStrategy, SinkType
from synthorg.observability.sinks import _build_file_handler, build_handler

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

pytestmark = pytest.mark.timeout(30)


def _foreign_pre_chain() -> list[structlog.types.Processor]:
    return [
        structlog.stdlib.add_log_level,
        structlog.processors.format_exc_info,
    ]


@pytest.fixture
def handler_cleanup() -> Iterator[list[logging.Handler]]:
    """Collect handlers and close them after the test."""
    handlers: list[logging.Handler] = []
    yield handlers
    for h in handlers:
        h.close()


@pytest.mark.unit
class TestBuildHandlerConsole:
    """Tests for console handler creation."""

    def test_returns_stream_handler(
        self, tmp_path: Path, handler_cleanup: list[logging.Handler]
    ) -> None:
        sink = SinkConfig(sink_type=SinkType.CONSOLE, json_format=False)
        handler = build_handler(sink, tmp_path, _foreign_pre_chain())
        handler_cleanup.append(handler)
        assert isinstance(handler, logging.StreamHandler)

    def test_has_processor_formatter(
        self, tmp_path: Path, handler_cleanup: list[logging.Handler]
    ) -> None:
        sink = SinkConfig(sink_type=SinkType.CONSOLE, json_format=False)
        handler = build_handler(sink, tmp_path, _foreign_pre_chain())
        handler_cleanup.append(handler)
        assert isinstance(handler.formatter, ProcessorFormatter)

    def test_console_json_format(
        self, tmp_path: Path, handler_cleanup: list[logging.Handler]
    ) -> None:
        sink = SinkConfig(sink_type=SinkType.CONSOLE, json_format=True)
        handler = build_handler(sink, tmp_path, _foreign_pre_chain())
        handler_cleanup.append(handler)
        assert isinstance(handler, logging.StreamHandler)

    def test_handler_level_matches_config(
        self, tmp_path: Path, handler_cleanup: list[logging.Handler]
    ) -> None:
        sink = SinkConfig(
            sink_type=SinkType.CONSOLE,
            level=LogLevel.ERROR,
            json_format=False,
        )
        handler = build_handler(sink, tmp_path, _foreign_pre_chain())
        handler_cleanup.append(handler)
        assert handler.level == logging.ERROR


@pytest.mark.unit
class TestBuildHandlerFileBuiltin:
    """Tests for file handler with BUILTIN rotation."""

    def test_returns_rotating_handler(
        self, tmp_path: Path, handler_cleanup: list[logging.Handler]
    ) -> None:
        sink = SinkConfig(
            sink_type=SinkType.FILE,
            file_path="app.log",
            rotation=RotationConfig(strategy=RotationStrategy.BUILTIN),
        )
        handler = build_handler(sink, tmp_path, _foreign_pre_chain())
        handler_cleanup.append(handler)
        assert isinstance(handler, logging.handlers.RotatingFileHandler)

    def test_creates_parent_directories(
        self, tmp_path: Path, handler_cleanup: list[logging.Handler]
    ) -> None:
        sink = SinkConfig(
            sink_type=SinkType.FILE,
            file_path="sub/dir/app.log",
            rotation=RotationConfig(),
        )
        handler = build_handler(sink, tmp_path, _foreign_pre_chain())
        handler_cleanup.append(handler)
        assert (tmp_path / "sub" / "dir").is_dir()

    def test_rotation_params_applied(
        self, tmp_path: Path, handler_cleanup: list[logging.Handler]
    ) -> None:
        sink = SinkConfig(
            sink_type=SinkType.FILE,
            file_path="app.log",
            rotation=RotationConfig(max_bytes=1_000_000, backup_count=3),
        )
        handler = build_handler(sink, tmp_path, _foreign_pre_chain())
        handler_cleanup.append(handler)
        assert isinstance(handler, logging.handlers.RotatingFileHandler)
        assert handler.maxBytes == 1_000_000
        assert handler.backupCount == 3

    def test_handler_level_matches_config(
        self, tmp_path: Path, handler_cleanup: list[logging.Handler]
    ) -> None:
        sink = SinkConfig(
            sink_type=SinkType.FILE,
            file_path="app.log",
            level=LogLevel.WARNING,
            rotation=RotationConfig(),
        )
        handler = build_handler(sink, tmp_path, _foreign_pre_chain())
        handler_cleanup.append(handler)
        assert handler.level == logging.WARNING

    def test_has_processor_formatter(
        self, tmp_path: Path, handler_cleanup: list[logging.Handler]
    ) -> None:
        sink = SinkConfig(
            sink_type=SinkType.FILE,
            file_path="app.log",
            rotation=RotationConfig(),
        )
        handler = build_handler(sink, tmp_path, _foreign_pre_chain())
        handler_cleanup.append(handler)
        assert isinstance(handler.formatter, ProcessorFormatter)

    def test_default_rotation_when_none(
        self, tmp_path: Path, handler_cleanup: list[logging.Handler]
    ) -> None:
        sink = SinkConfig(
            sink_type=SinkType.FILE,
            file_path="app.log",
        )
        handler = build_handler(sink, tmp_path, _foreign_pre_chain())
        handler_cleanup.append(handler)
        assert isinstance(handler, logging.handlers.RotatingFileHandler)


@pytest.mark.unit
class TestBuildHandlerFileExternal:
    """Tests for file handler with EXTERNAL rotation."""

    def test_returns_watched_handler(
        self, tmp_path: Path, handler_cleanup: list[logging.Handler]
    ) -> None:
        sink = SinkConfig(
            sink_type=SinkType.FILE,
            file_path="app.log",
            rotation=RotationConfig(strategy=RotationStrategy.EXTERNAL),
        )
        handler = build_handler(sink, tmp_path, _foreign_pre_chain())
        handler_cleanup.append(handler)
        assert isinstance(handler, logging.handlers.WatchedFileHandler)

    def test_creates_parent_directories(
        self, tmp_path: Path, handler_cleanup: list[logging.Handler]
    ) -> None:
        sink = SinkConfig(
            sink_type=SinkType.FILE,
            file_path="ext/app.log",
            rotation=RotationConfig(strategy=RotationStrategy.EXTERNAL),
        )
        handler = build_handler(sink, tmp_path, _foreign_pre_chain())
        handler_cleanup.append(handler)
        assert (tmp_path / "ext").is_dir()


@pytest.mark.unit
class TestBuildFileHandlerErrors:
    """Tests for _build_file_handler error paths."""

    def test_mkdir_oserror_raises_runtime_error(self, tmp_path: Path) -> None:
        """mkdir OSError is wrapped in RuntimeError."""
        sink = SinkConfig(
            sink_type=SinkType.FILE,
            file_path="sub/app.log",
            rotation=RotationConfig(),
        )
        with (
            patch("pathlib.Path.mkdir", side_effect=OSError("permission denied")),
            pytest.raises(RuntimeError, match="Failed to create log directory"),
        ):
            _build_file_handler(sink, tmp_path)

    def test_file_open_oserror_raises_runtime_error(self, tmp_path: Path) -> None:
        """File open OSError is wrapped in RuntimeError."""
        sink = SinkConfig(
            sink_type=SinkType.FILE,
            file_path="app.log",
            rotation=RotationConfig(strategy=RotationStrategy.BUILTIN),
        )
        with (
            patch(
                "logging.handlers.RotatingFileHandler.__init__",
                side_effect=OSError("disk full"),
            ),
            pytest.raises(RuntimeError, match="Failed to open log file"),
        ):
            _build_file_handler(sink, tmp_path)

    def test_external_rotation_uses_watched_handler(
        self, tmp_path: Path, handler_cleanup: list[logging.Handler]
    ) -> None:
        """EXTERNAL rotation creates a WatchedFileHandler."""
        sink = SinkConfig(
            sink_type=SinkType.FILE,
            file_path="watched.log",
            rotation=RotationConfig(strategy=RotationStrategy.EXTERNAL),
        )
        handler = _build_file_handler(sink, tmp_path)
        handler_cleanup.append(handler)
        assert isinstance(handler, logging.handlers.WatchedFileHandler)

    def test_missing_file_path_raises_value_error(self, tmp_path: Path) -> None:
        """file_path=None raises ValueError."""
        sink = SinkConfig(sink_type=SinkType.FILE, file_path="placeholder.log")
        # Bypass SinkConfig validation by forcing file_path to None
        object.__setattr__(sink, "file_path", None)
        with pytest.raises(ValueError, match="FILE sink is missing 'file_path'"):
            _build_file_handler(sink, tmp_path)
