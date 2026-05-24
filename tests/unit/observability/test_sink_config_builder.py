"""Tests for sink configuration builder (runtime sink overrides + custom sinks)."""

import json

import pytest

from synthorg.observability.config import DEFAULT_SINKS
from synthorg.observability.enums import LogLevel, RotationStrategy, SinkType
from synthorg.observability.sink_config_builder import (
    SinkBuildResult,
    build_log_config_from_settings,
)

_DEFAULTS_COUNT = len(DEFAULT_SINKS)


def _build(
    *,
    overrides: str = "{}",
    custom: str = "[]",
    root_level: LogLevel = LogLevel.DEBUG,
    enable_correlation: bool = True,
) -> SinkBuildResult:
    """Shorthand builder with sensible defaults."""
    return build_log_config_from_settings(
        root_level=root_level,
        enable_correlation=enable_correlation,
        sink_overrides_json=overrides,
        custom_sinks_json=custom,
    )


# ── Empty / default behaviour ────────────────────────────────────


@pytest.mark.unit
class TestEmptyOverrides:
    """Empty overrides and custom sinks produce unmodified DEFAULT_SINKS."""

    def test_empty_overrides_returns_all_default_sinks(self) -> None:
        result = _build()
        assert len(result.config.sinks) == _DEFAULTS_COUNT

    def test_default_sink_levels_preserved(self) -> None:
        result = _build()
        levels = {s.file_path or "__console__": s.level for s in result.config.sinks}
        assert levels["__console__"] == LogLevel.INFO
        assert levels["errors.log"] == LogLevel.ERROR
        assert levels["debug.log"] == LogLevel.DEBUG

    def test_root_level_propagated(self) -> None:
        result = _build(root_level=LogLevel.WARNING)
        assert result.config.root_level == LogLevel.WARNING

    def test_enable_correlation_propagated(self) -> None:
        result = _build(enable_correlation=False)
        assert result.config.enable_correlation is False

    def test_routing_overrides_empty(self) -> None:
        result = _build()
        assert result.routing_overrides == {}


# ── Disable file sinks ───────────────────────────────────────────


@pytest.mark.unit
class TestDisableSinks:
    """Disabling sinks via sink_overrides."""

    def test_disable_file_sink_removes_it(self) -> None:
        overrides = json.dumps({"synthorg.log": {"enabled": False}})
        result = _build(overrides=overrides)
        paths = {s.file_path for s in result.config.sinks if s.file_path}
        assert "synthorg.log" not in paths
        assert len(result.config.sinks) == _DEFAULTS_COUNT - 1

    def test_disable_console_raises(self) -> None:
        overrides = json.dumps({"__console__": {"enabled": False}})
        with pytest.raises(ValueError, match=r"[Cc]onsole"):
            _build(overrides=overrides)

    def test_disable_all_file_sinks_leaves_console(self) -> None:
        disabled = {
            s.file_path: {"enabled": False}
            for s in DEFAULT_SINKS
            if s.sink_type == SinkType.FILE
        }
        result = _build(overrides=json.dumps(disabled))
        assert len(result.config.sinks) == 1
        assert result.config.sinks[0].sink_type == SinkType.CONSOLE

    def test_disable_multiple_sinks(self) -> None:
        overrides = json.dumps(
            {
                "audit.log": {"enabled": False},
                "debug.log": {"enabled": False},
            }
        )
        result = _build(overrides=overrides)
        paths = {s.file_path for s in result.config.sinks if s.file_path}
        assert "audit.log" not in paths
        assert "debug.log" not in paths
        assert len(result.config.sinks) == _DEFAULTS_COUNT - 2


# ── Level overrides ──────────────────────────────────────────────


@pytest.mark.unit
class TestLevelOverrides:
    """Per-sink log level overrides."""

    def test_level_override_applied(self) -> None:
        overrides = json.dumps({"errors.log": {"level": "debug"}})
        result = _build(overrides=overrides)
        errors_sink = next(
            s for s in result.config.sinks if s.file_path == "errors.log"
        )
        assert errors_sink.level == LogLevel.DEBUG

    def test_console_level_override_applied(self) -> None:
        overrides = json.dumps({"__console__": {"level": "error"}})
        result = _build(overrides=overrides)
        console = next(
            s for s in result.config.sinks if s.sink_type == SinkType.CONSOLE
        )
        assert console.level == LogLevel.ERROR

    def test_invalid_level_raises(self) -> None:
        overrides = json.dumps({"audit.log": {"level": "nonexistent"}})
        with pytest.raises(ValueError, match=r"[Ll]evel"):
            _build(overrides=overrides)


