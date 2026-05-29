# ruff: noqa: TRY004 -- consistent ValueError API with sink_config_builder
"""Syslog and HTTP custom sink parsers for the sink config builder.

Extracted from ``sink_config_builder`` to keep the main module under
the 800-line limit.  These functions are internal helpers -- import
them via ``sink_config_builder`` dispatch, not directly.
"""

from typing import Any

from synthorg.observability.config import SinkConfig
from synthorg.observability.enums import (
    SinkType,
    SyslogFacility,
    SyslogProtocol,
)

_SYSLOG_FACILITY_MAP: dict[str, SyslogFacility] = {f.value: f for f in SyslogFacility}
_SYSLOG_PROTOCOL_MAP: dict[str, SyslogProtocol] = {p.value: p for p in SyslogProtocol}


def _parse_syslog_optional_fields(
    entry: dict[str, Any],
    ctx: str,
    *,
    parse_int: Any,
    parse_enum: Any,
) -> tuple[int, SyslogFacility, SyslogProtocol]:
    """Parse optional syslog fields from a custom sink entry.

    Returns:
        A ``(port, facility, protocol)`` tuple, using the syslog defaults
        (514, USER, UDP) for any key absent from the entry.
    """
    port = parse_int(entry, "syslog_port", ctx) if "syslog_port" in entry else 514
    facility = (
        parse_enum(
            entry,
            "syslog_facility",
            _SYSLOG_FACILITY_MAP,
            "syslog facility",
            ctx,
        )
        if "syslog_facility" in entry
        else SyslogFacility.USER
    )
    protocol = (
        parse_enum(
            entry,
            "syslog_protocol",
            _SYSLOG_PROTOCOL_MAP,
            "syslog protocol",
            ctx,
        )
        if "syslog_protocol" in entry
        else SyslogProtocol.UDP
    )
    return port, facility, protocol


def build_custom_syslog_sink(
    entry: dict[str, Any],
    index: int,
    *,
    parse_common: Any,
    parse_int: Any,
    parse_enum: Any,
) -> SinkConfig:
    """Build a SYSLOG SinkConfig from a custom sink entry.

    Returns:
        A ``SinkConfig`` for a SYSLOG sink built from the entry.

    Raises:
        ValueError: If ``syslog_host`` is absent or not a non-empty string.
    """
    ctx = f"custom_sinks[{index}]"
    if "syslog_host" not in entry:
        msg = f"{ctx} is missing required field 'syslog_host' for syslog sink"
        raise ValueError(msg)

    raw_host = entry["syslog_host"]
    if not isinstance(raw_host, str) or not raw_host.strip():
        msg = f"{ctx}.syslog_host must be a non-empty string"
        raise ValueError(msg)

    level, _json_format = parse_common(
        entry,
        index,
        sink_type="syslog",
    )
    port, facility, protocol = _parse_syslog_optional_fields(
        entry,
        ctx,
        parse_int=parse_int,
        parse_enum=parse_enum,
    )

    return SinkConfig(
        sink_type=SinkType.SYSLOG,
        level=level,
        syslog_host=raw_host.strip(),
        syslog_port=port,
        syslog_facility=facility,
        syslog_protocol=protocol,
    )


def _parse_http_headers(
    entry: dict[str, Any],
    index: int,
) -> tuple[tuple[str, str], ...]:
    """Parse and validate HTTP headers from a custom sink entry.

    Returns:
        An immutable tuple of ``(name, value)`` string pairs.

    Raises:
        ValueError: If ``http_headers`` is not a list, an element is not
            a 2-element ``[str, str]`` pair, or a header name is empty
            after stripping.
    """
    raw_headers = entry["http_headers"]
    if not isinstance(raw_headers, list):
        msg = f"custom_sinks[{index}].http_headers must be an array"
        raise ValueError(msg)
    headers: list[tuple[str, str]] = []
    for j, pair in enumerate(raw_headers):
        if (
            not isinstance(pair, list)
            or len(pair) != 2  # noqa: PLR2004
            or not isinstance(pair[0], str)
            or not isinstance(pair[1], str)
        ):
            msg = (
                f"custom_sinks[{index}].http_headers[{j}] must be "
                "a [name, value] string pair"
            )
            raise ValueError(msg)
        name = pair[0].strip()
        if not name:
            msg = f"custom_sinks[{index}].http_headers[{j}] has an empty header name"
            raise ValueError(msg)
        headers.append((name, pair[1]))
    return tuple(headers)


def build_custom_http_sink(
    entry: dict[str, Any],
    index: int,
    *,
    parse_common: Any,
    parse_int: Any,
    parse_number: Any,
) -> SinkConfig:
    """Build an HTTP SinkConfig from a custom sink entry.

    Returns:
        A ``SinkConfig`` for an HTTP sink built from the entry.

    Raises:
        ValueError: If ``http_url`` is absent or not a non-empty string.
    """
    ctx = f"custom_sinks[{index}]"
    if "http_url" not in entry:
        msg = f"{ctx} is missing required field 'http_url' for http sink"
        raise ValueError(msg)

    raw_url = entry["http_url"]
    if not isinstance(raw_url, str) or not raw_url.strip():
        msg = f"{ctx}.http_url must be a non-empty string"
        raise ValueError(msg)

    level, _json_format = parse_common(
        entry,
        index,
        sink_type="http",
    )
    kwargs: dict[str, Any] = {
        "sink_type": SinkType.HTTP,
        "level": level,
        "http_url": raw_url.strip(),
    }

    for int_key in ("http_batch_size", "http_max_retries"):
        if int_key in entry:
            kwargs[int_key] = parse_int(entry, int_key, ctx)

    for num_key in (
        "http_flush_interval_seconds",
        "http_timeout_seconds",
    ):
        if num_key in entry:
            kwargs[num_key] = parse_number(entry, num_key, ctx)

    if "http_headers" in entry:
        kwargs["http_headers"] = _parse_http_headers(entry, index)

    return SinkConfig(**kwargs)
