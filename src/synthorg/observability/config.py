"""Observability configuration models.

Frozen Pydantic models for log sinks, rotation, and top-level logging
configuration.  All models are immutable and validated on construction.

.. note::

    ``DEFAULT_SINKS`` provides the standard eleven-sink layout described
    in the design spec (console + ten file sinks).
"""

from collections import Counter
from pathlib import PurePath, PurePosixPath, PureWindowsPath
from typing import Final, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.types import NotBlankStr
from synthorg.observability.enums import (
    LogLevel,
    OtlpProtocol,
    RotationStrategy,
    SinkType,
    SyslogFacility,
    SyslogProtocol,
)

# Default values for cross-type field rejection checks
_DEFAULT_SYSLOG_PORT: Final[int] = 514
_DEFAULT_HTTP_BATCH_SIZE: Final[int] = 100
_DEFAULT_HTTP_FLUSH_INTERVAL: Final[float] = 5.0
_DEFAULT_HTTP_TIMEOUT: Final[float] = 10.0
_DEFAULT_HTTP_MAX_RETRIES: Final[int] = 3
_DEFAULT_OTLP_EXPORT_INTERVAL: Final[float] = 5.0
_DEFAULT_OTLP_BATCH_SIZE: Final[int] = 100
_DEFAULT_OTLP_TIMEOUT: Final[float] = 10.0


