"""Tests for observability configuration models."""

import pytest
from pydantic import ValidationError

from synthorg.observability.config import (
    DEFAULT_SINKS,
    ContainerLogShippingConfig,
    LogConfig,
    RotationConfig,
    SinkConfig,
)
from synthorg.observability.enums import (
    LogLevel,
    OtlpProtocol,
    RotationStrategy,
    SinkType,
    SyslogFacility,
    SyslogProtocol,
)

from .conftest import LogConfigFactory, RotationConfigFactory, SinkConfigFactory

# ── RotationConfig ─────────────────────────────────────────────────


@pytest.mark.unit
class TestRotationConfig:
    """Tests for RotationConfig defaults, validation, and immutability."""

    def test_defaults(self) -> None:
        cfg = RotationConfig()
        assert cfg.strategy == RotationStrategy.BUILTIN
        assert cfg.max_bytes == 10 * 1024 * 1024
        assert cfg.backup_count == 5

    def test_custom_values(self) -> None:
        cfg = RotationConfig(
            strategy=RotationStrategy.EXTERNAL,
            max_bytes=5_000_000,
            backup_count=3,
        )
        assert cfg.strategy == RotationStrategy.EXTERNAL
        assert cfg.max_bytes == 5_000_000
        assert cfg.backup_count == 3

    def test_max_bytes_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            RotationConfig(max_bytes=0)

    def test_max_bytes_negative_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RotationConfig(max_bytes=-1)

    def test_backup_count_zero_accepted(self) -> None:
        cfg = RotationConfig(backup_count=0)
        assert cfg.backup_count == 0

    def test_backup_count_negative_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RotationConfig(backup_count=-1)

    def test_frozen(self) -> None:
        cfg = RotationConfig()
        with pytest.raises(ValidationError):
            cfg.max_bytes = 999  # type: ignore[misc]

    def test_factory(self) -> None:
        cfg = RotationConfigFactory.build()
        assert isinstance(cfg, RotationConfig)

    def test_compress_rotated_defaults_false(self) -> None:
        cfg = RotationConfig()
        assert cfg.compress_rotated is False

    def test_compress_rotated_true(self) -> None:
        cfg = RotationConfig(compress_rotated=True)
        assert cfg.compress_rotated is True

    def test_json_roundtrip(self) -> None:
        cfg = RotationConfig(
            strategy=RotationStrategy.EXTERNAL,
            max_bytes=1_000_000,
            backup_count=10,
        )
        restored = RotationConfig.model_validate_json(cfg.model_dump_json())
        assert restored == cfg

    def test_json_roundtrip_with_compression(self) -> None:
        cfg = RotationConfig(compress_rotated=True)
        restored = RotationConfig.model_validate_json(cfg.model_dump_json())
        assert restored.compress_rotated is True


# ── SinkConfig ─────────────────────────────────────────────────────


