"""Unit tests for ConfigResolver bridge-config composed-read helpers.

Each helper assembles a frozen Pydantic dataclass from a namespace's
bridged settings using :meth:`ConfigResolver._resolve_bridge_fields`.
These tests verify the typed-return contract:

1. The returned dataclass matches the mocked resolved values.
2. Out-of-range or pattern-mismatched values raise a
   ``ValidationError`` at dataclass construction, so misconfigured
   operator values never escape the settings layer.

The mock-side assertions intentionally focus on the typed return
value; the exact ``SettingsService.get`` call signature and the
parallel ``asyncio.TaskGroup`` resolution are covered by the
lower-level ``tests/unit/settings/test_resolver.py`` suite.
"""

from typing import Any
from unittest.mock import AsyncMock

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

from synthorg.settings.bridge_configs import (
    A2ABridgeConfig,
    ApiBridgeConfig,
    CommunicationBridgeConfig,
    EngineBridgeConfig,
    IntegrationsBridgeConfig,
    MemoryBridgeConfig,
    MetaBridgeConfig,
    NotificationsBridgeConfig,
    ObservabilityBridgeConfig,
    SettingsDispatcherBridgeConfig,
    ToolsBridgeConfig,
)
from synthorg.settings.enums import SettingNamespace, SettingSource
from synthorg.settings.models import SettingValue
from synthorg.settings.resolver import ConfigResolver


class _FakeRootConfig(BaseModel):
    model_config = ConfigDict(frozen=True)


@pytest.fixture
def mock_settings() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def resolver(mock_settings: AsyncMock) -> ConfigResolver:
    return ConfigResolver(
        settings_service=mock_settings,
        config=_FakeRootConfig(),  # type: ignore[arg-type]
    )


def _value(namespace: SettingNamespace, key: str, value: str) -> SettingValue:
    return SettingValue(
        namespace=namespace, key=key, value=value, source=SettingSource.DEFAULT
    )


def _static_responses(
    mapping: dict[tuple[str, str], str],
) -> Any:
    """Build an AsyncMock side-effect that returns values from ``mapping``."""

    async def _side_effect(namespace: str, key: str) -> SettingValue:
        try:
            value_str = mapping[(namespace, key)]
        except KeyError as exc:  # pragma: no cover - test misconfiguration
            msg = f"unexpected settings lookup: {namespace}/{key}"
            raise AssertionError(msg) from exc
        return _value(SettingNamespace(namespace), key, value_str)

    return _side_effect


# ── Happy-path matrix ────────────────────────────────────────────
#
# Each row is a single bridge-config helper:
#   (method_name, expected_class, mock_settings_mapping, expected_attrs).
# ``expected_attrs`` is the subset of resolved fields we cross-check
# against the mock values; covering every field per namespace is the
# job of ``test_definitions_config_bridge.py``, so we only spot-check
# representative typed values here.