# ── JSON format overrides ────────────────────────────────────────


@pytest.mark.unit
class TestJsonFormatOverrides:
    """Per-sink JSON format toggle."""

    def test_json_format_override_applied(self) -> None:
        overrides = json.dumps({"__console__": {"json_format": True}})
        result = _build(overrides=overrides)
        console = next(
            s for s in result.config.sinks if s.sink_type == SinkType.CONSOLE
        )
        assert console.json_format is True

    def test_disable_json_on_file_sink(self) -> None:
        overrides = json.dumps({"synthorg.log": {"json_format": False}})
        result = _build(overrides=overrides)
        main_sink = next(
            s for s in result.config.sinks if s.file_path == "synthorg.log"
        )
        assert main_sink.json_format is False


# ── Rotation overrides ───────────────────────────────────────────


@pytest.mark.unit
class TestRotationOverrides:
    """Per-sink rotation configuration overrides."""

    def test_rotation_max_bytes_override(self) -> None:
        overrides = json.dumps(
            {
                "audit.log": {"rotation": {"max_bytes": 20_971_520}},
            }
        )
        result = _build(overrides=overrides)
        audit = next(s for s in result.config.sinks if s.file_path == "audit.log")
        assert audit.rotation is not None
        assert audit.rotation.max_bytes == 20_971_520
        # backup_count preserved from default
        assert audit.rotation.backup_count == 5

    def test_rotation_backup_count_override(self) -> None:
        overrides = json.dumps(
            {
                "synthorg.log": {"rotation": {"backup_count": 10}},
            }
        )
        result = _build(overrides=overrides)
        main = next(s for s in result.config.sinks if s.file_path == "synthorg.log")
        assert main.rotation is not None
        assert main.rotation.backup_count == 10

    def test_rotation_strategy_override(self) -> None:
        overrides = json.dumps(
            {
                "debug.log": {"rotation": {"strategy": "external"}},
            }
        )
        result = _build(overrides=overrides)
        debug = next(s for s in result.config.sinks if s.file_path == "debug.log")
        assert debug.rotation is not None
        assert debug.rotation.strategy == RotationStrategy.EXTERNAL

    def test_invalid_rotation_strategy_raises(self) -> None:
        overrides = json.dumps({"audit.log": {"rotation": {"strategy": "daily"}}})
        with pytest.raises(ValueError, match=r"[Ss]trategy"):
            _build(overrides=overrides)


# ── Validation ───────────────────────────────────────────────────


@pytest.mark.unit
class TestOverrideValidation:
    """Validation of sink_overrides JSON structure."""

    def test_unknown_sink_identifier_raises(self) -> None:
        overrides = json.dumps({"nonexistent.log": {"level": "info"}})
        with pytest.raises(ValueError, match=r"nonexistent\.log"):
            _build(overrides=overrides)

    def test_invalid_json_raises(self) -> None:
        with pytest.raises(ValueError, match=r"[Jj]SON"):
            _build(overrides="not-json")

    def test_non_object_top_level_raises(self) -> None:
        with pytest.raises(ValueError, match=r"(?i)object"):
            _build(overrides="[]")

    def test_override_value_must_be_object(self) -> None:
        overrides = json.dumps({"audit.log": "not-an-object"})
        with pytest.raises(ValueError, match=r"(?i)object"):
            _build(overrides=overrides)


# ── Custom sinks ─────────────────────────────────────────────────