@pytest.mark.unit
class TestSinkConfig:
    """Tests for SinkConfig defaults, validation, and immutability."""

    def test_console_sink_defaults(self) -> None:
        cfg = SinkConfig(sink_type=SinkType.CONSOLE)
        assert cfg.level == LogLevel.INFO
        assert cfg.file_path is None
        assert cfg.rotation is None
        assert cfg.json_format is True

    def test_file_sink_valid(self) -> None:
        cfg = SinkConfig(
            sink_type=SinkType.FILE,
            file_path="app.log",
            rotation=RotationConfig(),
        )
        assert cfg.file_path == "app.log"
        assert cfg.rotation is not None

    def test_file_sink_requires_path(self) -> None:
        with pytest.raises(ValidationError, match="file_path is required"):
            SinkConfig(sink_type=SinkType.FILE)

    def test_file_sink_rejects_whitespace_path(self) -> None:
        with pytest.raises(ValidationError, match="empty or whitespace-only"):
            SinkConfig(sink_type=SinkType.FILE, file_path="   ")

    def test_file_sink_rejects_empty_path(self) -> None:
        with pytest.raises(ValidationError, match="empty or whitespace-only"):
            SinkConfig(sink_type=SinkType.FILE, file_path="")

    def test_file_sink_rejects_path_traversal(self) -> None:
        with pytest.raises(ValidationError, match=r"must not contain '\.\.'"):
            SinkConfig(
                sink_type=SinkType.FILE,
                file_path="../../../etc/passwd",
            )

    def test_file_sink_rejects_embedded_path_traversal(self) -> None:
        with pytest.raises(ValidationError, match=r"must not contain '\.\.'"):
            SinkConfig(
                sink_type=SinkType.FILE,
                file_path="logs/../../etc/shadow.log",
            )

    def test_console_sink_rejects_file_path(self) -> None:
        with pytest.raises(ValidationError, match="file_path must be None"):
            SinkConfig(
                sink_type=SinkType.CONSOLE,
                file_path="ignored.log",
            )

    def test_console_sink_rejects_rotation(self) -> None:
        with pytest.raises(ValidationError, match="rotation must be None"):
            SinkConfig(
                sink_type=SinkType.CONSOLE,
                rotation=RotationConfig(),
            )

    def test_file_sink_rejects_absolute_path(self) -> None:
        with pytest.raises(ValidationError, match="file_path must be relative"):
            SinkConfig(
                sink_type=SinkType.FILE,
                file_path="/etc/passwd",
            )

    def test_custom_level(self) -> None:
        cfg = SinkConfig(sink_type=SinkType.CONSOLE, level=LogLevel.ERROR)
        assert cfg.level == LogLevel.ERROR

    def test_frozen(self) -> None:
        cfg = SinkConfig(sink_type=SinkType.CONSOLE)
        with pytest.raises(ValidationError):
            cfg.level = LogLevel.DEBUG  # type: ignore[misc]

    def test_factory(self) -> None:
        cfg = SinkConfigFactory.build()
        assert isinstance(cfg, SinkConfig)

    def test_json_roundtrip(self) -> None:
        cfg = SinkConfig(
            sink_type=SinkType.FILE,
            level=LogLevel.DEBUG,
            file_path="test.log",
            rotation=RotationConfig(),
            json_format=True,
        )
        restored = SinkConfig.model_validate_json(cfg.model_dump_json())
        assert restored == cfg


# ── SinkConfig (Syslog) ──────────────────────────────────────────


@pytest.mark.unit
class TestSinkConfigSyslog:
    """Tests for SYSLOG sink configuration validation."""

    def test_valid_syslog_sink(self) -> None:
        cfg = SinkConfig(
            sink_type=SinkType.SYSLOG,
            syslog_host="loghost.example.com",
        )
        assert cfg.syslog_host == "loghost.example.com"
        assert cfg.syslog_port == 514
        assert cfg.syslog_facility == SyslogFacility.USER
        assert cfg.syslog_protocol == SyslogProtocol.UDP

    def test_custom_port_and_facility(self) -> None:
        cfg = SinkConfig(
            sink_type=SinkType.SYSLOG,
            syslog_host="10.0.0.1",
            syslog_port=1514,
            syslog_facility=SyslogFacility.LOCAL3,
            syslog_protocol=SyslogProtocol.TCP,
        )
        assert cfg.syslog_port == 1514
        assert cfg.syslog_facility == SyslogFacility.LOCAL3
        assert cfg.syslog_protocol == SyslogProtocol.TCP

    def test_syslog_requires_host(self) -> None:
        with pytest.raises(ValidationError, match="syslog_host is required"):
            SinkConfig(sink_type=SinkType.SYSLOG)

    def test_syslog_rejects_blank_host(self) -> None:
        with pytest.raises(ValidationError, match="syslog_host must not be blank"):
            SinkConfig(sink_type=SinkType.SYSLOG, syslog_host="   ")

    def test_syslog_rejects_file_path(self) -> None:
        with pytest.raises(ValidationError, match="file_path must be None for SYSLOG"):
            SinkConfig(
                sink_type=SinkType.SYSLOG,
                syslog_host="localhost",
                file_path="nope.log",
            )

    def test_syslog_rejects_rotation(self) -> None:
        with pytest.raises(ValidationError, match="rotation must be None for SYSLOG"):
            SinkConfig(
                sink_type=SinkType.SYSLOG,
                syslog_host="localhost",
                rotation=RotationConfig(),
            )

    def test_syslog_rejects_http_url(self) -> None:
        with pytest.raises(ValidationError, match="http_url must be None for SYSLOG"):
            SinkConfig(
                sink_type=SinkType.SYSLOG,
                syslog_host="localhost",
                http_url="http://example.com",
            )

    def test_syslog_port_bounds(self) -> None:
        with pytest.raises(ValidationError):
            SinkConfig(
                sink_type=SinkType.SYSLOG,
                syslog_host="localhost",
                syslog_port=0,
            )
        with pytest.raises(ValidationError):
            SinkConfig(
                sink_type=SinkType.SYSLOG,
                syslog_host="localhost",
                syslog_port=65536,
            )

    def test_custom_level(self) -> None:
        cfg = SinkConfig(
            sink_type=SinkType.SYSLOG,
            syslog_host="localhost",
            level=LogLevel.ERROR,
        )
        assert cfg.level == LogLevel.ERROR

    def test_frozen(self) -> None:
        cfg = SinkConfig(
            sink_type=SinkType.SYSLOG,
            syslog_host="localhost",
        )
        with pytest.raises(ValidationError):
            cfg.syslog_host = "other"  # type: ignore[misc]

    def test_json_roundtrip(self) -> None:
        cfg = SinkConfig(
            sink_type=SinkType.SYSLOG,
            syslog_host="syslog.local",
            syslog_port=1514,
            syslog_facility=SyslogFacility.LOCAL7,
            syslog_protocol=SyslogProtocol.TCP,
            level=LogLevel.WARNING,
        )
        restored = SinkConfig.model_validate_json(cfg.model_dump_json())
        assert restored == cfg


