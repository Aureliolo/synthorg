"""Frozen runtime-config models assembled from bridged settings.

Each class here is the return type of a ``ConfigResolver.get_<ns>_bridge_config``
helper.  They hold the fields wired through from :mod:`synthorg.settings.definitions`
for operator-tunable timeouts, limits, and resource parameters that previously
lived as hardcoded module constants.

The models are pure data holders: every field has a default that matches the
historical hardcoded value so a consumer can construct one from defaults for
tests without an active settings service.
"""

import re
from typing import Final
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

from synthorg.core.types import NotBlankStr

# Canonical origin pattern: scheme + host (+ optional port) only -- no
# path, query, fragment, or userinfo. The CSP spec treats anything past
# the host as a source expression, so a permissive regex would let
# operators leak whole URLs into the directive and silently degrade
# /docs CSP. ``urlsplit`` handles the structural check; the regex
# rejects whitespace and embedded slashes up front.
_CSP_ORIGIN_RE: Final[re.Pattern[str]] = re.compile(r"^https?://[^\s/]+$")

# WebSocket first-message auth handshake timeout bounds. Exposed as
# module constants so the ``set_ws_auth_timeout_seconds`` setter on
# ``AppState`` can validate against the same bounds as the Pydantic
# field without duplicating the numeric literals (DRY).
WS_AUTH_TIMEOUT_MIN_SECONDS: Final[float] = 1.0
WS_AUTH_TIMEOUT_MAX_SECONDS: Final[float] = 120.0


class CommunicationBridgeConfig(BaseModel):
    """Operator-tunable values for the communication subsystem.

    Covers bus bridges, NATS history replay, delegation-record storage,
    event-stream backpressure, and loop-prevention window.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    bus_bridge_poll_timeout_seconds: float = Field(default=1.0, ge=0.1, le=10.0)
    bus_bridge_max_consecutive_errors: int = Field(default=30, ge=5, le=100)
    webhook_bridge_poll_timeout_seconds: float = Field(default=1.0, ge=0.1, le=10.0)
    webhook_bridge_max_consecutive_errors: int = Field(default=30, ge=5, le=100)
    nats_history_batch_size: int = Field(default=100, ge=10, le=1000)
    nats_history_fetch_timeout_seconds: float = Field(default=0.5, ge=0.1, le=5.0)
    delegation_record_store_max_size: int = Field(default=10_000, ge=100, le=1_000_000)
    event_stream_max_queue_size: int = Field(default=256, ge=16, le=10_000)
    loop_prevention_window_seconds: float = Field(default=60.0, ge=5.0, le=600.0)


class A2ABridgeConfig(BaseModel):
    """Operator-tunable values for the A2A federation subsystem."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    client_timeout_seconds: float = Field(default=30.0, ge=5.0, le=300.0)
    push_verification_clock_skew_seconds: int = Field(default=300, ge=0, le=3600)
    max_message_parts: int = Field(default=100, ge=1, le=10_000)


class IntegrationsBridgeConfig(BaseModel):
    """Operator-tunable values for the integrations subsystem.

    Covers health probing of external connections, OAuth HTTP timeouts,
    OAuth device-flow max wait, and rate-limit coordinator poll.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    health_probe_interval_seconds: int = Field(default=300, ge=30, le=3600)
    oauth_http_timeout_seconds: float = Field(default=30.0, ge=5.0, le=300.0)
    oauth_device_flow_max_wait_seconds: int = Field(default=600, ge=60, le=7200)
    rate_limit_coordinator_poll_timeout_seconds: float = Field(
        default=0.5, ge=0.1, le=10.0
    )


class MetaBridgeConfig(BaseModel):
    """Operator-tunable values for the meta-agent subsystem."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    ci_timeout_seconds: int = Field(default=300, ge=30, le=600)
    proposal_rate_limit_max: int = Field(default=10, ge=1, le=100)
    outcome_store_default_limit: int = Field(default=10, ge=1, le=100)


