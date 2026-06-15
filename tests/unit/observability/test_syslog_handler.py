"""Tests for syslog handler builder."""

import json
import logging
import logging.handlers
import socket
from unittest.mock import MagicMock, patch

import pytest
import structlog
import structlog.testing
from structlog.stdlib import ProcessorFormatter
from structlog.typing import Processor

from synthorg.observability.config import SinkConfig
from synthorg.observability.enums import (
    LogLevel,
    SinkType,
    SyslogFacility,
    SyslogProtocol,
)
from synthorg.observability.errors import SinkConstructionError
from synthorg.observability.events.metrics import (
    METRICS_LOG_SINK_CALLBACK_ERROR,
    METRICS_LOG_SINK_EXPORT_FAILED,
)
from synthorg.observability.syslog_handler import (
    FACILITY_MAP,
    PROTOCOL_MAP,
    CountingSysLogHandler,
    build_syslog_handler,
)


def _syslog_sink(**overrides: object) -> SinkConfig:
    defaults: dict[str, object] = {
        "sink_type": SinkType.SYSLOG,
        "syslog_host": "localhost",
    }
    defaults.update(overrides)
    return SinkConfig.model_validate(defaults)


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
        pre_chain: list[Processor] = [
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

    def test_connection_failure_raises_sink_construction_error(self) -> None:
        """TCP connection failure is wrapped in a typed SinkConstructionError."""
        sink = _syslog_sink(syslog_protocol=SyslogProtocol.TCP)
        with (
            patch(
                "logging.handlers.SysLogHandler.__init__",
                side_effect=OSError("Connection refused"),
            ),
            pytest.raises(SinkConstructionError, match="Failed to connect"),
        ):
            build_syslog_handler(sink, foreign_pre_chain=[])


def _plain_record(msg: str = "hello") -> logging.LogRecord:
    return logging.LogRecord(
        name="test.logger",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg=msg,
        args=(),
        exc_info=None,
    )


@pytest.mark.unit
class TestSyslogExportCallback:
    """The subclass counts drops and reports export outcomes."""

    def _handler(
        self,
        handler_cleanup: list[logging.Handler],
    ) -> CountingSysLogHandler:
        handler = build_syslog_handler(_syslog_sink(), foreign_pre_chain=[])
        handler.setFormatter(logging.Formatter("%(message)s"))
        handler_cleanup.append(handler)
        return handler

    def test_returns_counting_subclass(
        self,
        handler_cleanup: list[logging.Handler],
    ) -> None:
        handler = self._handler(handler_cleanup)
        assert isinstance(handler, CountingSysLogHandler)

    def test_successful_emit_invokes_success_callback(
        self,
        handler_cleanup: list[logging.Handler],
    ) -> None:
        handler = self._handler(handler_cleanup)
        outcomes: list[tuple[str, int]] = []
        handler.set_export_callback(lambda o, d: outcomes.append((o, d)))
        handler.socket = MagicMock(spec=socket.socket)  # type: ignore[attr-defined]

        handler.emit(_plain_record())

        assert outcomes == [("success", 0)]

    def test_failed_emit_counts_drop_and_invokes_failure_callback(
        self,
        handler_cleanup: list[logging.Handler],
    ) -> None:
        handler = self._handler(handler_cleanup)
        outcomes: list[tuple[str, int]] = []
        handler.set_export_callback(lambda o, d: outcomes.append((o, d)))
        mock_socket = MagicMock(spec=socket.socket)
        mock_socket.sendto.side_effect = OSError("syslog down")
        handler.socket = mock_socket  # type: ignore[attr-defined]

        with structlog.testing.capture_logs() as logs:
            handler.emit(_plain_record())

        assert outcomes == [("failure", 1)]
        assert handler._dropped_count == 1
        drops = [
            rec
            for rec in logs
            if rec.get("event") == METRICS_LOG_SINK_EXPORT_FAILED
            and rec.get("sink") == "syslog"
        ]
        assert drops, "a failed syslog emit must log redacted drop context"
        assert drops[0].get("error_type") == "OSError"
        assert drops[0].get("error")

    def test_set_export_callback_rejects_non_callable(
        self,
        handler_cleanup: list[logging.Handler],
    ) -> None:
        handler = self._handler(handler_cleanup)
        with pytest.raises(TypeError, match="callable or None"):
            handler.set_export_callback(42)  # type: ignore[arg-type]

    def test_invoke_export_callback_swallows_non_critical_exception(
        self,
        handler_cleanup: list[logging.Handler],
    ) -> None:
        """A throwing callback is logged, not propagated, and never breaks emit."""
        handler = self._handler(handler_cleanup)

        def _boom(_outcome: str, _dropped: int) -> None:
            msg = "callback boom"
            raise RuntimeError(msg)

        handler.set_export_callback(_boom)
        with structlog.testing.capture_logs() as logs:
            handler._invoke_export_callback("success", 0)

        errors = [
            rec
            for rec in logs
            if rec.get("event") == METRICS_LOG_SINK_CALLBACK_ERROR
            and rec.get("sink") == "syslog"
        ]
        assert errors, "a throwing callback must log METRICS_LOG_SINK_CALLBACK_ERROR"
        assert errors[0].get("error_type") == "RuntimeError"
