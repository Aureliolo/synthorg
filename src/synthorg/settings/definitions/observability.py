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
    )
)

# ── Multi-surface settings ─────────────────────────────────────

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.OBSERVABILITY,
        key="log_directory",
        type=SettingType.STRING,
        default="",
        description=(
            "Log output directory. Sourced from the SYNTHORG_LOG_DIR env"
            " var > unset at process start."
            " Read-only post-init: the directory is opened once at"
            " bootstrap_logging and a runtime change requires a"
            " process restart."
        ),
        group="Logging",
        level=SettingLevel.ADVANCED,
        restart_required=True,
        read_only_post_init=True,
        env_var_override="SYNTHORG_LOG_DIR",
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
            " DB > env (SYNTHORG_LOG_LEVEL) > unset.  Mutable at"
            " runtime: the next call to"
            " _apply_console_level_override applies the new value."
        ),
        group="Logging",
        level=SettingLevel.ADVANCED,
        # ``(?i:...)`` is an inline case-insensitive group so mixed-case
        # inputs like ``Info`` or ``Debug`` validate the same as
        # ``info`` / ``DEBUG``; the description advertises
        # case-insensitivity, the validator must honour it.
        validator_pattern=r"^(?:|(?i:debug|info|warning|error|critical))$",
        env_var_override="SYNTHORG_LOG_LEVEL",
    )
)

# ── TSA preset endpoints (RFC 3161) ──────────────────────────────
# Each preset's canonical URL is registered as a setting so operators
# can pin a vendor-controlled hostname change or stand up a private
# TSA without a code patch.  Consumed via ``ObservabilityBridgeConfig``.

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.OBSERVABILITY,
        key="tsa_endpoint_freetsa",
        type=SettingType.STRING,
        default="https://freetsa.org/tsr",
        description=(
            "RFC 3161 Time-Stamp Authority endpoint URL for the FreeTSA"
            " preset.  Override only if FreeTSA changes its endpoint or"
            " an operator stands up a private mirror with the same trust"
            " anchors."
        ),
        group="Audit Chain",
        level=SettingLevel.ADVANCED,
        restart_required=True,
        validator_pattern=r"^https://[\w.\-:]+(?:/.*)?$",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.OBSERVABILITY,
        key="tsa_endpoint_digicert",
        type=SettingType.STRING,
        default="https://timestamp.digicert.com",
        description=(
            "RFC 3161 Time-Stamp Authority endpoint URL for the DigiCert"
            " preset.  Override only if DigiCert changes its endpoint."
        ),
        group="Audit Chain",
        level=SettingLevel.ADVANCED,
        restart_required=True,
        validator_pattern=r"^https://[\w.\-:]+(?:/.*)?$",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.OBSERVABILITY,
        key="tsa_endpoint_sectigo",
        type=SettingType.STRING,
        default="https://timestamp.sectigo.com",
        description=(
            "RFC 3161 Time-Stamp Authority endpoint URL for the Sectigo"
            " preset.  Override only if Sectigo changes its endpoint."
        ),
        group="Audit Chain",
        level=SettingLevel.ADVANCED,
        restart_required=True,
        validator_pattern=r"^https://[\w.\-:]+(?:/.*)?$",
    )
)