class NotificationsBridgeConfig(BaseModel):
    """Operator-tunable timeouts and defaults for notification sink adapters."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    slack_webhook_timeout_seconds: float = Field(default=10.0, ge=1.0, le=60.0)
    ntfy_webhook_timeout_seconds: float = Field(default=10.0, ge=1.0, le=60.0)
    email_smtp_timeout_seconds: float = Field(default=10.0, ge=1.0, le=60.0)
    slack_default_webhook_url: str = Field(
        default="",
        pattern=r"^(|https://hooks\.slack\.com/services/.+)$",
    )
    ntfy_default_url: NotBlankStr = Field(
        default=NotBlankStr("https://ntfy.sh"),
        pattern=r"^https?://[\w.\-:]+(?:/.*)?$",
    )

    @field_validator("slack_default_webhook_url")
    @classmethod
    def _validate_slack_default_webhook_url(cls, value: str) -> str:
        if value == "":
            return value
        if value != value.strip():
            msg = (
                "slack_default_webhook_url must not have leading or trailing whitespace"
            )
            raise ValueError(msg)
        parsed = urlsplit(value)
        try:
            port = parsed.port
        except ValueError as exc:
            msg = (
                "slack_default_webhook_url must use a numeric port in"
                " 1..65535 when one is supplied"
            )
            raise ValueError(msg) from exc
        if port == 0:
            msg = (
                "slack_default_webhook_url must use a port in 1..65535 (0 is reserved)"
            )
            raise ValueError(msg)
        if (
            parsed.scheme != "https"
            or parsed.hostname != "hooks.slack.com"
            or parsed.username is not None
            or parsed.password is not None
            or not parsed.path.startswith("/services/")
            or parsed.query
            or parsed.fragment
        ):
            msg = (
                "slack_default_webhook_url must be a canonical Slack"
                " webhook URL: https://hooks.slack.com/services/<path>"
                " with no userinfo, query, or fragment"
            )
            raise ValueError(msg)
        return value


class ToolsBridgeConfig(BaseModel):
    """Operator-tunable timeouts and resource limits for tool execution.

    Covers git/Atlas subprocess kill-grace, Docker sandbox sidecar
    (poll/timeout/memory/CPU/PIDs/stop-grace), and subprocess sandbox
    kill-grace.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    git_kill_grace_timeout_seconds: float = Field(default=5.0, ge=1.0, le=60.0)
    atlas_kill_grace_timeout_seconds: float = Field(default=5.0, ge=1.0, le=60.0)
    docker_sidecar_health_poll_interval_seconds: float = Field(
        default=0.2, ge=0.05, le=5.0
    )
    docker_sidecar_health_timeout_seconds: float = Field(default=15.0, ge=1.0, le=300.0)
    docker_sidecar_memory_limit: str = Field(
        default="64m", pattern=r"^[1-9]\d*[bkmgBKMG]?$"
    )
    docker_sidecar_cpu_limit: float = Field(default=0.5, ge=0.1, le=16.0)
    docker_sidecar_max_pids: int = Field(default=32, ge=1, le=4096)
    docker_stop_grace_timeout_seconds: int = Field(default=5, ge=1, le=300)
    subprocess_kill_grace_timeout_seconds: float = Field(default=5.0, ge=1.0, le=60.0)


