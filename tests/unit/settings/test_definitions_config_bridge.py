"""Parametrized coverage for the config-bridge settings.

Each row of ``_EXPECTED`` describes one settings entry covering the
namespace bridge: expected namespace, key, type, default, numeric
bounds, restart requirement, and required presence of ``yaml_path`` /
``group`` / ``description``.

Behaviour is asserted against the live registry after triggering
registration via ``import synthorg.settings.definitions``.
"""

import pytest

import synthorg.settings.definitions  # noqa: F401 -- trigger registration
from synthorg.settings.enums import SettingNamespace, SettingType
from synthorg.settings.registry import get_registry

_Spec = tuple[
    SettingNamespace,
    str,
    SettingType,
    str,
    float | None,
    float | None,
    bool,
]


def _spec(*args: object) -> _Spec:
    """Cast a positional tuple literal to ``_Spec`` for readability."""
    return args  # type: ignore[return-value]


_EXPECTED: tuple[
    tuple[
        SettingNamespace,
        str,
        SettingType,
        str,
        float | None,
        float | None,
        bool,
    ],
    ...,
] = (
    # api (9 new)
    _spec(
        SettingNamespace.API,
        "ticket_cleanup_interval_seconds",
        SettingType.FLOAT,
        "60.0",
        5.0,
        3600.0,
        False,
    ),
    _spec(
        SettingNamespace.API,
        "max_rpm_default",
        SettingType.INTEGER,
        "60",
        1,
        100_000,
        True,
    ),
    _spec(
        SettingNamespace.API,
        "compression_minimum_size_bytes",
        SettingType.INTEGER,
        "1000",
        100,
        10_000,
        True,
    ),
    _spec(
        SettingNamespace.API,
        "request_max_body_size_bytes",
        SettingType.INTEGER,
        "52428800",
        1_000_000,
        536_870_912,
        True,
    ),
    _spec(
        SettingNamespace.API,
        "ws_ticket_max_pending_per_user",
        SettingType.INTEGER,
        "5",
        1,
        50,
        False,
    ),
    _spec(
        SettingNamespace.API,
        "max_lifecycle_events_per_query",
        SettingType.INTEGER,
        "10000",
        100,
        1_000_000,
        False,
    ),
    _spec(
        SettingNamespace.API,
        "max_audit_records_per_query",
        SettingType.INTEGER,
        "10000",
        100,
        1_000_000,
        False,
    ),
    _spec(
        SettingNamespace.API,
        "max_metrics_per_query",
        SettingType.INTEGER,
        "10000",
        100,
        1_000_000,
        False,
    ),
    _spec(
        SettingNamespace.API,
        "max_meeting_context_keys",
        SettingType.INTEGER,
        "20",
        5,
        100,
        True,
    ),
    # communication (9 new)
    _spec(
        SettingNamespace.COMMUNICATION,
        "bus_bridge_poll_timeout_seconds",
        SettingType.FLOAT,
        "1.0",
        0.1,
        10.0,
        False,
    ),
    _spec(
        SettingNamespace.COMMUNICATION,
        "bus_bridge_max_consecutive_errors",
        SettingType.INTEGER,
        "30",
        5,
        100,
        False,
    ),
    _spec(
        SettingNamespace.COMMUNICATION,
        "webhook_bridge_poll_timeout_seconds",
        SettingType.FLOAT,
        "1.0",
        0.1,
        10.0,
        False,
    ),
    _spec(
        SettingNamespace.COMMUNICATION,
        "webhook_bridge_max_consecutive_errors",
        SettingType.INTEGER,
        "30",
        5,
        100,
        False,
    ),
    _spec(
        SettingNamespace.COMMUNICATION,
        "nats_history_batch_size",
        SettingType.INTEGER,
        "100",
        10,
        1000,
        False,
    ),
    _spec(
        SettingNamespace.COMMUNICATION,
        "nats_history_fetch_timeout_seconds",
        SettingType.FLOAT,
        "0.5",
        0.1,
        5.0,
        False,
    ),
    _spec(
        SettingNamespace.COMMUNICATION,
        "delegation_record_store_max_size",
        SettingType.INTEGER,
        "10000",
        100,
        1_000_000,
        True,
    ),
    _spec(
        SettingNamespace.COMMUNICATION,
        "event_stream_max_queue_size",
        SettingType.INTEGER,
        "256",
        16,
        10_000,
        True,
    ),
    _spec(
        SettingNamespace.COMMUNICATION,
        "loop_prevention_window_seconds",
        SettingType.FLOAT,
        "60.0",
        5.0,
        600.0,
        False,
    ),
    # a2a (2 new)
    _spec(
        SettingNamespace.A2A,
        "client_timeout_seconds",
        SettingType.FLOAT,
        "30.0",
        5.0,
        300.0,
        True,
    ),
    _spec(
        SettingNamespace.A2A,
        "push_verification_clock_skew_seconds",
        SettingType.INTEGER,
        "300",
        0,
        3600,
        False,
    ),
    # engine (2 new)
    _spec(
        SettingNamespace.ENGINE,
        "approval_interrupt_timeout_seconds",
        SettingType.FLOAT,
        "300.0",
        30.0,
        3600.0,
        False,
    ),
    _spec(
        SettingNamespace.ENGINE,
        "health_quality_degradation_threshold",
        SettingType.INTEGER,
        "3",
        1,
        10,
        False,
    ),
    # memory (1 new)
    _spec(
        SettingNamespace.MEMORY,
        "consolidation_enforce_batch_size",
        SettingType.INTEGER,
        "1000",
        100,
        10_000,
        False,
    ),
    # integrations (4 new)
    _spec(
        SettingNamespace.INTEGRATIONS,
        "health_probe_interval_seconds",
        SettingType.INTEGER,
        "300",
        30,
        3600,
        False,
    ),
    _spec(
        SettingNamespace.INTEGRATIONS,
        "oauth_http_timeout_seconds",
        SettingType.FLOAT,
        "30.0",
        5.0,
        300.0,
        True,
    ),
    _spec(
        SettingNamespace.INTEGRATIONS,
        "oauth_device_flow_max_wait_seconds",
        SettingType.INTEGER,
        "600",
        60,
        7200,
        False,
    ),
    _spec(
        SettingNamespace.INTEGRATIONS,
        "rate_limit_coordinator_poll_timeout_seconds",
        SettingType.FLOAT,
        "0.5",
        0.1,
        10.0,
        False,
    ),
    # meta (3 new)
    _spec(
        SettingNamespace.META,
        "ci_timeout_seconds",
        SettingType.INTEGER,
        "300",
        30,
        600,
        False,
    ),
    _spec(
        SettingNamespace.META,
        "proposal_rate_limit_max",
        SettingType.INTEGER,
        "10",
        1,
        100,
        False,
    ),
    _spec(
        SettingNamespace.META,
        "outcome_store_default_limit",
        SettingType.INTEGER,
        "10",
        1,
        100,
        False,
    ),
    # observability (5 new)
    _spec(
        SettingNamespace.OBSERVABILITY,
        "http_batch_size",
        SettingType.INTEGER,
        "100",
        10,
        1000,
        True,
    ),
    _spec(
        SettingNamespace.OBSERVABILITY,
        "http_flush_interval_seconds",
        SettingType.FLOAT,
        "5.0",
        0.5,
        60.0,
        True,
    ),
    _spec(
        SettingNamespace.OBSERVABILITY,
        "http_timeout_seconds",
        SettingType.FLOAT,
        "10.0",
        1.0,
        60.0,
        True,
    ),
    _spec(
        SettingNamespace.OBSERVABILITY,
        "http_max_retries",
        SettingType.INTEGER,
        "3",
        0,
        10,
        True,
    ),
    _spec(
        SettingNamespace.OBSERVABILITY,
        "audit_chain_signing_timeout_seconds",
        SettingType.FLOAT,
        "5.0",
        1.0,
        60.0,
        True,
    ),
    # notifications (3 new)
    _spec(
        SettingNamespace.NOTIFICATIONS,
        "slack_webhook_timeout_seconds",
        SettingType.FLOAT,
        "10.0",
        1.0,
        60.0,
        True,
    ),
    _spec(
        SettingNamespace.NOTIFICATIONS,
        "ntfy_webhook_timeout_seconds",
        SettingType.FLOAT,
        "10.0",
        1.0,
        60.0,
        True,
    ),
    _spec(
        SettingNamespace.NOTIFICATIONS,
        "email_smtp_timeout_seconds",
        SettingType.FLOAT,
        "10.0",
        1.0,
        60.0,
        True,
    ),
    # tools (9 new)
    _spec(
        SettingNamespace.TOOLS,
        "git_kill_grace_timeout_seconds",
        SettingType.FLOAT,
        "5.0",
        1.0,
        60.0,
        False,
    ),
    _spec(
        SettingNamespace.TOOLS,
        "atlas_kill_grace_timeout_seconds",
        SettingType.FLOAT,
        "5.0",
        1.0,
        60.0,
        False,
    ),
    _spec(
        SettingNamespace.TOOLS,
        "docker_sidecar_health_poll_interval_seconds",
        SettingType.FLOAT,
        "0.2",
        0.05,
        5.0,
        True,
    ),
    _spec(
        SettingNamespace.TOOLS,
        "docker_sidecar_health_timeout_seconds",
        SettingType.FLOAT,
        "15.0",
        1.0,
        300.0,
        True,
    ),
    _spec(
        SettingNamespace.TOOLS,
        "docker_sidecar_cpu_limit",
        SettingType.FLOAT,
        "0.5",
        0.1,
        16.0,
        True,
    ),
    _spec(
        SettingNamespace.TOOLS,
        "docker_sidecar_max_pids",
        SettingType.INTEGER,
        "32",
        1,
        4096,
        True,
    ),
    _spec(
        SettingNamespace.TOOLS,
        "docker_stop_grace_timeout_seconds",
        SettingType.INTEGER,
        "5",
        1,
        300,
        True,
    ),
    _spec(
        SettingNamespace.TOOLS,
        "subprocess_kill_grace_timeout_seconds",
        SettingType.FLOAT,
        "5.0",
        1.0,
        60.0,
        False,
    ),
    _spec(
        SettingNamespace.TOOLS,
        "docker_sidecar_memory_limit",
        SettingType.STRING,
        "64m",
        None,
        None,
        True,
    ),
    # settings (3 new) - dispatcher self-config
    _spec(
        SettingNamespace.SETTINGS,
        "dispatcher_poll_timeout_seconds",
        SettingType.FLOAT,
        "1.0",
        0.1,
        10.0,
        False,
    ),
    _spec(
        SettingNamespace.SETTINGS,
        "dispatcher_error_backoff_seconds",
        SettingType.FLOAT,
        "1.0",
        0.1,
        60.0,
        False,
    ),
    _spec(
        SettingNamespace.SETTINGS,
        "dispatcher_max_consecutive_errors",
        SettingType.INTEGER,
        "30",
        5,
        100,
        False,
    ),
)