class RotationConfig(BaseModel):
    """Log file rotation configuration.

    Attributes:
        strategy: Rotation mechanism to use.
        max_bytes: Maximum file size in bytes before rotation.
            Only used when ``strategy`` is
            :attr:`RotationStrategy.BUILTIN`.
        backup_count: Number of rotated backup files to keep.
        compress_rotated: Whether to gzip-compress rotated backup
            files.  Only supported with builtin rotation.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    @model_validator(mode="after")
    def _reject_compress_with_external(self) -> Self:
        """Reject compress_rotated with non-builtin strategy."""
        if self.compress_rotated and self.strategy != RotationStrategy.BUILTIN:
            msg = "compress_rotated is only supported with builtin rotation strategy"
            raise ValueError(msg)
        return self

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
    compress_rotated: bool = Field(
        default=False,
        description="Gzip-compress rotated backup files",
    )


def _is_private_ip(addr_str: str) -> bool:
    """Check whether an IP address string is private/loopback/link-local."""
    import ipaddress  # noqa: PLC0415

    try:
        addr = ipaddress.ip_address(addr_str)
    except ValueError:
        return False
    return bool(addr.is_private or addr.is_loopback or addr.is_link_local)


def _validate_otlp_endpoint_safety(
    endpoint: str,
    hostname: str,
    *,
    has_headers: bool,
) -> None:
    """Reject private IPs (SSRF) and warn on unencrypted HTTP.

    Checks both IP literals and DNS-resolved addresses (best-effort).
    Localhost (127.0.0.1, ::1, ``localhost``) is always allowed as a
    standard local OTLP collector endpoint.
    """
    localhost_names = {"localhost", "127.0.0.1", "::1"}

    # Allow localhost/loopback -- standard for local collectors.
    if hostname in localhost_names:
        return

    # Direct IP literal check (non-localhost private IPs).
    if _is_private_ip(hostname):
        msg = (
            f"otlp_endpoint must not target private/loopback IP addresses ({hostname})"
        )
        raise ValueError(msg)

    # DNS resolution check for hostnames (best-effort).
    if not _is_private_ip(hostname):
        import socket  # noqa: PLC0415

        try:
            addrs = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
        except socket.gaierror:
            # DNS resolution failed -- skip check (hostname may be valid
            # at runtime even if not resolvable at config-load time).
            return
        for _family, _type, _proto, _canonname, sockaddr in addrs:
            resolved_ip = str(sockaddr[0])
            if _is_private_ip(resolved_ip):
                msg = (
                    f"otlp_endpoint hostname {hostname!r} resolves to "
                    f"private/loopback address {resolved_ip}"
                )
                raise ValueError(msg)

    if (
        endpoint.startswith("http://")
        and hostname not in ("localhost", "127.0.0.1", "::1")
        and has_headers
    ):
        import warnings  # noqa: PLC0415

        warnings.warn(
            "OTLP endpoint uses unencrypted HTTP with headers "
            "that may contain secrets; prefer https://",
            UserWarning,
            stacklevel=4,
        )


class SinkConfig(BaseModel):
    """Configuration for a single log output destination.

    Attributes:
        sink_type: Where to send log output.
        level: Minimum log level for this sink.
        file_path: Relative path for FILE sinks (within ``log_dir``).
        rotation: Rotation settings for FILE sinks.
        json_format: Whether to format output as JSON.
        syslog_host: Hostname for SYSLOG sinks.
        syslog_port: Port for SYSLOG sinks.
        syslog_facility: Syslog facility code.
        syslog_protocol: Transport protocol (TCP or UDP).
        http_url: Endpoint URL for HTTP sinks.
        http_headers: Extra HTTP headers as ``(name, value)`` pairs.
        http_batch_size: Records per HTTP POST batch.
        http_flush_interval_seconds: Seconds between automatic flushes.
        http_timeout_seconds: HTTP request timeout.
        http_max_retries: Retry count on HTTP failure.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    sink_type: SinkType = Field(
        description="Log output destination type",
    )
    level: LogLevel = Field(
        default=LogLevel.INFO,
        description="Minimum log level for this sink",
    )
    # FILE fields
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
    # SYSLOG fields
    syslog_host: str | None = Field(
        default=None,
        description="Hostname for SYSLOG sinks",
    )
    syslog_port: int = Field(
        default=514,
        gt=0,
        le=65535,
        description="Port for SYSLOG sinks",
    )
    syslog_facility: SyslogFacility = Field(
        default=SyslogFacility.USER,
        description="Syslog facility code",
    )
    syslog_protocol: SyslogProtocol = Field(
        default=SyslogProtocol.UDP,
        description="Transport protocol (TCP or UDP)",
    )
    # HTTP fields
    http_url: str | None = Field(
        default=None,
        description="Endpoint URL for HTTP sinks",
    )
    http_headers: tuple[tuple[str, str], ...] = Field(
        default=(),
        description="Extra HTTP headers as (name, value) pairs",
    )
    http_batch_size: int = Field(
        default=100,
        gt=0,
        description="Records per HTTP POST batch",
    )
    http_flush_interval_seconds: float = Field(
        default=5.0,
        gt=0,
        description="Seconds between automatic flushes",
    )
    http_timeout_seconds: float = Field(
        default=10.0,
        gt=0,
        description="HTTP request timeout in seconds",
    )
    http_max_retries: int = Field(
        default=3,
        ge=0,
        description="Retry count on HTTP failure",
    )
    # OTLP fields
    otlp_endpoint: str | None = Field(
        default=None,
        description="OTLP collector endpoint URL",
    )
    otlp_protocol: OtlpProtocol = Field(
        default=OtlpProtocol.HTTP_JSON,
        description="OTLP transport protocol",
    )
    otlp_headers: tuple[tuple[str, str], ...] = Field(
        default=(),
        description="Extra OTLP headers as (name, value) pairs",
    )
    otlp_export_interval_seconds: float = Field(
        default=5.0,
        gt=0,
        description="Seconds between OTLP export batches",
    )
    otlp_batch_size: int = Field(
        default=_DEFAULT_OTLP_BATCH_SIZE,
        gt=0,
        description="Records per OTLP export batch",
    )
    otlp_timeout_seconds: float = Field(
        default=_DEFAULT_OTLP_TIMEOUT,
        gt=0,
        description="HTTP request timeout in seconds for OTLP export",
    )

    @model_validator(mode="after")
    def _validate_sink_type_fields(self) -> Self:
        """Enforce required/rejected fields per sink type."""
        match self.sink_type:
            case SinkType.FILE:
                self._validate_file_fields()
                self._reject_otlp_fields("FILE")
            case SinkType.CONSOLE:
                self._reject_file_fields("CONSOLE")
                self._reject_syslog_fields("CONSOLE")
                self._reject_http_fields("CONSOLE")
                self._reject_otlp_fields("CONSOLE")
            case SinkType.SYSLOG:
                self._reject_file_fields("SYSLOG")
                self._validate_syslog_fields()
                self._reject_http_fields("SYSLOG")
                self._require_json_format("SYSLOG")
                self._reject_otlp_fields("SYSLOG")
            case SinkType.HTTP:
                self._reject_file_fields("HTTP")
                self._reject_syslog_fields("HTTP")
                self._validate_http_fields()
                self._require_json_format("HTTP")
                self._reject_otlp_fields("HTTP")
            case SinkType.PROMETHEUS:
                self._reject_file_fields("PROMETHEUS")
                self._reject_syslog_fields("PROMETHEUS")
                self._reject_http_fields("PROMETHEUS")
                self._reject_otlp_fields("PROMETHEUS")
            case SinkType.OTLP:
                self._reject_file_fields("OTLP")
                self._reject_syslog_fields("OTLP")
                self._reject_http_fields("OTLP")
                self._validate_otlp_fields()
                self._require_json_format("OTLP")
        return self

    def _validate_file_fields(self) -> None:
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
        self._reject_syslog_fields("FILE")
        self._reject_http_fields("FILE")

    def _reject_file_fields(self, sink_label: str) -> None:
        if self.file_path is not None:
            msg = f"file_path must be None for {sink_label} sinks"
            raise ValueError(msg)
        if self.rotation is not None:
            msg = f"rotation must be None for {sink_label} sinks"
            raise ValueError(msg)

    def _validate_syslog_fields(self) -> None:
        if self.syslog_host is None:
            msg = "syslog_host is required for SYSLOG sinks"
            raise ValueError(msg)
        if not self.syslog_host.strip():
            msg = "syslog_host must not be blank"
            raise ValueError(msg)

    def _reject_syslog_fields(self, sink_label: str) -> None:
        if self.syslog_host is not None:
            msg = f"syslog_host must be None for {sink_label} sinks"
            raise ValueError(msg)
        if self.syslog_port != _DEFAULT_SYSLOG_PORT:
            msg = f"syslog_port must be default (514) for {sink_label} sinks"
            raise ValueError(msg)
        if self.syslog_facility != SyslogFacility.USER:
            msg = f"syslog_facility must be default (USER) for {sink_label} sinks"
            raise ValueError(msg)
        if self.syslog_protocol != SyslogProtocol.UDP:
            msg = f"syslog_protocol must be default (UDP) for {sink_label} sinks"
            raise ValueError(msg)

    def _validate_http_fields(self) -> None:
        if self.http_url is None:
            msg = "http_url is required for HTTP sinks"
            raise ValueError(msg)
        if not self.http_url.strip():
            msg = "http_url must not be blank"
            raise ValueError(msg)
        if not (
            self.http_url.startswith("http://") or self.http_url.startswith("https://")
        ):
            msg = "http_url must start with http:// or https://"
            raise ValueError(msg)
        from urllib.parse import urlparse  # noqa: PLC0415

        parsed = urlparse(self.http_url)
        if not parsed.hostname:
            msg = "http_url must include a host"
            raise ValueError(msg)
        for i, (name, _value) in enumerate(self.http_headers):
            if not name or not name.strip():
                msg = f"http_headers[{i}] has an empty header name"
                raise ValueError(msg)

    def _reject_http_fields(self, sink_label: str) -> None:
        if self.http_url is not None:
            msg = f"http_url must be None for {sink_label} sinks"
            raise ValueError(msg)
        if self.http_headers != ():
            msg = f"http_headers must be empty for {sink_label} sinks"
            raise ValueError(msg)
        if self.http_batch_size != _DEFAULT_HTTP_BATCH_SIZE:
            msg = f"http_batch_size must be default (100) for {sink_label} sinks"
            raise ValueError(msg)
        if self.http_flush_interval_seconds != _DEFAULT_HTTP_FLUSH_INTERVAL:
            msg = (
                "http_flush_interval_seconds must be default (5.0) "
                f"for {sink_label} sinks"
            )
            raise ValueError(msg)
        if self.http_timeout_seconds != _DEFAULT_HTTP_TIMEOUT:
            msg = f"http_timeout_seconds must be default (10.0) for {sink_label} sinks"
            raise ValueError(msg)
        if self.http_max_retries != _DEFAULT_HTTP_MAX_RETRIES:
            msg = f"http_max_retries must be default (3) for {sink_label} sinks"
            raise ValueError(msg)

    def _require_json_format(self, sink_label: str) -> None:
        if not self.json_format:
            msg = f"json_format must be True for {sink_label} sinks (always JSON)"
            raise ValueError(msg)

    def _validate_otlp_fields(self) -> None:
        if self.otlp_protocol == OtlpProtocol.GRPC:
            msg = "OTLP gRPC transport is not supported; use HTTP_JSON"
            raise ValueError(msg)
        if self.otlp_endpoint is None:
            msg = "otlp_endpoint is required for OTLP sinks"
            raise ValueError(msg)
        if not self.otlp_endpoint.strip():
            msg = "otlp_endpoint must not be blank"
            raise ValueError(msg)
        if not (
            self.otlp_endpoint.startswith("http://")
            or self.otlp_endpoint.startswith("https://")
        ):
            msg = "otlp_endpoint must start with http:// or https://"
            raise ValueError(msg)
        from urllib.parse import urlparse  # noqa: PLC0415

        parsed = urlparse(self.otlp_endpoint)
        if not parsed.hostname:
            msg = "otlp_endpoint must include a host"
            raise ValueError(msg)
        _validate_otlp_endpoint_safety(
            self.otlp_endpoint,
            parsed.hostname,
            has_headers=bool(self.otlp_headers),
        )
        for i, (name, value) in enumerate(self.otlp_headers):
            if not name or not name.strip():
                msg = f"otlp_headers[{i}] has an empty header name"
                raise ValueError(msg)
            if "\r" in name or "\n" in name:
                msg = f"otlp_headers[{i}] name contains CRLF"
                raise ValueError(msg)
            if "\r" in value or "\n" in value:
                msg = f"otlp_headers[{i}] value contains CRLF"
                raise ValueError(msg)

    def _reject_otlp_fields(self, sink_label: str) -> None:
        if self.otlp_endpoint is not None:
            msg = f"otlp_endpoint must be None for {sink_label} sinks"
            raise ValueError(msg)
        if self.otlp_headers != ():
            msg = f"otlp_headers must be empty for {sink_label} sinks"
            raise ValueError(msg)
        if self.otlp_export_interval_seconds != _DEFAULT_OTLP_EXPORT_INTERVAL:
            msg = (
                "otlp_export_interval_seconds must be default (5.0) "
                f"for {sink_label} sinks"
            )
            raise ValueError(msg)
        if self.otlp_protocol != OtlpProtocol.HTTP_JSON:
            msg = f"otlp_protocol must be default (http/json) for {sink_label} sinks"
            raise ValueError(msg)
        if self.otlp_batch_size != _DEFAULT_OTLP_BATCH_SIZE:
            msg = (
                f"otlp_batch_size must be default "
                f"({_DEFAULT_OTLP_BATCH_SIZE}) for {sink_label} sinks"
            )
            raise ValueError(msg)
        if self.otlp_timeout_seconds != _DEFAULT_OTLP_TIMEOUT:
            msg = (
                f"otlp_timeout_seconds must be default "
                f"({_DEFAULT_OTLP_TIMEOUT}) for {sink_label} sinks"
            )
            raise ValueError(msg)