_HAPPY_CASES: tuple[
    tuple[str, type[BaseModel], dict[tuple[str, str], str], dict[str, object]],
    ...,
] = (
    (
        "get_api_bridge_config",
        ApiBridgeConfig,
        {
            ("api", "ticket_cleanup_interval_seconds"): "60.0",
            ("api", "ws_ticket_max_pending_per_user"): "5",
            ("api", "ws_auth_timeout_seconds"): "10.0",
            ("api", "ws_frame_timeout_seconds"): "30",
            ("api", "ws_revalidation_window_seconds"): "60",
            ("api", "ws_revalidation_max_failures"): "5",
            ("api", "sse_keepalive_seconds"): "30.0",
            ("api", "max_rpm_default"): "60",
            ("api", "compression_minimum_size_bytes"): "1000",
            ("api", "request_max_body_size_bytes"): "52428800",
            ("api", "max_lifecycle_events_per_query"): "10000",
            ("api", "max_audit_records_per_query"): "10000",
            ("api", "max_metrics_per_query"): "10000",
            ("api", "max_meeting_context_keys"): "20",
            ("api", "rate_limit_gc_every_n_acquires"): "1024",
            ("api", "rate_limit_gc_min_horizon_seconds"): "60",
            ("api", "rate_limit_inflight_gc_every_n_acquires"): "1024",
            ("api", "rate_limit_inflight_min_retry_after_seconds"): "1",
            ("api", "lifecycle_task_engine_shutdown_seconds"): "8.0",
            ("api", "lifecycle_meeting_scheduler_shutdown_seconds"): "2.0",
            ("api", "lifecycle_performance_tracker_shutdown_seconds"): "2.0",
            ("api", "lifecycle_backup_shutdown_seconds"): "5.0",
            ("api", "lifecycle_settings_dispatcher_shutdown_seconds"): "2.0",
            ("api", "lifecycle_bridge_shutdown_seconds"): "2.0",
            ("api", "lifecycle_distributed_queue_shutdown_seconds"): "3.0",
            ("api", "lifecycle_message_bus_shutdown_seconds"): "3.0",
            ("api", "lifecycle_persistence_shutdown_seconds"): "5.0",
            ("api", "lifecycle_approval_timeout_shutdown_seconds"): "1.0",
            ("api", "lifecycle_drain_timeout_seconds"): "25.0",
            ("api", "approval_urgency_critical_seconds"): "3600.0",
            ("api", "approval_urgency_high_seconds"): "14400.0",
            ("api", "csp_docs_external_origins"): (
                '["https://cdn.example.com", "https://fonts.example.com"]'
            ),
            ("api", "error_docs_base_url"): "https://docs.example.com/errors",
        },
        {
            "ticket_cleanup_interval_seconds": 60.0,
            "ws_ticket_max_pending_per_user": 5,
            "ws_auth_timeout_seconds": 10.0,
            "ws_frame_timeout_seconds": 30,
            "ws_revalidation_window_seconds": 60,
            "ws_revalidation_max_failures": 5,
            "sse_keepalive_seconds": 30.0,
            "max_rpm_default": 60,
            "compression_minimum_size_bytes": 1000,
            "request_max_body_size_bytes": 52_428_800,
            "max_lifecycle_events_per_query": 10_000,
            "max_audit_records_per_query": 10_000,
            "max_metrics_per_query": 10_000,
            "rate_limit_gc_every_n_acquires": 1024,
            "lifecycle_drain_timeout_seconds": 25.0,
            "approval_urgency_critical_seconds": 3600.0,
            "approval_urgency_high_seconds": 14_400.0,
            "max_meeting_context_keys": 20,
            "csp_docs_external_origins": (
                "https://cdn.example.com",
                "https://fonts.example.com",
            ),
            "error_docs_base_url": "https://docs.example.com/errors",
        },
    ),
    (
        "get_communication_bridge_config",
        CommunicationBridgeConfig,
        {
            ("communication", "bus_bridge_poll_timeout_seconds"): "1.0",
            ("communication", "bus_bridge_max_consecutive_errors"): "30",
            ("communication", "webhook_bridge_poll_timeout_seconds"): "1.0",
            ("communication", "webhook_bridge_max_consecutive_errors"): "30",
            ("communication", "nats_history_batch_size"): "100",
            ("communication", "nats_history_fetch_timeout_seconds"): "0.5",
            ("communication", "delegation_record_store_max_size"): "10000",
            ("communication", "event_stream_max_queue_size"): "256",
            ("communication", "loop_prevention_window_seconds"): "60.0",
        },
        {
            "bus_bridge_poll_timeout_seconds": 1.0,
            "bus_bridge_max_consecutive_errors": 30,
            "nats_history_batch_size": 100,
            "event_stream_max_queue_size": 256,
        },
    ),
    (
        "get_a2a_bridge_config",
        A2ABridgeConfig,
        {
            ("a2a", "client_timeout_seconds"): "45.0",
            ("a2a", "push_verification_clock_skew_seconds"): "120",
            ("a2a", "max_message_parts"): "250",
        },
        {
            "client_timeout_seconds": 45.0,
            "push_verification_clock_skew_seconds": 120,
            "max_message_parts": 250,
        },
    ),
    (
        "get_engine_bridge_config",
        EngineBridgeConfig,
        {
            ("engine", "approval_interrupt_timeout_seconds"): "600.0",
            ("engine", "max_subworkflow_depth"): "32",
            ("engine", "health_quality_degradation_threshold"): "5",
            ("engine", "routing_weight_primary_skill"): "0.4",
            ("engine", "routing_weight_secondary_skill"): "0.2",
            ("engine", "routing_weight_tag_match_bonus"): "0.1",
            ("engine", "routing_weight_role_match_bonus"): "0.2",
            ("engine", "routing_weight_seniority_alignment_bonus"): "0.2",
            ("engine", "routing_min_score"): "0.1",
            ("engine", "matcher_tier_base_score"): "0.5",
            ("engine", "matcher_headroom_max_bonus"): "0.25",
            ("engine", "matcher_priority_max_bonus"): "0.25",
            ("engine", "matcher_headroom_ratio_cap"): "2.0",
            ("engine", "matcher_balanced_partial_credit"): "0.125",
            ("engine", "quality_heuristic_pass_threshold"): "0.5",
            ("engine", "quality_heuristic_pass_grade"): "0.8",
            ("engine", "quality_heuristic_fail_grade"): "0.3",
            ("engine", "quality_heuristic_confidence_ceiling"): "0.9",
            ("engine", "quality_heuristic_confidence_bias"): "0.1",
        },
        {
            "approval_interrupt_timeout_seconds": 600.0,
            "max_subworkflow_depth": 32,
            "health_quality_degradation_threshold": 5,
            "routing_weight_primary_skill": 0.4,
            "matcher_tier_base_score": 0.5,
            "quality_heuristic_pass_threshold": 0.5,
        },
    ),
    (
        "get_memory_bridge_config",
        MemoryBridgeConfig,
        {
            ("memory", "consolidation_enforce_batch_size"): "2500",
            ("memory", "fine_tune_chunk_size"): "512",
            ("memory", "fine_tune_vram_batch_table"): (
                "[[40.0, 128], [16.0, 64], [8.0, 32]]"
            ),
        },
        {
            "consolidation_enforce_batch_size": 2500,
            "fine_tune_chunk_size": 512,
            "fine_tune_vram_batch_table": (
                (40.0, 128),
                (16.0, 64),
                (8.0, 32),
            ),
        },
    ),
    (
        "get_integrations_bridge_config",
        IntegrationsBridgeConfig,
        {
            ("integrations", "health_probe_interval_seconds"): "300",
            ("integrations", "oauth_http_timeout_seconds"): "45.0",
            ("integrations", "oauth_device_flow_max_wait_seconds"): "900",
            (
                "integrations",
                "rate_limit_coordinator_poll_timeout_seconds",
            ): "0.5",
        },
        {
            "oauth_http_timeout_seconds": 45.0,
            "oauth_device_flow_max_wait_seconds": 900,
        },
    ),
    (
        "get_meta_bridge_config",
        MetaBridgeConfig,
        {
            ("meta", "ci_timeout_seconds"): "300",
            ("meta", "proposal_rate_limit_max"): "25",
            ("meta", "outcome_store_default_limit"): "50",
        },
        {
            "ci_timeout_seconds": 300,
            "proposal_rate_limit_max": 25,
            "outcome_store_default_limit": 50,
        },
    ),
    (
        "get_notifications_bridge_config",
        NotificationsBridgeConfig,
        {
            ("notifications", "slack_webhook_timeout_seconds"): "15.0",
            ("notifications", "ntfy_webhook_timeout_seconds"): "10.0",
            ("notifications", "email_smtp_timeout_seconds"): "30.0",
            (
                "notifications",
                "slack_default_webhook_url",
            ): "https://hooks.slack.com/services/T/B/X",
            ("notifications", "ntfy_default_url"): "https://ntfy.example.com",
        },
        {
            "slack_webhook_timeout_seconds": 15.0,
            "email_smtp_timeout_seconds": 30.0,
            "slack_default_webhook_url": "https://hooks.slack.com/services/T/B/X",
            "ntfy_default_url": "https://ntfy.example.com",
        },
    ),
    (
        "get_tools_bridge_config",
        ToolsBridgeConfig,
        {
            ("tools", "git_kill_grace_timeout_seconds"): "5.0",
            ("tools", "docker_sidecar_health_poll_interval_seconds"): "0.2",
            ("tools", "docker_sidecar_health_timeout_seconds"): "15.0",
            ("tools", "docker_sidecar_memory_limit"): "128m",
            ("tools", "docker_sidecar_cpu_limit"): "1.0",
            ("tools", "docker_sidecar_max_pids"): "64",
            ("tools", "docker_stop_grace_timeout_seconds"): "10",
            ("tools", "subprocess_kill_grace_timeout_seconds"): "5.0",
        },
        {
            "docker_sidecar_memory_limit": "128m",
            "docker_sidecar_cpu_limit": 1.0,
            "docker_sidecar_max_pids": 64,
        },
    ),
    (
        "get_observability_bridge_config",
        ObservabilityBridgeConfig,
        {
            ("observability", "http_batch_size"): "250",
            ("observability", "http_flush_interval_seconds"): "2.5",
            ("observability", "http_timeout_seconds"): "10.0",
            ("observability", "http_max_retries"): "5",
            ("observability", "audit_chain_signing_timeout_seconds"): "10.0",
            ("observability", "tsa_endpoint_freetsa"): "https://tsa.example.com/tsr",
            (
                "observability",
                "tsa_endpoint_digicert",
            ): "https://timestamp.digicert.com",
            ("observability", "tsa_endpoint_sectigo"): "https://timestamp.sectigo.com",
        },
        {
            "http_batch_size": 250,
            "http_max_retries": 5,
            "audit_chain_signing_timeout_seconds": 10.0,
            "tsa_endpoint_freetsa": "https://tsa.example.com/tsr",
            "tsa_endpoint_digicert": "https://timestamp.digicert.com",
            "tsa_endpoint_sectigo": "https://timestamp.sectigo.com",
        },
    ),
    (
        "get_settings_dispatcher_bridge_config",
        SettingsDispatcherBridgeConfig,
        {
            ("settings", "dispatcher_poll_timeout_seconds"): "0.25",
            ("settings", "dispatcher_error_backoff_seconds"): "2.0",
            ("settings", "dispatcher_max_consecutive_errors"): "50",
        },
        {
            "poll_timeout_seconds": 0.25,
            "error_backoff_seconds": 2.0,
            "max_consecutive_errors": 50,
        },
    ),
)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("method_name", "expected_cls", "mapping", "expected_attrs"),
    _HAPPY_CASES,
    ids=[case[0] for case in _HAPPY_CASES],
)
async def test_bridge_config_happy_path(  # noqa: PLR0913
    resolver: ConfigResolver,
    mock_settings: AsyncMock,
    method_name: str,
    expected_cls: type[BaseModel],
    mapping: dict[tuple[str, str], str],
    expected_attrs: dict[str, object],
) -> None:
    """Each bridge-config helper returns the right typed dataclass.

    Drives the 11 ``ConfigResolver.get_<ns>_bridge_config()`` methods
    through a single parametrized case matrix: mocked settings lookup
    returns the values in ``mapping``; the resolved dataclass must be
    an instance of ``expected_cls`` with the fields from
    ``expected_attrs`` set to the expected typed values.
    """
    mock_settings.get.side_effect = _static_responses(mapping)
    method = getattr(resolver, method_name)
    cfg = await method()
    assert isinstance(cfg, expected_cls)
    for attr, expected_value in expected_attrs.items():
        actual = getattr(cfg, attr)
        assert actual == expected_value, (
            f"{method_name}: {attr} expected {expected_value!r}, got {actual!r}"
        )