@pytest.mark.unit
class TestCustomSinks:
    """Adding custom file sinks via custom_sinks JSON."""

    def test_custom_sink_added(self) -> None:
        custom = json.dumps([{"file_path": "my_custom.log"}])
        result = _build(custom=custom)
        assert len(result.config.sinks) == _DEFAULTS_COUNT + 1
        custom_sink = next(
            s for s in result.config.sinks if s.file_path == "my_custom.log"
        )
        assert custom_sink.sink_type == SinkType.FILE
        assert custom_sink.level == LogLevel.INFO
        assert custom_sink.json_format is True
        assert custom_sink.rotation is not None

    def test_custom_sink_with_level(self) -> None:
        custom = json.dumps(
            [
                {
                    "file_path": "debug_custom.log",
                    "level": "debug",
                }
            ]
        )
        result = _build(custom=custom)
        sink = next(s for s in result.config.sinks if s.file_path == "debug_custom.log")
        assert sink.level == LogLevel.DEBUG

    def test_custom_sink_with_routing_prefixes(self) -> None:
        custom = json.dumps(
            [
                {
                    "file_path": "custom_routed.log",
                    "routing_prefixes": ["synthorg.tools.", "synthorg.memory."],
                }
            ]
        )
        result = _build(custom=custom)
        assert "custom_routed.log" in result.routing_overrides
        assert result.routing_overrides["custom_routed.log"] == (
            "synthorg.tools.",
            "synthorg.memory.",
        )

    def test_custom_sink_duplicate_path_with_default_raises(self) -> None:
        custom = json.dumps([{"file_path": "audit.log"}])
        with pytest.raises(ValueError, match=r"audit\.log"):
            _build(custom=custom)

    def test_custom_sink_duplicate_path_between_custom_raises(self) -> None:
        custom = json.dumps(
            [
                {"file_path": "dup.log"},
                {"file_path": "dup.log"},
            ]
        )
        with pytest.raises(ValueError, match=r"dup\.log"):
            _build(custom=custom)

    def test_custom_sink_path_traversal_raises(self) -> None:
        custom = json.dumps([{"file_path": "../evil.log"}])
        with pytest.raises(ValueError, match="\\.\\."):
            _build(custom=custom)

    def test_custom_sink_absolute_path_raises(self) -> None:
        custom = json.dumps([{"file_path": "/var/log/evil.log"}])
        with pytest.raises(ValueError, match=r"[Rr]elative|[Aa]bsolute"):
            _build(custom=custom)

    def test_custom_sink_missing_file_path_raises(self) -> None:
        custom = json.dumps([{"level": "info"}])
        with pytest.raises(ValueError, match="file_path"):
            _build(custom=custom)

    def test_custom_sink_empty_routing_prefixes_ignored(self) -> None:
        custom = json.dumps(
            [
                {
                    "file_path": "no_route.log",
                    "routing_prefixes": [],
                }
            ]
        )
        result = _build(custom=custom)
        assert "no_route.log" not in result.routing_overrides


# ── Custom sinks JSON validation ─────────────────────────────────


@pytest.mark.unit
class TestCustomSinksValidation:
    """Validation of custom_sinks JSON structure."""

    def test_invalid_json_raises(self) -> None:
        with pytest.raises(ValueError, match=r"[Jj]SON"):
            _build(custom="not-json")

    def test_non_array_top_level_raises(self) -> None:
        with pytest.raises(ValueError, match=r"[Aa]rray"):
            _build(custom="{}")

    def test_non_object_entry_raises(self) -> None:
        with pytest.raises(ValueError, match=r"(?i)object"):
            _build(custom='["not-an-object"]')

    def test_invalid_routing_prefix_raises(self) -> None:
        custom = json.dumps(
            [
                {
                    "file_path": "bad_route.log",
                    "routing_prefixes": [""],
                }
            ]
        )
        with pytest.raises(ValueError, match=r"[Pp]refix"):
            _build(custom=custom)

    def test_routing_prefixes_non_array_raises(self) -> None:
        custom = json.dumps(
            [{"file_path": "x.log", "routing_prefixes": "not-an-array"}]
        )
        with pytest.raises(ValueError, match=r"[Aa]rray"):
            _build(custom=custom)

    def test_non_string_file_path_raises(self) -> None:
        custom = json.dumps([{"file_path": 123}])
        with pytest.raises(ValueError, match=r"non-empty string"):
            _build(custom=custom)

    def test_null_file_path_raises(self) -> None:
        custom = json.dumps([{"file_path": None}])
        with pytest.raises(ValueError, match=r"non-empty string"):
            _build(custom=custom)


# ── Combined overrides + custom ──────────────────────────────────