# ── SinkConfig (HTTP) ────────────────────────────────────────────


@pytest.mark.unit
class TestSinkConfigHttp:
    """Tests for HTTP sink configuration validation."""

    def test_valid_http_sink(self) -> None:
        cfg = SinkConfig(
            sink_type=SinkType.HTTP,
            http_url="https://logs.example.com/ingest",
        )
        assert cfg.http_url == "https://logs.example.com/ingest"
        assert cfg.http_headers == ()
        assert cfg.http_batch_size == 100
        assert cfg.http_flush_interval_seconds == 5.0
        assert cfg.http_timeout_seconds == 10.0
        assert cfg.http_max_retries == 3

    def test_custom_http_settings(self) -> None:
        cfg = SinkConfig(
            sink_type=SinkType.HTTP,
            http_url="http://logs.example.local:3100/api/v1/push",
            http_headers=(("Authorization", "Bearer test-token"),),
            http_batch_size=50,
            http_flush_interval_seconds=2.0,
            http_timeout_seconds=30.0,
            http_max_retries=5,
        )
        assert cfg.http_batch_size == 50
        assert cfg.http_flush_interval_seconds == 2.0
        assert cfg.http_timeout_seconds == 30.0
        assert cfg.http_max_retries == 5
        assert len(cfg.http_headers) == 1

    def test_http_requires_url(self) -> None:
        with pytest.raises(ValidationError, match="http_url is required"):
            SinkConfig(sink_type=SinkType.HTTP)

    def test_http_rejects_blank_url(self) -> None:
        with pytest.raises(ValidationError, match="http_url must not be blank"):
            SinkConfig(sink_type=SinkType.HTTP, http_url="   ")

    def test_http_rejects_non_http_scheme(self) -> None:
        with pytest.raises(ValidationError, match="http_url must start with"):
            SinkConfig(
                sink_type=SinkType.HTTP,
                http_url="ftp://example.com/logs",
            )

    def test_http_rejects_file_path(self) -> None:
        with pytest.raises(ValidationError, match="file_path must be None for HTTP"):
            SinkConfig(
                sink_type=SinkType.HTTP,
                http_url="https://example.com",
                file_path="nope.log",
            )

    def test_http_rejects_rotation(self) -> None:
        with pytest.raises(ValidationError, match="rotation must be None for HTTP"):
            SinkConfig(
                sink_type=SinkType.HTTP,
                http_url="https://example.com",
                rotation=RotationConfig(),
            )

    def test_http_rejects_syslog_host(self) -> None:
        with pytest.raises(ValidationError, match="syslog_host must be None for HTTP"):
            SinkConfig(
                sink_type=SinkType.HTTP,
                http_url="https://example.com",
                syslog_host="localhost",
            )

    def test_http_batch_size_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            SinkConfig(
                sink_type=SinkType.HTTP,
                http_url="https://example.com",
                http_batch_size=0,
            )

    def test_http_flush_interval_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            SinkConfig(
                sink_type=SinkType.HTTP,
                http_url="https://example.com",
                http_flush_interval_seconds=0.0,
            )

    def test_http_timeout_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            SinkConfig(
                sink_type=SinkType.HTTP,
                http_url="https://example.com",
                http_timeout_seconds=0.0,
            )

    def test_http_max_retries_zero_accepted(self) -> None:
        cfg = SinkConfig(
            sink_type=SinkType.HTTP,
            http_url="https://example.com",
            http_max_retries=0,
        )
        assert cfg.http_max_retries == 0

    def test_frozen(self) -> None:
        cfg = SinkConfig(
            sink_type=SinkType.HTTP,
            http_url="https://example.com",
        )
        with pytest.raises(ValidationError):
            cfg.http_url = "other"  # type: ignore[misc]

    def test_json_roundtrip(self) -> None:
        cfg = SinkConfig(
            sink_type=SinkType.HTTP,
            http_url="https://logs.example.com/ingest",
            http_headers=(("X-Source", "synthorg"),),
            http_batch_size=200,
            level=LogLevel.ERROR,
        )
        restored = SinkConfig.model_validate_json(cfg.model_dump_json())
        assert restored == cfg