# ── Validation-failure cases ────────────────────────────────────


@pytest.mark.unit
async def test_get_api_bridge_config_rejects_out_of_range(
    resolver: ConfigResolver, mock_settings: AsyncMock
) -> None:
    mock_settings.get.side_effect = _static_responses(
        {
            ("api", "ticket_cleanup_interval_seconds"): "60.0",
            ("api", "ws_ticket_max_pending_per_user"): "5",
            ("api", "ws_auth_timeout_seconds"): "10.0",
            ("api", "ws_frame_timeout_seconds"): "30",
            ("api", "ws_revalidation_window_seconds"): "60",
            ("api", "ws_revalidation_max_failures"): "5",
            ("api", "sse_keepalive_seconds"): "30.0",
            ("api", "max_rpm_default"): "60",
            ("api", "compression_minimum_size_bytes"): "1000",
            # 10 GiB - way over the 512 MiB cap.
            ("api", "request_max_body_size_bytes"): "10737418240",
            ("api", "max_lifecycle_events_per_query"): "10000",
            ("api", "max_audit_records_per_query"): "10000",
            ("api", "max_metrics_per_query"): "10000",
            ("api", "max_meeting_context_keys"): "20",
            ("api", "rate_limit_gc_every_n_acquires"): "1024",
            ("api", "rate_limit_gc_min_horizon_seconds"): "60",
            ("api", "rate_limit_inflight_gc_every_n_acquires"): "1024",
            ("api", "rate_limit_inflight_min_retry_after_seconds"): "1",
            ("api", "lifecycle_task_engine_shutdown_seconds"): "8.0",
            ("api", "lifecycle_meeting_scheduler_shutdown_seconds"): "2.0",
            ("api", "lifecycle_performance_tracker_shutdown_seconds"): "2.0",
            ("api", "lifecycle_backup_shutdown_seconds"): "5.0",
            ("api", "lifecycle_settings_dispatcher_shutdown_seconds"): "2.0",
            ("api", "lifecycle_bridge_shutdown_seconds"): "2.0",
            ("api", "lifecycle_distributed_queue_shutdown_seconds"): "3.0",
            ("api", "lifecycle_message_bus_shutdown_seconds"): "3.0",
            ("api", "lifecycle_persistence_shutdown_seconds"): "5.0",
            ("api", "lifecycle_approval_timeout_shutdown_seconds"): "1.0",
            ("api", "lifecycle_drain_timeout_seconds"): "25.0",
            ("api", "approval_urgency_critical_seconds"): "3600.0",
            ("api", "approval_urgency_high_seconds"): "14400.0",
            ("api", "csp_docs_external_origins"): ('["https://cdn.example.com"]'),
            ("api", "error_docs_base_url"): "https://docs.example.com/errors",
        }
    )
    with pytest.raises(ValidationError):
        await resolver.get_api_bridge_config()