@pytest.mark.unit
class TestCombined:
    """Overrides and custom sinks combined."""

    def test_disable_sink_and_add_custom(self) -> None:
        overrides = json.dumps({"debug.log": {"enabled": False}})
        custom = json.dumps([{"file_path": "my_debug.log", "level": "debug"}])
        result = _build(overrides=overrides, custom=custom)
        paths = {s.file_path for s in result.config.sinks if s.file_path}
        assert "debug.log" not in paths
        assert "my_debug.log" in paths
        # Same count: removed one, added one
        assert len(result.config.sinks) == _DEFAULTS_COUNT

    def test_custom_sink_cannot_reuse_disabled_default_path(self) -> None:
        """Even if a default sink is disabled, its path is reserved."""
        overrides = json.dumps({"audit.log": {"enabled": False}})
        custom = json.dumps([{"file_path": "audit.log"}])
        with pytest.raises(ValueError, match=r"audit\.log"):
            _build(overrides=overrides, custom=custom)

    def test_multiple_fields_overridden_simultaneously(self) -> None:
        overrides = json.dumps(
            {
                "synthorg.log": {
                    "level": "warning",
                    "json_format": False,
                    "rotation": {"max_bytes": 20_000_000},
                },
            }
        )
        result = _build(overrides=overrides)
        sink = next(s for s in result.config.sinks if s.file_path == "synthorg.log")
        assert sink.level == LogLevel.WARNING
        assert sink.json_format is False
        assert sink.rotation is not None
        assert sink.rotation.max_bytes == 20_000_000

    def test_custom_sink_with_rotation_override(self) -> None:
        custom = json.dumps(
            [
                {
                    "file_path": "large.log",
                    "rotation": {"max_bytes": 50_000_000, "backup_count": 3},
                }
            ]
        )
        result = _build(custom=custom)
        sink = next(s for s in result.config.sinks if s.file_path == "large.log")
        assert sink.rotation is not None
        assert sink.rotation.max_bytes == 50_000_000
        assert sink.rotation.backup_count == 3

    def test_custom_log_dir_propagated(self) -> None:
        result = build_log_config_from_settings(
            root_level=LogLevel.DEBUG,
            enable_correlation=True,
            sink_overrides_json="{}",
            custom_sinks_json="[]",
            log_dir="custom_logs",
        )
        assert result.config.log_dir == "custom_logs"


# -- Strict type validation ----------------------------------------


@pytest.mark.unit
class TestStrictTypeValidation:
    """Strict type checks for boolean and level fields."""

    def test_enabled_string_false_raises(self) -> None:
        overrides = json.dumps({"audit.log": {"enabled": "false"}})
        with pytest.raises(ValueError, match=r"boolean"):
            _build(overrides=overrides)

    def test_enabled_int_zero_raises(self) -> None:
        overrides = json.dumps({"audit.log": {"enabled": 0}})
        with pytest.raises(ValueError, match=r"boolean"):
            _build(overrides=overrides)

    def test_json_format_string_false_raises(self) -> None:
        overrides = json.dumps({"synthorg.log": {"json_format": "false"}})
        with pytest.raises(ValueError, match=r"boolean"):
            _build(overrides=overrides)

    def test_custom_sink_json_format_string_raises(self) -> None:
        custom = json.dumps([{"file_path": "x.log", "json_format": "true"}])
        with pytest.raises(ValueError, match=r"boolean"):
            _build(custom=custom)

    def test_level_null_raises(self) -> None:
        overrides = json.dumps({"audit.log": {"level": None}})
        with pytest.raises(ValueError, match=r"string"):
            _build(overrides=overrides)

    def test_level_number_raises(self) -> None:
        overrides = json.dumps({"audit.log": {"level": 42}})
        with pytest.raises(ValueError, match=r"string"):
            _build(overrides=overrides)

    def test_rotation_non_object_raises(self) -> None:
        overrides = json.dumps({"audit.log": {"rotation": "disabled"}})
        with pytest.raises(ValueError, match=r"(?i)object"):
            _build(overrides=overrides)

    def test_rotation_array_raises(self) -> None:
        overrides = json.dumps({"audit.log": {"rotation": []}})
        with pytest.raises(ValueError, match=r"(?i)object"):
            _build(overrides=overrides)

    def test_invalid_max_bytes_raises(self) -> None:
        overrides = json.dumps(
            {"audit.log": {"rotation": {"max_bytes": "not-a-number"}}},
        )
        with pytest.raises(ValueError, match=r"max_bytes"):
            _build(overrides=overrides)

    def test_invalid_backup_count_raises(self) -> None:
        overrides = json.dumps(
            {"audit.log": {"rotation": {"backup_count": None}}},
        )
        with pytest.raises(ValueError, match=r"backup_count"):
            _build(overrides=overrides)


