"""Tests for syslog handler builder."""

import json
import logging
import logging.handlers
import socket
from typing import Any
from unittest.mock import patch

import pytest
import structlog
from structlog.stdlib import ProcessorFormatter

from synthorg.observability.config import SinkConfig
from synthorg.observability.enums import (
    LogLevel,
    SinkType,
    SyslogFacility,
    SyslogProtocol,
)
from synthorg.observability.syslog_handler import (
    FACILITY_MAP,
    PROTOCOL_MAP,
    build_syslog_handler,
)


def _syslog_sink(**overrides: Any) -> SinkConfig:
    defaults: dict[str, Any] = {
        "sink_type": SinkType.SYSLOG,
        "syslog_host": "localhost",
    }
    defaults.update(overrides)
    return SinkConfig(**defaults)


@pytest.mark.unit
class TestBuildSyslogHandler:
    """Tests for build_syslog_handler factory."""

    def test_returns_syslog_handler(
        self,
        handler_cleanup: list[logging.Handler],
    ) -> None:
        sink = _syslog_sink()
        handler = build_syslog_handler(sink, foreign_pre_chain=[])
        handler_cleanup.append(handler)
        assert isinstance(handler, logging.handlers.SysLogHandler)

    def test_udp_default(
        self,
        handler_cleanup: list[logging.Handler],
    ) -> None:
        sink = _syslog_sink()
        handler = build_syslog_handler(sink, foreign_pre_chain=[])
        handler_cleanup.append(handler)
        assert handler.socktype == socket.SOCK_DGRAM

    def test_tcp_protocol(
        self,
        handler_cleanup: list[logging.Handler],
    ) -> None:
        sink = _syslog_sink(syslog_protocol=SyslogProtocol.TCP)
        # TCP SysLogHandler tries to connect immediately -- mock socket
        with patch(
            "logging.handlers.SysLogHandler.createSocket",
        ):
            handler = build_syslog_handler(sink, foreign_pre_chain=[])
            handler_cleanup.append(handler)
            assert handler.socktype == socket.SOCK_STREAM

    def test_custom_host_and_port(
        self,
        handler_cleanup: list[logging.Handler],
    ) -> None:
        sink = _syslog_sink(
            syslog_host="10.0.0.1",
            syslog_port=1514,
        )
        handler = build_syslog_handler(sink, foreign_pre_chain=[])
        handler_cleanup.append(handler)
        assert handler.address == ("10.0.0.1", 1514)

    def test_handler_level_set(
        self,
        handler_cleanup: list[logging.Handler],
    ) -> None:
        sink = _syslog_sink(level=LogLevel.ERROR)
        handler = build_syslog_handler(sink, foreign_pre_chain=[])
        handler_cleanup.append(handler)
        assert handler.level == logging.ERROR

    def test_json_formatter_attached(
        self,
        handler_cleanup: list[logging.Handler],
    ) -> None:
        sink = _syslog_sink()
        handler = build_syslog_handler(sink, foreign_pre_chain=[])
        handler_cleanup.append(handler)
        assert isinstance(handler.formatter, ProcessorFormatter)

    @pytest.mark.parametrize(
        ("facility", "expected"),
        [
            (SyslogFacility.USER, logging.handlers.SysLogHandler.LOG_USER),
            (SyslogFacility.DAEMON, logging.handlers.SysLogHandler.LOG_DAEMON),
            (SyslogFacility.LOCAL0, logging.handlers.SysLogHandler.LOG_LOCAL0),
            (SyslogFacility.LOCAL7, logging.handlers.SysLogHandler.LOG_LOCAL7),
            (SyslogFacility.AUTH, logging.handlers.SysLogHandler.LOG_AUTH),
            (SyslogFacility.KERN, logging.handlers.SysLogHandler.LOG_KERN),
            (SyslogFacility.SYSLOG, logging.handlers.SysLogHandler.LOG_SYSLOG),
        ],
        ids=[
            "user",
            "daemon",
            "local0",
            "local7",
            "auth",
            "kern",
            "syslog",
        ],
    )
    def test_facility_mapping(
        self,
        facility: SyslogFacility,
        expected: int,
        handler_cleanup: list[logging.Handler],
    ) -> None:
        sink = _syslog_sink(syslog_facility=facility)
        handler = build_syslog_handler(sink, foreign_pre_chain=[])
        handler_cleanup.append(handler)
        assert handler.facility == expected


@pytest.mark.unit
class TestFacilityAndProtocolMaps:
    """Tests for the mapping dictionaries."""

    def test_facility_map_covers_all_members(self) -> None:
        for member in SyslogFacility:
            assert member in FACILITY_MAP

    def test_protocol_map_covers_all_members(self) -> None:
        for member in SyslogProtocol:
            assert member in PROTOCOL_MAP

    def test_protocol_map_values(self) -> None:
        assert PROTOCOL_MAP[SyslogProtocol.TCP] == socket.SOCK_STREAM
        assert PROTOCOL_MAP[SyslogProtocol.UDP] == socket.SOCK_DGRAM


@pytest.mark.unit
class TestSyslogHandlerEmit:
    """Tests for syslog handler emit behavior."""

    def test_emit_formats_as_json(
        self,
        handler_cleanup: list[logging.Handler],
    ) -> None:
        sink = _syslog_sink()
        pre_chain = [
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
        ]
        handler = build_syslog_handler(sink, foreign_pre_chain=pre_chain)
        handler_cleanup.append(handler)

        record = logging.LogRecord(
            name="test.logger",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="hello syslog",
            args=(),
            exc_info=None,
        )
        formatted = handler.format(record)
        parsed = json.loads(formatted)
        assert isinstance(parsed, dict)
        assert "event" in parsed

    def test_empty_host_raises(self) -> None:
        """build_syslog_handler rejects empty syslog_host."""
        sink = _syslog_sink()
        # Bypass SinkConfig validation to force empty host
        object.__setattr__(sink, "syslog_host", "")
        with pytest.raises(ValueError, match="non-empty syslog_host"):
            build_syslog_handler(sink, foreign_pre_chain=[])

    def test_connection_failure_raises_runtime_error(self) -> None:
        """TCP connection failure is wrapped in RuntimeError."""
        sink = _syslog_sink(syslog_protocol=SyslogProtocol.TCP)
        with (
            patch(
                "logging.handlers.SysLogHandler.__init__",
                side_effect=OSError("Connection refused"),
            ),
            pytest.raises(RuntimeError, match="Failed to connect"),
        ):
            build_syslog_handler(sink, foreign_pre_chain=[])