@pytest.mark.unit
@pytest.mark.parametrize(
    (
        "namespace",
        "key",
        "setting_type",
        "default",
        "min_value",
        "max_value",
        "restart",
    ),
    _EXPECTED,
    ids=lambda p: p if isinstance(p, str) else None,
)
def test_config_bridge_setting_is_registered(  # noqa: PLR0913
    namespace: SettingNamespace,
    key: str,
    setting_type: SettingType,
    default: str,
    min_value: float | None,
    max_value: float | None,
    restart: bool,
) -> None:
    """Each config-bridge setting is registered with the expected metadata."""
    registry = get_registry()
    defn = registry.get(namespace.value, key)
    assert defn is not None, f"{namespace.value}/{key} not registered"
    assert defn.type == setting_type
    assert defn.default == default
    assert defn.min_value == min_value
    assert defn.max_value == max_value
    assert defn.restart_required is restart
    assert defn.group.strip(), f"{namespace.value}/{key} has blank group"
    assert defn.description.strip(), f"{namespace.value}/{key} has blank description"
    assert defn.yaml_path is not None, f"{namespace.value}/{key} missing yaml_path"
    assert defn.yaml_path.strip(), f"{namespace.value}/{key} blank yaml_path"


@pytest.mark.unit
def test_docker_sidecar_memory_limit_pattern() -> None:
    """The docker sidecar memory limit is a STRING with a size regex.

    The pattern allows raw bytes (no suffix) and an optional single-character
    size unit ``b``/``k``/``m``/``g`` (case-insensitive), while rejecting
    leading-zero and multi-character suffixes.
    """
    registry = get_registry()
    defn = registry.get(SettingNamespace.TOOLS.value, "docker_sidecar_memory_limit")
    assert defn is not None
    assert defn.type == SettingType.STRING
    assert defn.default == "64m"
    assert defn.validator_pattern == r"^[1-9]\d*[bkmgBKMG]?$"
    assert defn.restart_required is True


