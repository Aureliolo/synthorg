"""Syslog handler builder for shipping structured logs to syslog endpoints.

Builds a ``logging.handlers.SysLogHandler`` configured for structured
JSON output via structlog's ``ProcessorFormatter``.
"""

import logging.handlers
import socket
from types import MappingProxyType

import structlog
from structlog.stdlib import ProcessorFormatter
from structlog.typing import Processor

from synthorg.observability.config import SinkConfig
from synthorg.observability.enums import SyslogFacility, SyslogProtocol
from synthorg.observability.redaction import safe_error_description

FACILITY_MAP: MappingProxyType[SyslogFacility, int] = MappingProxyType(
    {
        SyslogFacility.USER: logging.handlers.SysLogHandler.LOG_USER,
        SyslogFacility.DAEMON: logging.handlers.SysLogHandler.LOG_DAEMON,
        SyslogFacility.SYSLOG: logging.handlers.SysLogHandler.LOG_SYSLOG,
        SyslogFacility.AUTH: logging.handlers.SysLogHandler.LOG_AUTH,
        SyslogFacility.KERN: logging.handlers.SysLogHandler.LOG_KERN,
        SyslogFacility.LOCAL0: logging.handlers.SysLogHandler.LOG_LOCAL0,
        SyslogFacility.LOCAL1: logging.handlers.SysLogHandler.LOG_LOCAL1,
        SyslogFacility.LOCAL2: logging.handlers.SysLogHandler.LOG_LOCAL2,
        SyslogFacility.LOCAL3: logging.handlers.SysLogHandler.LOG_LOCAL3,
        SyslogFacility.LOCAL4: logging.handlers.SysLogHandler.LOG_LOCAL4,
        SyslogFacility.LOCAL5: logging.handlers.SysLogHandler.LOG_LOCAL5,
        SyslogFacility.LOCAL6: logging.handlers.SysLogHandler.LOG_LOCAL6,
        SyslogFacility.LOCAL7: logging.handlers.SysLogHandler.LOG_LOCAL7,
    }
)

PROTOCOL_MAP: MappingProxyType[SyslogProtocol, int] = MappingProxyType(
    {
        SyslogProtocol.TCP: socket.SOCK_STREAM,
        SyslogProtocol.UDP: socket.SOCK_DGRAM,
    }
)


def build_syslog_handler(
    sink: SinkConfig,
    foreign_pre_chain: list[Processor],
) -> logging.handlers.SysLogHandler:
    """Build a SysLogHandler from a SYSLOG sink configuration.

    Args:
        sink: The SYSLOG sink configuration.
        foreign_pre_chain: Processor chain for stdlib-originated logs.

    Returns:
        A configured ``SysLogHandler`` with JSON formatting.

    Raises:
        ValueError: If ``sink.syslog_host`` is absent or blank.
        RuntimeError: If the OS-level socket connection to the syslog
            endpoint fails.
    """
    if not sink.syslog_host or not sink.syslog_host.strip():
        msg = "SYSLOG sink requires a non-empty syslog_host"
        raise ValueError(msg)
    host = sink.syslog_host.strip()
    try:
        handler = logging.handlers.SysLogHandler(
            address=(host, sink.syslog_port),
            facility=FACILITY_MAP[sink.syslog_facility],
            socktype=socket.SocketKind(
                PROTOCOL_MAP[sink.syslog_protocol],
            ),
        )
    except OSError as exc:
        msg = (
            f"Failed to connect to syslog endpoint "
            f"{sink.syslog_host}:{sink.syslog_port} "
            f"({sink.syslog_protocol.value.upper()}): {safe_error_description(exc)}"
        )
        raise RuntimeError(msg) from exc
    # UDP: disable NUL terminator (datagrams are self-framing, NUL
    # corrupts JSON parsers).  TCP: keep NUL (the traditional syslog-
    # over-TCP message delimiter that receivers expect).
    handler.append_nul = sink.syslog_protocol == SyslogProtocol.TCP
    handler.setLevel(sink.level.value)

    renderer: Processor = structlog.processors.JSONRenderer()
    processors: list[Processor] = [
        ProcessorFormatter.remove_processors_meta,
        structlog.processors.format_exc_info,
        renderer,
    ]
    formatter = ProcessorFormatter(
        processors=processors,
        foreign_pre_chain=foreign_pre_chain,
    )
    handler.setFormatter(formatter)

    return handler