# -- Unknown field rejection ---------------------------------------


@pytest.mark.unit
class TestUnknownFieldRejection:
    """Unknown fields in override/custom sink dicts are rejected."""

    def test_unknown_override_field_raises(self) -> None:
        overrides = json.dumps({"audit.log": {"unknown_field_name": "debug"}})
        with pytest.raises(ValueError, match=r"Unknown fields"):
            _build(overrides=overrides)

    def test_unknown_custom_sink_field_raises(self) -> None:
        custom = json.dumps([{"file_path": "x.log", "routing_prefix": ["a."]}])
        with pytest.raises(ValueError, match=r"Unknown fields"):
            _build(custom=custom)

    def test_unknown_rotation_field_raises(self) -> None:
        overrides = json.dumps(
            {"audit.log": {"rotation": {"max_size": 1000}}},
        )
        with pytest.raises(ValueError, match=r"Unknown fields"):
            _build(overrides=overrides)


# -- Limits --------------------------------------------------------


@pytest.mark.unit
class TestLimits:
    """Caps on custom sinks and routing prefixes."""

    def test_too_many_custom_sinks_raises(self) -> None:
        sinks = [{"file_path": f"sink_{i}.log"} for i in range(21)]
        with pytest.raises(ValueError, match=r"exceeds maximum"):
            _build(custom=json.dumps(sinks))

    def test_too_many_routing_prefixes_raises(self) -> None:
        prefixes = [f"synthorg.mod{i}." for i in range(51)]
        custom = json.dumps(
            [{"file_path": "x.log", "routing_prefixes": prefixes}],
        )
        with pytest.raises(ValueError, match=r"exceeds maximum"):
            _build(custom=custom)


# -- Custom SYSLOG sinks ------------------------------------------


@pytest.mark.unit
class TestCustomSyslogSinks:
    """Custom sinks with sink_type='syslog'."""

    def test_syslog_sink_created(self) -> None:
        custom = json.dumps(
            [
                {
                    "sink_type": "syslog",
                    "syslog_host": "loghost.example.com",
                }
            ]
        )
        result = _build(custom=custom)
        assert len(result.config.sinks) == _DEFAULTS_COUNT + 1
        syslog_sink = result.config.sinks[-1]
        assert syslog_sink.sink_type == SinkType.SYSLOG
        assert syslog_sink.syslog_host == "loghost.example.com"
        assert syslog_sink.syslog_port == 514

    def test_syslog_custom_settings(self) -> None:
        custom = json.dumps(
            [
                {
                    "sink_type": "syslog",
                    "syslog_host": "10.0.0.1",
                    "syslog_port": 1514,
                    "syslog_facility": "local3",
                    "syslog_protocol": "tcp",
                    "level": "error",
                }
            ]
        )
        result = _build(custom=custom)
        syslog_sink = result.config.sinks[-1]
        assert syslog_sink.syslog_port == 1514
        assert syslog_sink.syslog_facility.value == "local3"
        assert syslog_sink.syslog_protocol.value == "tcp"
        assert syslog_sink.level == LogLevel.ERROR

    def test_syslog_requires_host(self) -> None:
        custom = json.dumps([{"sink_type": "syslog"}])
        with pytest.raises(ValueError, match="syslog_host"):
            _build(custom=custom)

    def test_syslog_no_routing(self) -> None:
        custom = json.dumps(
            [
                {
                    "sink_type": "syslog",
                    "syslog_host": "localhost",
                }
            ]
        )
        result = _build(custom=custom)
        assert len(result.routing_overrides) == 0

    def test_syslog_unknown_field_rejected(self) -> None:
        custom = json.dumps(
            [
                {
                    "sink_type": "syslog",
                    "syslog_host": "localhost",
                    "bad_field": True,
                }
            ]
        )
        with pytest.raises(ValueError, match="Unknown fields"):
            _build(custom=custom)

    def test_syslog_invalid_facility_rejected(self) -> None:
        custom = json.dumps(
            [
                {
                    "sink_type": "syslog",
                    "syslog_host": "localhost",
                    "syslog_facility": "invalid",
                }
            ]
        )
        with pytest.raises(ValueError, match="facility"):
            _build(custom=custom)

    def test_syslog_invalid_protocol_rejected(self) -> None:
        custom = json.dumps(
            [
                {
                    "sink_type": "syslog",
                    "syslog_host": "localhost",
                    "syslog_protocol": "quic",
                }
            ]
        )
        with pytest.raises(ValueError, match="protocol"):
            _build(custom=custom)