class ObservabilityBridgeConfig(BaseModel):
    """Operator-tunable values for the observability subsystem.

    Covers HTTP log-handler defaults, audit-chain signing timeout,
    and the per-preset RFC 3161 Time-Stamp Authority endpoints.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    http_batch_size: int = Field(default=100, ge=10, le=1000)
    http_flush_interval_seconds: float = Field(default=5.0, ge=0.5, le=60.0)
    http_timeout_seconds: float = Field(default=10.0, ge=1.0, le=60.0)
    http_max_retries: int = Field(default=3, ge=0, le=10)
    audit_chain_signing_timeout_seconds: float = Field(default=5.0, ge=1.0, le=60.0)
    tsa_endpoint_freetsa: NotBlankStr = Field(
        default=NotBlankStr("https://freetsa.org/tsr"),
        pattern=r"^https?://[\w.\-:]+(?:/.*)?$",
    )
    tsa_endpoint_digicert: NotBlankStr = Field(
        default=NotBlankStr("https://timestamp.digicert.com"),
        pattern=r"^https?://[\w.\-:]+(?:/.*)?$",
    )
    tsa_endpoint_sectigo: NotBlankStr = Field(
        default=NotBlankStr("https://timestamp.sectigo.com"),
        pattern=r"^https?://[\w.\-:]+(?:/.*)?$",
    )


class SettingsDispatcherBridgeConfig(BaseModel):
    """Operator-tunable values for the settings-change dispatcher itself."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    poll_timeout_seconds: float = Field(default=1.0, ge=0.1, le=10.0)
    error_backoff_seconds: float = Field(default=1.0, ge=0.1, le=60.0)
    max_consecutive_errors: int = Field(default=30, ge=5, le=100)