# ── SinkConfig (Cross-type rejection) ────────────────────────────


@pytest.mark.unit
class TestSinkConfigCrossTypeRejection:
    """Ensure each sink type rejects fields belonging to other types."""

    def test_console_rejects_syslog_host(self) -> None:
        with pytest.raises(ValidationError, match="syslog_host must be None"):
            SinkConfig(
                sink_type=SinkType.CONSOLE,
                syslog_host="localhost",
            )

    def test_console_rejects_http_url(self) -> None:
        with pytest.raises(ValidationError, match="http_url must be None"):
            SinkConfig(
                sink_type=SinkType.CONSOLE,
                http_url="https://example.com",
            )

    def test_file_rejects_syslog_host(self) -> None:
        with pytest.raises(ValidationError, match="syslog_host must be None"):
            SinkConfig(
                sink_type=SinkType.FILE,
                file_path="app.log",
                syslog_host="localhost",
            )

    def test_file_rejects_http_url(self) -> None:
        with pytest.raises(ValidationError, match="http_url must be None"):
            SinkConfig(
                sink_type=SinkType.FILE,
                file_path="app.log",
                http_url="https://example.com",
            )


# ── LogConfig (endpoint uniqueness) ──────────────────────────────


@pytest.mark.unit
class TestLogConfigEndpointUniqueness:
    """Ensure LogConfig rejects duplicate syslog/HTTP endpoints."""

    def test_duplicate_syslog_endpoints_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Duplicate syslog endpoints"):
            LogConfig(
                sinks=(
                    _console_sink(),
                    SinkConfig(
                        sink_type=SinkType.SYSLOG,
                        syslog_host="loghost",
                        syslog_port=514,
                    ),
                    SinkConfig(
                        sink_type=SinkType.SYSLOG,
                        syslog_host="loghost",
                        syslog_port=514,
                    ),
                ),
            )

    def test_different_syslog_ports_accepted(self) -> None:
        cfg = LogConfig(
            sinks=(
                _console_sink(),
                SinkConfig(
                    sink_type=SinkType.SYSLOG,
                    syslog_host="loghost",
                    syslog_port=514,
                ),
                SinkConfig(
                    sink_type=SinkType.SYSLOG,
                    syslog_host="loghost",
                    syslog_port=1514,
                ),
            ),
        )
        assert len(cfg.sinks) == 3

    def test_duplicate_http_urls_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Duplicate HTTP URLs"):
            LogConfig(
                sinks=(
                    _console_sink(),
                    SinkConfig(
                        sink_type=SinkType.HTTP,
                        http_url="https://example.com/logs",
                    ),
                    SinkConfig(
                        sink_type=SinkType.HTTP,
                        http_url="https://example.com/logs",
                    ),
                ),
            )

    def test_different_http_urls_accepted(self) -> None:
        cfg = LogConfig(
            sinks=(
                _console_sink(),
                SinkConfig(
                    sink_type=SinkType.HTTP,
                    http_url="https://logs-a.example.com/push",
                ),
                SinkConfig(
                    sink_type=SinkType.HTTP,
                    http_url="https://logs-b.example.com/push",
                ),
            ),
        )
        assert len(cfg.sinks) == 3


