# module-kind: declarative
"""Observability configuration models.

Frozen Pydantic models for log sinks, rotation, and top-level logging
configuration.  All models are immutable and validated on construction.

.. note::

    ``DEFAULT_SINKS`` provides the standard eleven-sink layout described
    in the design spec (console + ten file sinks).
"""

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import PurePath, PurePosixPath, PureWindowsPath
from types import MappingProxyType
from typing import Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.types import NotBlankStr
from synthorg.observability._endpoint_safety import validate_otlp_endpoint_safety
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
_DEFAULT_OTLP_MAX_RETRIES: Final[int] = 3


@dataclass(frozen=True)
class _SinkTypeDescriptor:
    """Per-sink-type field contract.

    Drives :meth:`SinkConfig._validate_sink_type_fields`: the sink
    validates the field group it ``owns`` and rejects every other
    group, so a new sink type is one mapping entry instead of another
    ``match`` arm.

    Attributes:
        owns: The field group this sink type validates (``"file"`` /
            ``"syslog"`` / ``"http"`` / ``"otlp"``), or ``None`` when the
            sink type owns no group (CONSOLE, PROMETHEUS).
        require_json: Whether the sink type must carry ``json_format``.
    """

    owns: Literal["file", "syslog", "http", "otlp"] | None
    require_json: bool = False


# Closed set of mutually-exclusive sink field groups. A sink validates
# the group it owns and rejects the rest.
_SINK_FIELD_GROUPS: Final[tuple[str, ...]] = ("file", "syslog", "http", "otlp")

