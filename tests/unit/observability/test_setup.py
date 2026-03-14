"""Tests for logging system setup."""

import json
import logging
from typing import TYPE_CHECKING

import pytest
import structlog

if TYPE_CHECKING:
    from pathlib import Path

from synthorg.observability.config import LogConfig, SinkConfig
from synthorg.observability.correlation import bind_correlation_id
from synthorg.observability.enums import LogLevel, SinkType
from synthorg.observability.setup import _DEFAULT_LOGGER_LEVELS, configure_logging

pytestmark = pytest.mark.timeout(30)


def _console_only_config() -> LogConfig:
    return LogConfig(
        sinks=(
            SinkConfig(
                sink_type=SinkType.CONSOLE,
                level=LogLevel.DEBUG,
                json_format=False,
            ),
        ),
    )


def _file_config(tmp_path: Path) -> LogConfig:
    return LogConfig(
        sinks=(
            SinkConfig(
                sink_type=SinkType.FILE,
                level=LogLevel.DEBUG,
                file_path="test.log",
                json_format=True,
            ),
        ),
        log_dir=str(tmp_path),
    )


@pytest.mark.unit
class TestConfigureLogging:
    """Tests for configure_logging function."""

    def test_default_config_creates_handlers(self) -> None:
        configure_logging()
        root = logging.getLogger()
        assert len(root.handlers) == 7

    def test_custom_config_creates_handlers(self) -> None:
        configure_logging(_console_only_config())
        root = logging.getLogger()
        assert len(root.handlers) == 1

    def test_idempotent_no_duplicate_handlers(self) -> None:
        configure_logging(_console_only_config())
        configure_logging(_console_only_config())
        root = logging.getLogger()
        assert len(root.handlers) == 1

    def test_root_logger_set_to_debug(self) -> None:
        configure_logging(_console_only_config())
        root = logging.getLogger()
        assert root.level == logging.DEBUG

    def test_custom_root_level_applied(self) -> None:
        config = LogConfig(
            sinks=(_console_only_config().sinks),
            root_level=LogLevel.WARNING,
        )
        configure_logging(config)
        root = logging.getLogger()
        assert root.level == logging.WARNING

    def test_default_logger_levels_applied(self) -> None:
        configure_logging(_console_only_config())
        for name, level in _DEFAULT_LOGGER_LEVELS:
            logger = logging.getLogger(name)
            assert logger.level == getattr(logging, level.value), (
                f"{name} expected {level.value}"
            )

    def test_config_overrides_default_levels(self) -> None:
        config = LogConfig(
            sinks=(
                SinkConfig(
                    sink_type=SinkType.CONSOLE,
                    level=LogLevel.DEBUG,
                    json_format=False,
                ),
            ),
            logger_levels=(("synthorg.engine", LogLevel.CRITICAL),),
        )
        configure_logging(config)
        logger = logging.getLogger("synthorg.engine")
        assert logger.level == logging.CRITICAL

    def test_none_config_uses_defaults(self) -> None:
        configure_logging(None)
        root = logging.getLogger()
        assert len(root.handlers) == 7


@pytest.mark.unit
class TestConfigureLoggingFileOutput:
    """Tests for file output from configure_logging."""

    def test_file_sink_creates_log_file(self, tmp_path: Path) -> None:
        configure_logging(_file_config(tmp_path))
        logger = logging.getLogger("test.file_output")
        logger.info("hello file")
        log_file = tmp_path / "test.log"
        assert log_file.exists()

    def test_json_output_is_parseable(self, tmp_path: Path) -> None:
        configure_logging(_file_config(tmp_path))
        logger = logging.getLogger("test.json_output")
        logger.info("json test message")
        log_file = tmp_path / "test.log"
        content = log_file.read_text().strip()
        assert content
        record = json.loads(content)
        assert record["event"] == "json test message"


@pytest.mark.unit
class TestStdlibBridge:
    """Tests for stdlib logging bridge (foreign log records)."""

    def test_stdlib_logger_captured(self, tmp_path: Path) -> None:
        configure_logging(_file_config(tmp_path))
        stdlib_logger = logging.getLogger("uvicorn.access")
        stdlib_logger.setLevel(logging.DEBUG)
        stdlib_logger.info("stdlib bridge test")
        log_file = tmp_path / "test.log"
        content = log_file.read_text().strip()
        assert content
        record = json.loads(content)
        assert record["event"] == "stdlib bridge test"


@pytest.mark.unit
class TestDefaultLoggerLevels:
    """Tests for the _DEFAULT_LOGGER_LEVELS constant."""

    def test_has_twelve_entries(self) -> None:
        assert len(_DEFAULT_LOGGER_LEVELS) == 12

    def test_all_start_with_synthorg(self) -> None:
        for name, _ in _DEFAULT_LOGGER_LEVELS:
            assert name.startswith("synthorg."), name

    def test_all_levels_are_valid(self) -> None:
        for _, level in _DEFAULT_LOGGER_LEVELS:
            assert isinstance(level, LogLevel)


@pytest.mark.unit
class TestSanitizationPipeline:
    """E2E tests for sensitive field sanitization through the pipeline."""

    def test_sensitive_fields_redacted_in_log_file(self, tmp_path: Path) -> None:
        configure_logging(_file_config(tmp_path))
        logger = structlog.get_logger("test.sanitize")
        logger.info("login attempt", password="s3cret", user="alice")
        log_file = tmp_path / "test.log"
        content = log_file.read_text().strip()
        assert content
        record = json.loads(content)
        assert record["password"] == "**REDACTED**"
        assert record["user"] == "alice"


@pytest.mark.unit
class TestCorrelationPipeline:
    """E2E tests for correlation IDs appearing in log file output."""

    def test_correlation_id_in_log_output(self, tmp_path: Path) -> None:
        configure_logging(_file_config(tmp_path))
        bind_correlation_id(request_id="test-req-123")
        logger = structlog.get_logger("test.correlation")
        logger.info("correlated event")
        log_file = tmp_path / "test.log"
        content = log_file.read_text().strip()
        assert content
        record = json.loads(content)
        assert record["request_id"] == "test-req-123"