# Exact group + yaml_path expectations per-setting so typos/drift in
# user-facing text cannot slip through. Group and yaml-path are the
# strings that appear in the Settings UI and YAML config files, so we
# assert equality rather than relying on the non-empty spot-check in
# ``test_config_bridge_setting_is_registered``.
_METADATA_EXPECTED: tuple[tuple[SettingNamespace, str, str, str], ...] = (
    # api
    (
        SettingNamespace.API,
        "ticket_cleanup_interval_seconds",
        "WebSocket",
        "api.ticket_cleanup_interval_seconds",
    ),
    (
        SettingNamespace.API,
        "ws_ticket_max_pending_per_user",
        "WebSocket",
        "api.ws_ticket_max_pending_per_user",
    ),
    (
        SettingNamespace.API,
        "max_rpm_default",
        "Rate Limiting",
        "api.rate_limit.max_rpm_default",
    ),
    (
        SettingNamespace.API,
        "compression_minimum_size_bytes",
        "Server",
        "api.server.compression_minimum_size_bytes",
    ),
    (
        SettingNamespace.API,
        "request_max_body_size_bytes",
        "Server",
        "api.server.request_max_body_size_bytes",
    ),
    # communication
    (
        SettingNamespace.COMMUNICATION,
        "bus_bridge_poll_timeout_seconds",
        "Bus Bridge",
        "communication.bus_bridge.poll_timeout_seconds",
    ),
    (
        SettingNamespace.COMMUNICATION,
        "webhook_bridge_poll_timeout_seconds",
        "Bus Bridge",
        "communication.webhook_bridge.poll_timeout_seconds",
    ),
    # tools
    (
        SettingNamespace.TOOLS,
        "git_kill_grace_timeout_seconds",
        "Git",
        "tools.git.kill_grace_timeout_seconds",
    ),
    (
        SettingNamespace.TOOLS,
        "atlas_kill_grace_timeout_seconds",
        "Atlas",
        "tools.atlas.kill_grace_timeout_seconds",
    ),
    (
        SettingNamespace.TOOLS,
        "docker_sidecar_memory_limit",
        "Docker Sandbox",
        "tools.docker.sidecar_memory_limit",
    ),
    # observability
    (
        SettingNamespace.OBSERVABILITY,
        "audit_chain_signing_timeout_seconds",
        "Audit Chain",
        "logging.audit_chain.signing_timeout_seconds",
    ),
    # notifications
    (
        SettingNamespace.NOTIFICATIONS,
        "slack_webhook_timeout_seconds",
        "Slack",
        "notifications.slack.webhook_timeout_seconds",
    ),
)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("namespace", "key", "expected_group", "expected_yaml_path"),
    _METADATA_EXPECTED,
    ids=[f"{row[0].value}.{row[1]}" for row in _METADATA_EXPECTED],
)
def test_config_bridge_setting_metadata_exact(
    namespace: SettingNamespace,
    key: str,
    expected_group: str,
    expected_yaml_path: str,
) -> None:
    """Spot-check exact group + yaml_path values for representative settings.

    Catches typos and drift that the non-empty check in
    ``test_config_bridge_setting_is_registered`` would miss. One row
    per representative setting per namespace -- we do not need to
    duplicate the exact strings for every setting; a single typo in
    any user-facing metadata column would fail here.
    """
    registry = get_registry()
    defn = registry.get(namespace.value, key)
    assert defn is not None, f"{namespace.value}/{key} not registered"
    assert defn.group == expected_group, f"{namespace.value}/{key}: group mismatch"
    assert defn.yaml_path == expected_yaml_path, (
        f"{namespace.value}/{key}: yaml_path mismatch"
    )
    assert defn.description.strip(), f"{namespace.value}/{key} has blank description"


@pytest.mark.unit
def test_all_config_bridge_settings_have_advanced_level() -> None:
    """Every config-bridge setting should default to the ADVANCED UI group.

    These are operator-tuning knobs, not everyday basics.
    """
    from synthorg.settings.enums import SettingLevel

    registry = get_registry()
    for namespace, key, *_ in _EXPECTED:
        defn = registry.get(namespace.value, key)
        assert defn is not None
        assert defn.level == SettingLevel.ADVANCED, (
            f"{namespace.value}/{key} is not ADVANCED"
        )
