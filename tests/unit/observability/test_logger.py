"""Tests for the get_logger convenience wrapper."""

import json
from typing import TYPE_CHECKING

import pytest

from synthorg.observability._logger import get_logger
from synthorg.observability.config import LogConfig, SinkConfig
from synthorg.observability.enums import LogLevel, SinkType
from synthorg.observability.setup import configure_logging

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.timeout(30)


@pytest.mark.unit
class TestGetLogger:
    """Tests for get_logger function."""

    def test_returns_usable_logger(self) -> None:
        configure_logging()
        logger = get_logger("test.module")
        assert hasattr(logger, "info")
        assert hasattr(logger, "debug")
        assert hasattr(logger, "warning")
        assert hasattr(logger, "error")

    def test_logger_name_bound(self, tmp_path: Path) -> None:
        config = LogConfig(
            sinks=(
                SinkConfig(
                    sink_type=SinkType.FILE,
                    level=LogLevel.DEBUG,
                    file_path="name-bound.log",
                    json_format=True,
                ),
            ),
            log_dir=str(tmp_path),
        )
        configure_logging(config)
        logger = get_logger("my.module")
        logger.info("name-check")
        log_file = tmp_path / "name-bound.log"
        content = log_file.read_text().strip()
        assert content
        record = json.loads(content)
        assert record["logger"] == "my.module"

    def test_initial_bindings_applied(self, tmp_path: Path) -> None:
        config = LogConfig(
            sinks=(
                SinkConfig(
                    sink_type=SinkType.FILE,
                    level=LogLevel.DEBUG,
                    file_path="bindings.log",
                    json_format=True,
                ),
            ),
            log_dir=str(tmp_path),
        )
        configure_logging(config)
        logger = get_logger("test.bindings", service="api")
        logger.info("binding-check")
        log_file = tmp_path / "bindings.log"
        content = log_file.read_text().strip()
        assert content
        record = json.loads(content)
        assert record["service"] == "api"

    def test_different_names_return_different_loggers(self) -> None:
        configure_logging()
        logger_a = get_logger("module.a")
        logger_b = get_logger("module.b")
        # They should be distinct instances
        assert logger_a is not logger_b