class ContainerLogShippingConfig(BaseModel):
    """Configuration for shipping container logs to the observability stack.

    Controls whether sandbox and sidecar container logs are collected
    and shipped through the structlog pipeline after execution.

    Attributes:
        enabled: Whether container log shipping is active.
        ship_raw_logs: Whether to include raw stdout/stderr/sidecar
            payloads in shipped events (security-sensitive).
        collection_timeout_seconds: Timeout for collecting container logs.
        max_log_bytes: Total byte budget across all shipped fields
            per execution (stdout + stderr + sidecar logs combined).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    enabled: bool = Field(
        default=True,
        description="Whether to ship collected container logs",
    )
    ship_raw_logs: bool = Field(
        default=False,
        description=(
            "Include raw stdout/stderr/sidecar payloads in shipped events. "
            "When False, only metadata (sizes, counts, timing) is shipped. "
            "Enable only in trusted environments -- raw output may contain "
            "secrets that bypass key-name-based redaction."
        ),
    )
    collection_timeout_seconds: float = Field(
        default=5.0,
        ge=0.1,
        le=30.0,
        description="Timeout for log collection from containers",
    )
    max_log_bytes: int = Field(
        default=10 * 1024 * 1024,
        gt=0,
        description="Total byte budget per execution across all shipped fields",
    )


class LogConfig(BaseModel):
    """Top-level logging configuration.

    Attributes:
        root_level: Root logger level (handlers filter individually).
        logger_levels: Per-logger level overrides as ``(name, level)`` pairs.
        sinks: Tuple of sink configurations.
        enable_correlation: Whether to enable correlation ID tracking.
        log_dir: Directory for log files.
        console_level: Optional override for the console sink's log
            level, distinct from ``root_level``.  Empty string means
            "use the per-sink level / root_level default".  Mirrors the
            ``observability.log_level_console`` registry entry; the
            console-override applier reads DB > env > YAML (this field)
            > unset.
        container_log_shipping: Container log shipping configuration.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    root_level: LogLevel = Field(
        default=LogLevel.INFO,
        description=(
            "Root logger level. Defaults to INFO so HTTP log sinks do not"
            " leak verbose payloads or burn bandwidth on sampled streams;"
            " set to DEBUG explicitly (settings: observability.root_level)"
            " when operators need the full event stream. Per-logger"
            " overrides still force DEBUG on synthorg.engine /"
            " synthorg.memory so agent traces stay detailed."
        ),
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
    console_level: str = Field(
        default="",
        description=(
            "Optional console-sink level override (mutable); empty string"
            " means use the per-sink / root level. The applier resolves"
            " DB > env (SYNTHORG_LOG_LEVEL) > YAML (this field) > unset"
            " through the observability.log_level_console registry entry."
        ),
    )
    container_log_shipping: ContainerLogShippingConfig = Field(
        default_factory=ContainerLogShippingConfig,
        description="Container log shipping configuration",
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
    def _validate_no_duplicate_syslog_endpoints(self) -> Self:
        """Ensure no duplicate syslog ``(host, port)`` pairs."""
        endpoints = [
            (s.syslog_host.strip() if s.syslog_host else "", s.syslog_port)
            for s in self.sinks
            if s.sink_type == SinkType.SYSLOG
        ]
        counts = Counter(endpoints)
        dupes = sorted(f"{h}:{p}" for (h, p), c in counts.items() if c > 1)
        if dupes:
            msg = f"Duplicate syslog endpoints: {dupes}"
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_no_duplicate_http_urls(self) -> Self:
        """Ensure no duplicate HTTP URLs."""
        urls = [
            s.http_url
            for s in self.sinks
            if s.sink_type == SinkType.HTTP and s.http_url is not None
        ]
        counts = Counter(urls)
        dupes = sorted(u for u, c in counts.items() if c > 1)
        if dupes:
            msg = f"Duplicate HTTP URLs: {dupes}"
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
        file_path="synthorg.log",
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
    SinkConfig(
        sink_type=SinkType.FILE,
        level=LogLevel.INFO,
        file_path="access.log",
        rotation=RotationConfig(),
        json_format=True,
    ),
    SinkConfig(
        sink_type=SinkType.FILE,
        level=LogLevel.INFO,
        file_path="persistence.log",
        rotation=RotationConfig(),
        json_format=True,
    ),
    SinkConfig(
        sink_type=SinkType.FILE,
        level=LogLevel.INFO,
        file_path="configuration.log",
        rotation=RotationConfig(),
        json_format=True,
    ),
    SinkConfig(
        sink_type=SinkType.FILE,
        level=LogLevel.INFO,
        file_path="backup.log",
        rotation=RotationConfig(),
        json_format=True,
    ),
)