# ── LogConfig ──────────────────────────────────────────────────────


def _console_sink() -> SinkConfig:
    return SinkConfig(sink_type=SinkType.CONSOLE, json_format=False)


@pytest.mark.unit
class TestLogConfig:
    """Tests for LogConfig defaults, validation, and immutability."""

    def test_defaults(self) -> None:
        cfg = LogConfig(sinks=(_console_sink(),))
        # Root defaults to INFO: DEBUG on root leaked verbose payloads
        # through HTTP sinks. Per-logger
        # overrides still force DEBUG on ``synthorg.engine`` /
        # ``synthorg.memory`` so agent traces stay detailed.
        assert cfg.root_level == LogLevel.INFO
        assert cfg.logger_levels == ()
        assert cfg.enable_correlation is True
        assert cfg.log_dir == "logs"

    def test_custom_values(self) -> None:
        cfg = LogConfig(
            root_level=LogLevel.WARNING,
            logger_levels=(("synthorg.engine", LogLevel.DEBUG),),
            sinks=(_console_sink(),),
            enable_correlation=False,
            log_dir="custom_logs",
        )
        assert cfg.root_level == LogLevel.WARNING
        assert len(cfg.logger_levels) == 1
        assert cfg.enable_correlation is False
        assert cfg.log_dir == "custom_logs"

    def test_empty_sinks_rejected(self) -> None:
        with pytest.raises(ValidationError, match="At least one sink"):
            LogConfig(sinks=())

    def test_duplicate_logger_names_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Duplicate logger names"):
            LogConfig(
                sinks=(_console_sink(),),
                logger_levels=(
                    ("synthorg.engine", LogLevel.DEBUG),
                    ("synthorg.engine", LogLevel.INFO),
                ),
            )

    def test_duplicate_file_paths_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Duplicate file paths"):
            LogConfig(
                sinks=(
                    SinkConfig(
                        sink_type=SinkType.FILE,
                        file_path="same.log",
                        rotation=RotationConfig(),
                    ),
                    SinkConfig(
                        sink_type=SinkType.FILE,
                        file_path="same.log",
                        rotation=RotationConfig(),
                    ),
                ),
            )

    def test_blank_log_dir_rejected(self) -> None:
        with pytest.raises(ValidationError, match="whitespace-only"):
            LogConfig(sinks=(_console_sink(),), log_dir="   ")

    def test_log_dir_traversal_rejected(self) -> None:
        with pytest.raises(
            ValidationError, match=r"must not contain '\.\.' components"
        ):
            LogConfig(sinks=(_console_sink(),), log_dir="../../../tmp")

    @pytest.mark.parametrize(
        "absolute_dir",
        ["/var/log", "/opt/app/logs", "C:\\Logs"],
    )
    def test_absolute_log_dir_accepted(self, absolute_dir: str) -> None:
        cfg = LogConfig(sinks=(_console_sink(),), log_dir=absolute_dir)
        assert cfg.log_dir == absolute_dir

    def test_frozen(self) -> None:
        cfg = LogConfig(sinks=(_console_sink(),))
        with pytest.raises(ValidationError):
            cfg.log_dir = "other"  # type: ignore[misc]

    def test_factory(self) -> None:
        cfg = LogConfigFactory.build()
        assert isinstance(cfg, LogConfig)

    def test_json_roundtrip(self) -> None:
        cfg = LogConfig(
            sinks=(_console_sink(),),
            logger_levels=(("synthorg.core", LogLevel.WARNING),),
        )
        restored = LogConfig.model_validate_json(cfg.model_dump_json())
        assert restored == cfg


# ── DEFAULT_SINKS ──────────────────────────────────────────────────