@pytest.mark.unit
async def test_get_tools_bridge_config_rejects_bad_memory_literal(
    resolver: ConfigResolver, mock_settings: AsyncMock
) -> None:
    mock_settings.get.side_effect = _static_responses(
        {
            ("tools", "git_kill_grace_timeout_seconds"): "5.0",
            ("tools", "docker_sidecar_health_poll_interval_seconds"): "0.2",
            ("tools", "docker_sidecar_health_timeout_seconds"): "15.0",
            # invalid format (not a size string: non-digit prefix).
            ("tools", "docker_sidecar_memory_limit"): "invalid",
            ("tools", "docker_sidecar_cpu_limit"): "0.5",
            ("tools", "docker_sidecar_max_pids"): "32",
            ("tools", "docker_stop_grace_timeout_seconds"): "5",
            ("tools", "subprocess_kill_grace_timeout_seconds"): "5.0",
        }
    )
    with pytest.raises(ValidationError):
        await resolver.get_tools_bridge_config()


# ── slack_default_webhook_url canonical-URL validation ──────────


@pytest.mark.unit
class TestSlackDefaultWebhookUrlValidator:
    """``slack_default_webhook_url`` must accept only empty or canonical
    Slack incoming-webhook URLs. The pattern alone is too permissive;
    the field validator forces scheme/host/path/no-userinfo/no-query
    discipline so malformed config never reaches the dispatcher."""

    @pytest.mark.parametrize(
        "good_url",
        [
            "",
            "https://hooks.slack.com/services/T/B/X",
            "https://hooks.slack.com/services/T000/B000/abc-123",
        ],
        ids=["empty", "minimal", "with_alphanum_path"],
    )
    def test_accepts_canonical_url(self, good_url: str) -> None:
        cfg = NotificationsBridgeConfig(slack_default_webhook_url=good_url)
        assert cfg.slack_default_webhook_url == good_url

    @pytest.mark.parametrize(
        "bad_url",
        [
            "http://hooks.slack.com/services/T/B/X",
            "https://evil.example.com/services/T/B/X",
            "https://hooks.slack.com/services/T/B/X?debug=1",
            "https://hooks.slack.com/services/T/B/X#frag",
            "https://user:pw@hooks.slack.com/services/T/B/X",
            "https://hooks.slack.com/foo/T/B/X",
            " https://hooks.slack.com/services/T/B/X",
            "https://hooks.slack.com/services/T/B/X ",
            "https://hooks.slack.com:99999/services/T/B/X",
        ],
        ids=[
            "http_insecure",
            "wrong_host",
            "with_query",
            "with_fragment",
            "with_userinfo",
            "wrong_path_prefix",
            "leading_whitespace",
            "trailing_whitespace",
            "port_out_of_range",
        ],
    )
    def test_rejects_non_canonical_url(self, bad_url: str) -> None:
        with pytest.raises(ValidationError):
            NotificationsBridgeConfig(slack_default_webhook_url=bad_url)


@pytest.mark.unit
class TestApprovalUrgencyThresholdInvariant:
    """``critical_seconds`` must be strictly less than ``high_seconds``.

    Otherwise a critical escalation would fire later than (or at the same
    time as) a high one, defeating the whole urgency hierarchy.
    """

    def test_accepts_critical_below_high(self) -> None:
        cfg = ApiBridgeConfig(
            approval_urgency_critical_seconds=3600.0,
            approval_urgency_high_seconds=14_400.0,
        )
        assert cfg.approval_urgency_critical_seconds == 3600.0
        assert cfg.approval_urgency_high_seconds == 14_400.0

    @pytest.mark.parametrize(
        ("critical", "high"),
        [
            (3600.0, 3600.0),  # equal: critical fires no sooner than high
            (14_400.0, 3600.0),  # inverted: critical fires later than high
        ],
        ids=["equal", "inverted"],
    )
    def test_rejects_critical_not_strictly_below_high(
        self, critical: float, high: float
    ) -> None:
        with pytest.raises(ValidationError):
            ApiBridgeConfig(
                approval_urgency_critical_seconds=critical,
                approval_urgency_high_seconds=high,
            )