# -- Custom HTTP sinks ---------------------------------------------


@pytest.mark.unit
class TestCustomHttpSinks:
    """Custom sinks with sink_type='http'."""

    def test_http_sink_created(self) -> None:
        custom = json.dumps(
            [
                {
                    "sink_type": "http",
                    "http_url": "https://logs.example.com/ingest",
                }
            ]
        )
        result = _build(custom=custom)
        assert len(result.config.sinks) == _DEFAULTS_COUNT + 1
        http_sink = result.config.sinks[-1]
        assert http_sink.sink_type == SinkType.HTTP
        assert http_sink.http_url == "https://logs.example.com/ingest"
        assert http_sink.http_batch_size == 100

    def test_http_custom_settings(self) -> None:
        custom = json.dumps(
            [
                {
                    "sink_type": "http",
                    "http_url": "http://example-collector:3100/push",
                    "http_batch_size": 50,
                    "http_flush_interval_seconds": 2.0,
                    "http_timeout_seconds": 30.0,
                    "http_max_retries": 5,
                    "http_headers": [["Authorization", "Bearer tok"]],
                    "level": "warning",
                }
            ]
        )
        result = _build(custom=custom)
        http_sink = result.config.sinks[-1]
        assert http_sink.http_batch_size == 50
        assert http_sink.http_flush_interval_seconds == 2.0
        assert http_sink.http_timeout_seconds == 30.0
        assert http_sink.http_max_retries == 5
        assert http_sink.http_headers == (("Authorization", "Bearer tok"),)
        assert http_sink.level == LogLevel.WARNING

    def test_http_requires_url(self) -> None:
        custom = json.dumps([{"sink_type": "http"}])
        with pytest.raises(ValueError, match="http_url"):
            _build(custom=custom)

    def test_http_rejects_non_http_scheme(self) -> None:
        custom = json.dumps(
            [
                {
                    "sink_type": "http",
                    "http_url": "ftp://example.com",
                }
            ]
        )
        with pytest.raises(ValueError, match="http_url must start with"):
            _build(custom=custom)

    def test_http_no_routing(self) -> None:
        custom = json.dumps(
            [
                {
                    "sink_type": "http",
                    "http_url": "https://example.com/logs",
                }
            ]
        )
        result = _build(custom=custom)
        assert len(result.routing_overrides) == 0

    def test_http_unknown_field_rejected(self) -> None:
        custom = json.dumps(
            [
                {
                    "sink_type": "http",
                    "http_url": "https://example.com",
                    "bad_field": True,
                }
            ]
        )
        with pytest.raises(ValueError, match="Unknown fields"):
            _build(custom=custom)


# -- Custom sink type dispatch -------------------------------------


@pytest.mark.unit
class TestCustomSinkTypeDispatch:
    """The sink_type field controls which builder path is used."""

    def test_default_omitted_remains_file(self) -> None:
        custom = json.dumps([{"file_path": "extra.log"}])
        result = _build(custom=custom)
        assert result.config.sinks[-1].sink_type == SinkType.FILE

    def test_explicit_file_type(self) -> None:
        custom = json.dumps(
            [
                {
                    "sink_type": "file",
                    "file_path": "extra.log",
                }
            ]
        )
        result = _build(custom=custom)
        assert result.config.sinks[-1].sink_type == SinkType.FILE

    def test_unknown_sink_type_rejected(self) -> None:
        custom = json.dumps(
            [
                {
                    "sink_type": "kafka",
                    "endpoint": "broker:9092",
                }
            ]
        )
        with pytest.raises(ValueError, match="sink_type"):
            _build(custom=custom)

    def test_mixed_types(self) -> None:
        custom = json.dumps(
            [
                {"file_path": "extra.log"},
                {
                    "sink_type": "syslog",
                    "syslog_host": "loghost",
                },
                {
                    "sink_type": "http",
                    "http_url": "https://example.com/logs",
                },
            ]
        )
        result = _build(custom=custom)
        assert len(result.config.sinks) == _DEFAULTS_COUNT + 3
        types = [s.sink_type for s in result.config.sinks[-3:]]
        assert types == [SinkType.FILE, SinkType.SYSLOG, SinkType.HTTP]