@pytest.mark.unit
class TestDefaultSinks:
    """Tests for the DEFAULT_SINKS constant."""

    def test_count(self) -> None:
        assert len(DEFAULT_SINKS) == 11

    def test_first_is_console(self) -> None:
        assert DEFAULT_SINKS[0].sink_type == SinkType.CONSOLE
        assert DEFAULT_SINKS[0].json_format is False

    def test_file_sinks_have_paths(self) -> None:
        for sink in DEFAULT_SINKS[1:]:
            assert sink.sink_type == SinkType.FILE
            assert sink.file_path is not None
            assert sink.rotation is not None

    def test_no_duplicate_file_paths(self) -> None:
        paths = [s.file_path for s in DEFAULT_SINKS if s.file_path is not None]
        assert len(paths) == len(set(paths))

    def test_valid_as_log_config(self) -> None:
        cfg = LogConfig(sinks=DEFAULT_SINKS)
        assert len(cfg.sinks) == 11


# -- SinkConfig (Prometheus) -----------------------------------------


@pytest.mark.unit
class TestSinkConfigPrometheus:
    """Tests for PROMETHEUS sink configuration validation."""

    def test_valid_prometheus_sink_defaults(self) -> None:
        cfg = SinkConfig(sink_type=SinkType.PROMETHEUS)
        assert cfg.sink_type == SinkType.PROMETHEUS

    def test_prometheus_rejects_file_path(self) -> None:
        with pytest.raises(ValidationError, match="file_path must be None"):
            SinkConfig(
                sink_type=SinkType.PROMETHEUS,
                file_path="nope.log",
            )

    def test_prometheus_rejects_syslog_host(self) -> None:
        with pytest.raises(ValidationError, match="syslog_host must be None"):
            SinkConfig(
                sink_type=SinkType.PROMETHEUS,
                syslog_host="localhost",
            )

    def test_prometheus_rejects_http_url(self) -> None:
        with pytest.raises(ValidationError, match="http_url must be None"):
            SinkConfig(
                sink_type=SinkType.PROMETHEUS,
                http_url="https://example.com",
            )

    def test_prometheus_rejects_otlp_endpoint(self) -> None:
        with pytest.raises(ValidationError, match="otlp_endpoint must be None"):
            SinkConfig(
                sink_type=SinkType.PROMETHEUS,
                otlp_endpoint="http://localhost:4318",
            )

    def test_frozen(self) -> None:
        cfg = SinkConfig(sink_type=SinkType.PROMETHEUS)
        with pytest.raises(ValidationError):
            cfg.sink_type = SinkType.CONSOLE  # type: ignore[misc]

    def test_json_roundtrip(self) -> None:
        cfg = SinkConfig(
            sink_type=SinkType.PROMETHEUS,
            level=LogLevel.INFO,
        )
        restored = SinkConfig.model_validate_json(cfg.model_dump_json())
        assert restored == cfg


# -- SinkConfig (OTLP) ----------------------------------------------