class ApiBridgeConfig(BaseModel):
    """Operator-tunable values for the API subsystem.

    Covers WebSocket ticket cleanup + per-user limit, Litestar brotli
    threshold + request body cap, fallback per-connection max RPM, and
    the four controller query clamps (lifecycle, audit, metrics,
    meeting context).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    ticket_cleanup_interval_seconds: float = Field(default=60.0, ge=5.0, le=3600.0)
    ws_ticket_max_pending_per_user: int = Field(default=5, ge=1, le=50)
    ws_auth_timeout_seconds: float = Field(
        default=10.0,
        ge=WS_AUTH_TIMEOUT_MIN_SECONDS,
        le=WS_AUTH_TIMEOUT_MAX_SECONDS,
    )
    ws_frame_timeout_seconds: int = Field(default=30, ge=1, le=600)
    ws_revalidation_window_seconds: int = Field(default=60, ge=1, le=3_600)
    ws_revalidation_max_failures: int = Field(default=5, ge=1, le=100)
    sse_keepalive_seconds: float = Field(default=30.0, ge=1.0, le=600.0)
    max_rpm_default: int = Field(default=60, ge=1, le=100_000)
    compression_minimum_size_bytes: int = Field(default=1000, ge=100, le=10_000)
    request_max_body_size_bytes: int = Field(
        default=52_428_800, ge=1_000_000, le=536_870_912
    )
    max_lifecycle_events_per_query: int = Field(default=10_000, ge=100, le=1_000_000)
    max_audit_records_per_query: int = Field(default=10_000, ge=100, le=1_000_000)
    max_metrics_per_query: int = Field(default=10_000, ge=100, le=1_000_000)
    max_meeting_context_keys: int = Field(default=20, ge=5, le=100)
    rate_limit_gc_every_n_acquires: int = Field(default=1024, ge=64, le=65_536)
    rate_limit_gc_min_horizon_seconds: int = Field(default=60, ge=1, le=3600)
    rate_limit_inflight_gc_every_n_acquires: int = Field(default=1024, ge=64, le=65_536)
    rate_limit_inflight_min_retry_after_seconds: int = Field(default=1, ge=1, le=300)
    lifecycle_task_engine_shutdown_seconds: float = Field(default=8.0, ge=1.0, le=120.0)
    lifecycle_meeting_scheduler_shutdown_seconds: float = Field(
        default=2.0, ge=0.5, le=60.0
    )
    lifecycle_performance_tracker_shutdown_seconds: float = Field(
        default=2.0, ge=0.5, le=60.0
    )
    lifecycle_backup_shutdown_seconds: float = Field(default=5.0, ge=0.5, le=60.0)
    lifecycle_settings_dispatcher_shutdown_seconds: float = Field(
        default=2.0, ge=0.5, le=60.0
    )
    lifecycle_bridge_shutdown_seconds: float = Field(default=2.0, ge=0.5, le=60.0)
    lifecycle_distributed_queue_shutdown_seconds: float = Field(
        default=3.0, ge=0.5, le=60.0
    )
    lifecycle_message_bus_shutdown_seconds: float = Field(default=3.0, ge=0.5, le=60.0)
    lifecycle_persistence_shutdown_seconds: float = Field(default=5.0, ge=1.0, le=120.0)
    lifecycle_approval_timeout_shutdown_seconds: float = Field(
        default=1.0, ge=0.5, le=60.0
    )
    lifecycle_drain_timeout_seconds: float = Field(default=40.0, ge=5.0, le=300.0)
    approval_urgency_critical_seconds: float = Field(
        default=3600.0, ge=60.0, le=86_400.0
    )
    approval_urgency_high_seconds: float = Field(
        default=14_400.0, ge=300.0, le=604_800.0
    )
    csp_docs_external_origins: tuple[NotBlankStr, ...] = Field(
        default=(
            NotBlankStr("https://cdn.jsdelivr.net"),
            NotBlankStr("https://fonts.scalar.com"),
            NotBlankStr("https://proxy.scalar.com"),
        ),
    )
    error_docs_base_url: NotBlankStr = Field(
        default=NotBlankStr("https://synthorg.io/docs/errors"),
        pattern=r"^https://[\w.\-:/]+$",
    )

    @field_validator("csp_docs_external_origins")
    @classmethod
    def _validate_csp_origins(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        if not value:
            msg = (
                "csp_docs_external_origins must contain at least one"
                " trusted origin; an empty tuple would yield a malformed"
                " /docs CSP with trailing whitespace before each ``;``"
            )
            raise ValueError(msg)
        for origin in value:
            if not _CSP_ORIGIN_RE.fullmatch(origin):
                msg = (
                    f"csp_docs_external_origins entry {origin!r} does not"
                    " match http(s)://host pattern; refusing to apply to"
                    " avoid CSP downgrade"
                )
                raise ValueError(msg)
            parsed = urlsplit(origin)
            # urlsplit defers port parsing until ``.port`` is read, then
            # raises ValueError for non-numeric or out-of-65535 values.
            # ``.port == 0`` is accepted by urlsplit but unusable for an
            # HTTP origin, so reject it alongside the parse failures.
            try:
                port = parsed.port
            except ValueError as exc:
                msg = (
                    f"csp_docs_external_origins entry {origin!r} must use"
                    " a numeric port in 1..65535"
                )
                raise ValueError(msg) from exc
            if port == 0:
                msg = (
                    f"csp_docs_external_origins entry {origin!r} must use"
                    " a port in 1..65535 (0 is reserved)"
                )
                raise ValueError(msg)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or parsed.path
                or parsed.query
                or parsed.fragment
            ):
                msg = (
                    f"csp_docs_external_origins entry {origin!r} must be"
                    " a canonical origin (scheme + host + optional port,"
                    " no path, query, fragment, or userinfo)"
                )
                raise ValueError(msg)
        return value

    @field_validator("error_docs_base_url")
    @classmethod
    def _validate_error_docs_base_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        try:
            port = parsed.port
        except ValueError as exc:
            msg = (
                "error_docs_base_url must use a numeric port in 1..65535"
                " when one is supplied"
            )
            raise ValueError(msg) from exc
        if port == 0:
            msg = "error_docs_base_url must use a port in 1..65535 (0 is reserved)"
            raise ValueError(msg)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            msg = (
                "error_docs_base_url must be a canonical HTTPS URL"
                " (host required, no userinfo / query / fragment)"
            )
            raise ValueError(msg)
        return value


class EngineBridgeConfig(BaseModel):
    """Operator-tunable values for the engine subsystem.

    Covers approval-gate interrupt timeout, health-judge quality
    degradation threshold, and the AgentTaskScorer score-component
    weights + minimum candidate score.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    approval_interrupt_timeout_seconds: float = Field(default=300.0, ge=30.0, le=3600.0)
    max_subworkflow_depth: int = Field(default=16, ge=1, le=64)
    health_quality_degradation_threshold: int = Field(default=3, ge=1, le=10)
    routing_weight_primary_skill: float = Field(default=0.4, ge=0.0, le=1.0)
    routing_weight_secondary_skill: float = Field(default=0.2, ge=0.0, le=1.0)
    routing_weight_tag_match_bonus: float = Field(default=0.1, ge=0.0, le=1.0)
    routing_weight_role_match_bonus: float = Field(default=0.2, ge=0.0, le=1.0)
    routing_weight_seniority_alignment_bonus: float = Field(default=0.2, ge=0.0, le=1.0)
    routing_min_score: float = Field(default=0.1, ge=0.0, le=1.0)
    matcher_tier_base_score: float = Field(default=0.5, ge=0.0, le=1.0)
    matcher_headroom_max_bonus: float = Field(default=0.25, ge=0.0, le=1.0)
    matcher_priority_max_bonus: float = Field(default=0.25, ge=0.0, le=1.0)
    matcher_headroom_ratio_cap: float = Field(default=2.0, ge=1.0, le=100.0)
    matcher_balanced_partial_credit: float = Field(default=0.125, ge=0.0, le=1.0)
    quality_heuristic_pass_threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    quality_heuristic_pass_grade: float = Field(default=0.8, ge=0.0, le=1.0)
    quality_heuristic_fail_grade: float = Field(default=0.3, ge=0.0, le=1.0)
    quality_heuristic_confidence_ceiling: float = Field(default=0.9, ge=0.0, le=1.0)
    quality_heuristic_confidence_bias: float = Field(default=0.1, ge=0.0, le=1.0)


