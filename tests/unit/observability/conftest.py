"""Test fixtures and factories for observability tests."""

import logging
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator
import structlog
from polyfactory.factories.pydantic_factory import ModelFactory

from ai_company.observability.config import LogConfig, RotationConfig, SinkConfig
from ai_company.observability.enums import LogLevel, RotationStrategy, SinkType

# -- Factories --------------------------------------------------------------


class RotationConfigFactory(ModelFactory[RotationConfig]):
    __model__ = RotationConfig
    strategy = RotationStrategy.BUILTIN
    max_bytes = 10 * 1024 * 1024
    backup_count = 5


class SinkConfigFactory(ModelFactory[SinkConfig]):
    __model__ = SinkConfig
    sink_type = SinkType.CONSOLE
    level = LogLevel.INFO
    file_path = None
    rotation = None
    json_format = False


class LogConfigFactory(ModelFactory[LogConfig]):
    __model__ = LogConfig
    root_level = LogLevel.DEBUG
    logger_levels = ()
    sinks = (
        SinkConfig(
            sink_type=SinkType.CONSOLE,
            level=LogLevel.INFO,
            json_format=False,
        ),
    )
    enable_correlation = True
    log_dir = "logs"


# -- Reset Fixture -----------------------------------------------------------


def _clear_logging_state() -> None:
    """Clear structlog context and stdlib root handlers."""
    structlog.reset_defaults()
    structlog.contextvars.clear_contextvars()
    root = logging.getLogger()
    for handler in root.handlers[:]:
        root.removeHandler(handler)
        handler.close()
    root.setLevel(logging.WARNING)


@pytest.fixture(autouse=True)
def _reset_logging() -> Iterator[None]:
    """Reset structlog and stdlib logging state before and after each test."""
    _clear_logging_state()
    yield
    _clear_logging_state()