@pytest.mark.unit
class TestSinkConfigOtlp:
    """Tests for OTLP sink configuration validation."""

    def test_valid_otlp_sink(self) -> None:
        cfg = SinkConfig(
            sink_type=SinkType.OTLP,
            otlp_endpoint="http://localhost:4318",
        )
        assert cfg.otlp_endpoint == "http://localhost:4318"
        assert cfg.otlp_protocol == OtlpProtocol.HTTP_JSON
        assert cfg.otlp_headers == ()
        assert cfg.otlp_export_interval_seconds == 5.0
        assert cfg.otlp_batch_size == 100
        assert cfg.otlp_timeout_seconds == 10.0

    def test_custom_otlp_settings(self) -> None:
        cfg = SinkConfig(
            sink_type=SinkType.OTLP,
            otlp_endpoint="https://otel-collector.example.com:4318",
            otlp_headers=(("Authorization", "Bearer test-token"),),
            otlp_export_interval_seconds=10.0,
        )
        assert cfg.otlp_protocol == OtlpProtocol.HTTP_JSON
        assert len(cfg.otlp_headers) == 1
        assert cfg.otlp_export_interval_seconds == 10.0

    def test_otlp_grpc_rejected_at_config_time(self) -> None:
        with pytest.raises(ValidationError, match="gRPC transport is not supported"):
            SinkConfig(
                sink_type=SinkType.OTLP,
                otlp_endpoint="http://localhost:4317",
                otlp_protocol=OtlpProtocol.GRPC,
            )

    def test_otlp_requires_endpoint(self) -> None:
        with pytest.raises(ValidationError, match="otlp_endpoint is required"):
            SinkConfig(sink_type=SinkType.OTLP)

    def test_otlp_rejects_blank_endpoint(self) -> None:
        with pytest.raises(ValidationError, match="otlp_endpoint must not be blank"):
            SinkConfig(sink_type=SinkType.OTLP, otlp_endpoint="   ")

    def test_otlp_rejects_non_http_scheme(self) -> None:
        with pytest.raises(ValidationError, match="otlp_endpoint must start with"):
            SinkConfig(
                sink_type=SinkType.OTLP,
                otlp_endpoint="grpc://localhost:4317",
            )

    def test_otlp_rejects_file_path(self) -> None:
        with pytest.raises(ValidationError, match="file_path must be None"):
            SinkConfig(
                sink_type=SinkType.OTLP,
                otlp_endpoint="http://localhost:4318",
                file_path="nope.log",
            )

    def test_otlp_rejects_syslog_host(self) -> None:
        with pytest.raises(ValidationError, match="syslog_host must be None"):
            SinkConfig(
                sink_type=SinkType.OTLP,
                otlp_endpoint="http://localhost:4318",
                syslog_host="localhost",
            )

    def test_otlp_rejects_http_url(self) -> None:
        with pytest.raises(ValidationError, match="http_url must be None"):
            SinkConfig(
                sink_type=SinkType.OTLP,
                otlp_endpoint="http://localhost:4318",
                http_url="https://example.com",
            )

    def test_otlp_rejects_empty_header_name(self) -> None:
        with pytest.raises(ValidationError, match="empty header name"):
            SinkConfig(
                sink_type=SinkType.OTLP,
                otlp_endpoint="http://localhost:4318",
                otlp_headers=(("", "value"),),
            )

    def test_otlp_export_interval_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            SinkConfig(
                sink_type=SinkType.OTLP,
                otlp_endpoint="http://localhost:4318",
                otlp_export_interval_seconds=0.0,
            )

    def test_frozen(self) -> None:
        cfg = SinkConfig(
            sink_type=SinkType.OTLP,
            otlp_endpoint="http://localhost:4318",
        )
        with pytest.raises(ValidationError):
            cfg.otlp_endpoint = "other"  # type: ignore[misc]

    def test_custom_batch_size_and_timeout(self) -> None:
        cfg = SinkConfig(
            sink_type=SinkType.OTLP,
            otlp_endpoint="http://localhost:4318",
            otlp_batch_size=50,
            otlp_timeout_seconds=30.0,
        )
        assert cfg.otlp_batch_size == 50
        assert cfg.otlp_timeout_seconds == 30.0

    def test_batch_size_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            SinkConfig(
                sink_type=SinkType.OTLP,
                otlp_endpoint="http://localhost:4318",
                otlp_batch_size=0,
            )

    def test_timeout_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            SinkConfig(
                sink_type=SinkType.OTLP,
                otlp_endpoint="http://localhost:4318",
                otlp_timeout_seconds=0.0,
            )

    def test_json_roundtrip(self) -> None:
        cfg = SinkConfig(
            sink_type=SinkType.OTLP,
            otlp_endpoint="https://otel.example.com:4318",
            otlp_headers=(("X-Source", "synthorg"),),
            otlp_export_interval_seconds=15.0,
            otlp_batch_size=200,
            otlp_timeout_seconds=20.0,
            level=LogLevel.WARNING,
        )
        restored = SinkConfig.model_validate_json(cfg.model_dump_json())
        assert restored == cfg


# -- Cross-type rejection (Prometheus/OTLP) --------------------------