_SINK_TYPE_DESCRIPTORS: Final[Mapping[SinkType, _SinkTypeDescriptor]] = (
    MappingProxyType(
        {
            SinkType.FILE: _SinkTypeDescriptor(owns="file"),
            SinkType.CONSOLE: _SinkTypeDescriptor(owns=None),
            SinkType.SYSLOG: _SinkTypeDescriptor(owns="syslog", require_json=True),
            SinkType.HTTP: _SinkTypeDescriptor(owns="http", require_json=True),
            SinkType.PROMETHEUS: _SinkTypeDescriptor(owns=None),
            SinkType.OTLP: _SinkTypeDescriptor(owns="otlp", require_json=True),
        }
    )
)


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
        """Reject compress_rotated with non-builtin strategy.

        Returns:
            The validated instance (Pydantic ``model_validator`` contract).

        Raises:
            ValueError: If ``compress_rotated`` is set with a non-``BUILTIN``
                rotation strategy.
        """
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
    otlp_max_retries: int = Field(
        default=_DEFAULT_OTLP_MAX_RETRIES,
        ge=0,
        description="Retry count on a transient OTLP export failure",
    )

    @model_validator(mode="after")
    def _validate_sink_type_fields(self) -> Self:
        """Enforce required/rejected fields per sink type.

        Returns:
            The validated instance (Pydantic ``model_validator`` contract).

        Raises:
            ValueError: If the sink omits a field required for its type
                or sets a field forbidden for its type.
        """
        descriptor = _SINK_TYPE_DESCRIPTORS[self.sink_type]
        label = self.sink_type.name
        validators = {
            "file": self._validate_file_fields,
            "syslog": self._validate_syslog_fields,
            "http": self._validate_http_fields,
            "otlp": self._validate_otlp_fields,
        }
        rejecters = {
            "file": self._reject_file_fields,
            "syslog": self._reject_syslog_fields,
            "http": self._reject_http_fields,
            "otlp": self._reject_otlp_fields,
        }
        for group in _SINK_FIELD_GROUPS:
            if group == descriptor.owns:
                validators[group]()
            else:
                rejecters[group](label)
        if descriptor.require_json:
            self._require_json_format(label)
        return self

    def _validate_file_fields(self) -> None:
        """Validate the FILE-sink fields (and reject foreign-type fields).

        Raises:
            ValueError: If ``file_path`` is missing, blank, absolute, or
                contains ``..``, or a syslog/http field is set.
        """
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

    def _reject_file_fields(self, sink_label: str) -> None:
        """Reject FILE-only fields on a non-FILE sink.

        Raises:
            ValueError: If ``file_path`` or ``rotation`` is set on a
                ``sink_label`` sink.
        """
        if self.file_path is not None:
            msg = f"file_path must be None for {sink_label} sinks"
            raise ValueError(msg)
        if self.rotation is not None:
            msg = f"rotation must be None for {sink_label} sinks"
            raise ValueError(msg)

    def _validate_syslog_fields(self) -> None:
        """Validate the required SYSLOG-sink fields.

        Raises:
            ValueError: If ``syslog_host`` is missing or blank.
        """
        if self.syslog_host is None:
            msg = "syslog_host is required for SYSLOG sinks"
            raise ValueError(msg)
        if not self.syslog_host.strip():
            msg = "syslog_host must not be blank"
            raise ValueError(msg)

    def _reject_syslog_fields(self, sink_label: str) -> None:
        """Reject SYSLOG-only fields on a non-SYSLOG sink.

        Raises:
            ValueError: If ``syslog_host`` is set or any syslog field
                differs from its default on a ``sink_label`` sink.
        """
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
        """Validate the required HTTP-sink fields.

        Raises:
            ValueError: If ``http_url`` is missing, blank, lacks an
                ``http(s)`` scheme or host, or a header name is empty.
        """
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
        """Reject HTTP-only fields on a non-HTTP sink.

        Raises:
            ValueError: If ``http_url`` is set or any http field differs
                from its default on a ``sink_label`` sink.
        """
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
        """Require ``json_format=True`` for an always-JSON sink type.

        Raises:
            ValueError: If ``json_format`` is ``False`` for a
                ``sink_label`` sink.
        """
        if not self.json_format:
            msg = f"json_format must be True for {sink_label} sinks (always JSON)"
            raise ValueError(msg)

    def _validate_otlp_fields(self) -> None:
        """Validate the required OTLP-sink fields.

        Raises:
            ValueError: If gRPC transport is requested, ``otlp_endpoint``
                is missing/blank/non-``http(s)``/host-less, or a header
                name is empty or contains CRLF.
        """
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
        validate_otlp_endpoint_safety(
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
        """Reject OTLP-only fields on a non-OTLP sink.

        Raises:
            ValueError: If ``otlp_endpoint`` is set or any OTLP field
                differs from its default on a ``sink_label`` sink.
        """
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
        if self.otlp_max_retries != _DEFAULT_OTLP_MAX_RETRIES:
            msg = (
                f"otlp_max_retries must be default "
                f"({_DEFAULT_OTLP_MAX_RETRIES}) for {sink_label} sinks"
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
        """Ensure at least one sink is configured.

        Returns:
            The validated instance (Pydantic ``model_validator`` contract).

        Raises:
            ValueError: If the ``sinks`` tuple is empty.
        """
        if not self.sinks:
            msg = "At least one sink must be configured"
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_no_duplicate_logger_names(self) -> Self:
        """Ensure no duplicate logger names in ``logger_levels``.

        Returns:
            The validated instance (Pydantic ``model_validator`` contract).

        Raises:
            ValueError: If two entries in ``logger_levels`` share a
                logger name.
        """
        names = [name for name, _ in self.logger_levels]
        counts = Counter(names)
        dupes = sorted(n for n, c in counts.items() if c > 1)
        if dupes:
            msg = f"Duplicate logger names in logger_levels: {dupes}"
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_no_duplicate_file_paths(self) -> Self:
        """Ensure no duplicate file paths across FILE sinks.

        Returns:
            The validated instance (Pydantic ``model_validator`` contract).

        Raises:
            ValueError: If two FILE sinks share a ``file_path``.
        """
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
        """Ensure no duplicate syslog ``(host, port)`` pairs.

        Returns:
            The validated instance (Pydantic ``model_validator`` contract).

        Raises:
            ValueError: If two SYSLOG sinks share a ``(host, port)`` pair.
        """
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
        """Ensure no duplicate HTTP URLs.

        Returns:
            The validated instance (Pydantic ``model_validator`` contract).

        Raises:
            ValueError: If two HTTP sinks share an ``http_url``.
        """
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
        """Ensure ``log_dir`` has no path traversal.

        Returns:
            The validated instance (Pydantic ``model_validator`` contract).

        Raises:
            ValueError: If ``log_dir`` contains ``..`` path components.
        """
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