class ClientBridgeConfig(BaseModel):
    """Operator-tunable values for the client (CRM simulation) subsystem.

    Drives the synthetic feedback profile attached to default
    :class:`~synthorg.client.ai_client.AIClient` instances.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    scored_feedback_passing_score: float = Field(default=0.5, ge=0.0, le=1.0)
    scored_feedback_strictness_multiplier: float = Field(default=2.0, ge=0.5, le=10.0)
    scored_feedback_strictness_floor: float = Field(default=0.1, ge=0.0, le=1.0)


class CoordinationBridgeConfig(BaseModel):
    """Operator-tunable values for the coordination subsystem.

    Currently scoped to the CAS-retry budget for optimistic-concurrency
    on shared mutation surfaces (departments, approval transitions).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    # Total attempts including the first call -- ``2`` means one retry
    # after the initial CAS write. Field name mirrors the registered
    # setting ``coordination.cas.max_attempts`` so the bridge resolver
    # can populate it from ``_resolve_bridge_fields`` without a name
    # remap; the documented semantics match
    # ``CASRetryHandler.max_attempts``.
    cas_max_attempts: int = Field(default=2, ge=1, le=10)


class WorkersBridgeConfig(BaseModel):
    """Operator-tunable values for the worker / dispatcher subsystem.

    Drives the JetStream task-claim publish retry budget.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    dispatcher_publish_max_attempts: int = Field(default=3, ge=1, le=10)
    dispatcher_publish_backoff_base_seconds: float = Field(
        default=0.1, ge=0.01, le=10.0
    )
    dispatcher_publish_backoff_cap_seconds: float = Field(default=1.0, ge=0.1, le=60.0)


class MemoryBridgeConfig(BaseModel):
    """Operator-tunable values for the memory subsystem.

    Covers consolidation batch-size and the embedding fine-tune
    preflight (VRAM-to-batch-size table + word-chunk size).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    consolidation_enforce_batch_size: int = Field(default=1000, ge=100, le=10_000)
    fine_tune_vram_batch_table: tuple[tuple[float, int], ...] = Field(
        default=((40.0, 128), (16.0, 64), (8.0, 32))
    )
    fine_tune_chunk_size: int = Field(default=512, ge=64, le=4096)
