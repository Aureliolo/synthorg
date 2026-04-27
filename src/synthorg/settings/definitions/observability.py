"""Observability namespace setting definitions."""

from synthorg.settings.enums import SettingLevel, SettingNamespace, SettingType
from synthorg.settings.models import SettingDefinition
from synthorg.settings.registry import get_registry

_r = get_registry()

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.OBSERVABILITY,
        key="root_log_level",
        type=SettingType.ENUM,
        # Production-unsafe default: a "debug" registry default leaks
        # verbose payloads to HTTP log sinks and wastes bandwidth in
        # deployments that have not explicitly set the value. The
        # operator escape hatch for incident-response verbose logging
        # is the SYNTHORG_LOG_LEVEL env var (consumed by
        # _apply_console_level_override at boot).
        default="info",
        description="Root logger level",
        group="Logging",
        enum_values=("debug", "info", "warning", "error", "critical"),
        yaml_path="logging.root_level",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.OBSERVABILITY,
        key="enable_correlation",
        type=SettingType.BOOLEAN,
        default="true",
        description="Enable correlation ID tracking across agent calls",
        group="Logging",
        level=SettingLevel.ADVANCED,
        yaml_path="logging.enable_correlation",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.OBSERVABILITY,
        key="sink_overrides",
        type=SettingType.JSON,
        default="{}",
        description=(
            "Per-sink overrides keyed by sink identifier "
            "(__console__ or file path). Each value is an object with "
            "optional fields: enabled (bool), level (string), "
            "json_format (bool), rotation (object with strategy, "
            "max_bytes, backup_count, compress_rotated "
            "(builtin-only; rejected with external strategy))"
        ),
        group="Sinks",
        level=SettingLevel.ADVANCED,
        yaml_path="logging.sink_overrides",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.OBSERVABILITY,
        key="custom_sinks",
        type=SettingType.JSON,
        default="[]",
        description=(
            "Additional sinks as JSON array. Each entry may specify "
            "sink_type (file, syslog, http; default file). "
            "File: file_path (required), level, json_format, rotation, "
            "routing_prefixes. "
            "Syslog: syslog_host (required), syslog_port, "
            "syslog_facility, syslog_protocol, level. "
            "HTTP: http_url (required), http_headers, http_batch_size, "
            "http_flush_interval_seconds, http_timeout_seconds, "
            "http_max_retries, level"
        ),
        group="Sinks",
        level=SettingLevel.ADVANCED,
        yaml_path="logging.custom_sinks",
    )
)

# ── HTTP log-handler defaults (applied to all HTTP sinks) ────────

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.OBSERVABILITY,
        key="http_batch_size",
        type=SettingType.INTEGER,
        default="100",
        description="Default batch size for HTTP log handlers",
        group="HTTP Sink",
        level=SettingLevel.ADVANCED,
        restart_required=True,
        min_value=10,
        max_value=1000,
        yaml_path="logging.http_sink.batch_size",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.OBSERVABILITY,
        key="http_flush_interval_seconds",
        type=SettingType.FLOAT,
        default="5.0",
        description="Default flush interval for HTTP log handlers",
        group="HTTP Sink",
        level=SettingLevel.ADVANCED,
        restart_required=True,
        min_value=0.5,
        max_value=60.0,
        yaml_path="logging.http_sink.flush_interval_seconds",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.OBSERVABILITY,
        key="http_timeout_seconds",
        type=SettingType.FLOAT,
        default="10.0",
        description="Default HTTP timeout for log-handler POSTs",
        group="HTTP Sink",
        level=SettingLevel.ADVANCED,
        restart_required=True,
        min_value=1.0,
        max_value=60.0,
        yaml_path="logging.http_sink.timeout_seconds",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.OBSERVABILITY,
        key="http_max_retries",
        type=SettingType.INTEGER,
        default="3",
        description="Default retry count for HTTP log-handler POSTs",
        group="HTTP Sink",
        level=SettingLevel.ADVANCED,
        restart_required=True,
        min_value=0,
        max_value=10,
        yaml_path="logging.http_sink.max_retries",
    )
)

# ── Audit-chain signing timeout ─────────────────────────────────

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.OBSERVABILITY,
        key="audit_chain_signing_timeout_seconds",
        type=SettingType.FLOAT,
        default="5.0",
        description=(
            "Timeout for signing and timestamp operations in the audit-chain"
            " sink. Applied once at API startup via"
            " AuditChainSink.set_signing_timeout_seconds; runtime dispatch is"
            " not wired, so a change requires a process restart."
        ),
        group="Audit Chain",
        level=SettingLevel.ADVANCED,
        restart_required=True,
        min_value=1.0,
        max_value=60.0,
        yaml_path="logging.audit_chain.signing_timeout_seconds",
    )
)

# ── Multi-surface settings (#1613) ─────────────────────────────

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.OBSERVABILITY,
        key="log_directory",
        type=SettingType.STRING,
        default="",
        description=(
            "Log output directory. Sourced from the SYNTHORG_LOG_DIR env"
            " var > YAML (logging.log_dir) > unset at process start."
            " Read-only post-init: the directory is opened once at"
            " bootstrap_logging and a runtime change requires a"
            " process restart."
        ),
        group="Logging",
        level=SettingLevel.ADVANCED,
        restart_required=True,
        read_only_post_init=True,
        env_var_override="SYNTHORG_LOG_DIR",
        yaml_path="logging.log_dir",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.OBSERVABILITY,
        key="log_level_console",
        type=SettingType.STRING,
        default="",
        description=(
            "Override the console sink's log level distinct from the root"
            " logger.  Empty string means 'use the root_log_level / sink"
            " default'.  Accepts debug / info / warning / error / critical"
            " (case-insensitive) or the empty string.  Sourced from"
            " DB > env (SYNTHORG_LOG_LEVEL) > unset.  Mutable at runtime:"
            " the next call to _apply_console_level_override applies"
            " the new value."
        ),
        group="Logging",
        level=SettingLevel.ADVANCED,
        validator_pattern=r"^(?:|debug|info|warning|error|critical|DEBUG|INFO|WARNING|ERROR|CRITICAL)$",
        env_var_override="SYNTHORG_LOG_LEVEL",
    )
)