@pytest.mark.unit
class TestSinkConfigCrossTypeRejectionNewTypes:
    """Ensure Prometheus/OTLP fields are rejected by other sink types."""

    def test_console_rejects_otlp_endpoint(self) -> None:
        with pytest.raises(ValidationError, match="otlp_endpoint must be None"):
            SinkConfig(
                sink_type=SinkType.CONSOLE,
                otlp_endpoint="http://localhost:4318",
            )

    def test_file_rejects_otlp_endpoint(self) -> None:
        with pytest.raises(ValidationError, match="otlp_endpoint must be None"):
            SinkConfig(
                sink_type=SinkType.FILE,
                file_path="app.log",
                otlp_endpoint="http://localhost:4318",
            )

    def test_syslog_rejects_otlp_endpoint(self) -> None:
        with pytest.raises(ValidationError, match="otlp_endpoint must be None"):
            SinkConfig(
                sink_type=SinkType.SYSLOG,
                syslog_host="localhost",
                otlp_endpoint="http://localhost:4318",
            )


# ── ContainerLogShippingConfig ────────────────────────────────────


@pytest.mark.unit
class TestContainerLogShippingConfig:
    """Tests for ContainerLogShippingConfig defaults and validation."""

    def test_defaults(self) -> None:
        cfg = ContainerLogShippingConfig()
        assert cfg.enabled is True
        assert cfg.ship_raw_logs is False
        assert cfg.collection_timeout_seconds == 5.0
        assert cfg.max_log_bytes == 10 * 1024 * 1024

    def test_frozen(self) -> None:
        cfg = ContainerLogShippingConfig()
        with pytest.raises(ValidationError):
            cfg.enabled = False  # type: ignore[misc]

    def test_disabled(self) -> None:
        cfg = ContainerLogShippingConfig(enabled=False)
        assert cfg.enabled is False

    def test_custom_timeout(self) -> None:
        cfg = ContainerLogShippingConfig(collection_timeout_seconds=15.0)
        assert cfg.collection_timeout_seconds == 15.0

    def test_timeout_too_low(self) -> None:
        with pytest.raises(ValidationError, match="collection_timeout"):
            ContainerLogShippingConfig(
                collection_timeout_seconds=0.01,
            )

    def test_timeout_too_high(self) -> None:
        with pytest.raises(ValidationError, match="collection_timeout"):
            ContainerLogShippingConfig(
                collection_timeout_seconds=60.0,
            )

    def test_max_log_bytes_must_be_positive(self) -> None:
        with pytest.raises(ValidationError, match="max_log_bytes"):
            ContainerLogShippingConfig(max_log_bytes=0)

    def test_custom_max_log_bytes(self) -> None:
        cfg = ContainerLogShippingConfig(max_log_bytes=5_000_000)
        assert cfg.max_log_bytes == 5_000_000

    def test_rejects_nan(self) -> None:
        with pytest.raises(ValidationError):
            ContainerLogShippingConfig(
                collection_timeout_seconds=float("nan"),
            )

    def test_ship_raw_logs_default_false(self) -> None:
        cfg = ContainerLogShippingConfig()
        assert cfg.ship_raw_logs is False

    def test_ship_raw_logs_opt_in(self) -> None:
        cfg = ContainerLogShippingConfig(ship_raw_logs=True)
        assert cfg.ship_raw_logs is True

    def test_timeout_boundary_min(self) -> None:
        cfg = ContainerLogShippingConfig(collection_timeout_seconds=0.1)
        assert cfg.collection_timeout_seconds == 0.1

    def test_timeout_boundary_max(self) -> None:
        cfg = ContainerLogShippingConfig(collection_timeout_seconds=30.0)
        assert cfg.collection_timeout_seconds == 30.0


@pytest.mark.unit
class TestLogConfigContainerLogShipping:
    """LogConfig includes container_log_shipping field."""

    def test_default_container_log_shipping(self) -> None:
        cfg = LogConfig(sinks=DEFAULT_SINKS)
        assert cfg.container_log_shipping.enabled is True
        assert cfg.container_log_shipping.collection_timeout_seconds == 5.0

    def test_custom_container_log_shipping(self) -> None:
        cfg = LogConfig(
            sinks=DEFAULT_SINKS,
            container_log_shipping=ContainerLogShippingConfig(
                enabled=False,
                collection_timeout_seconds=10.0,
            ),
        )
        assert cfg.container_log_shipping.enabled is False
        assert cfg.container_log_shipping.collection_timeout_seconds == 10.0
