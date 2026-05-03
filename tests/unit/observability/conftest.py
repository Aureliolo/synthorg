"""Test fixtures and factories for observability tests."""

import logging
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator, MutableMapping
import structlog
from polyfactory.factories.pydantic_factory import ModelFactory

from synthorg.observability.config import LogConfig, RotationConfig, SinkConfig
from synthorg.observability.enums import (
    LogLevel,
    OtlpProtocol,
    RotationStrategy,
    SinkType,
    SyslogFacility,
    SyslogProtocol,
)
from tests.conftest import clear_logging_state

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
    syslog_host = None
    syslog_port = 514
    syslog_facility = SyslogFacility.USER
    syslog_protocol = SyslogProtocol.UDP
    http_url = None
    http_headers = ()
    http_batch_size = 100
    http_flush_interval_seconds = 5.0
    http_timeout_seconds = 10.0
    http_max_retries = 3
    otlp_endpoint = None
    otlp_protocol = OtlpProtocol.HTTP_JSON
    otlp_headers = ()
    otlp_export_interval_seconds = 5.0
    otlp_batch_size = 100
    otlp_timeout_seconds = 10.0


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


@pytest.fixture(autouse=True)
def _reset_logging() -> Iterator[None]:
    """Reset structlog and stdlib logging state before and after each test."""
    clear_logging_state()
    yield
    clear_logging_state()


@pytest.fixture
def handler_cleanup() -> Iterator[list[logging.Handler]]:
    """Collect handlers and close them after the test."""
    handlers: list[logging.Handler] = []
    yield handlers
    for h in handlers:
        h.close()


@pytest.fixture
def captured_logs() -> Iterator[list[MutableMapping[str, Any]]]:
    """Capture structlog output as list of dicts for field-level assertions.

    Usage::

        def test_my_event(self, captured_logs: list) -> None:
            do_something()
            events = [e for e in captured_logs if e["event"] == MY_EVENT]
            assert len(events) == 1
    """
    with structlog.testing.capture_logs() as cap:
        yield cap
