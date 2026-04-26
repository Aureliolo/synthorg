"""Provider event constants."""

from typing import Final

# ── Provider lifecycle ────────────────────────────────────────────

PROVIDER_REGISTRY_BUILT: Final[str] = "provider.registry.built"
PROVIDER_DRIVER_INSTANTIATED: Final[str] = "provider.driver.instantiated"
PROVIDER_DRIVER_FACTORY_MISSING: Final[str] = "provider.driver.factory_missing"
PROVIDER_DRIVER_NOT_REGISTERED: Final[str] = "provider.driver.not_registered"
PROVIDER_CALL_START: Final[str] = "provider.call.start"
PROVIDER_CALL_SUCCESS: Final[str] = "provider.call.success"
PROVIDER_CALL_ERROR: Final[str] = "provider.call.error"
PROVIDER_STREAM_START: Final[str] = "provider.stream.start"
PROVIDER_STREAM_DONE: Final[str] = "provider.stream.done"
PROVIDER_STREAM_CHUNK_NO_DELTA: Final[str] = "provider.stream.chunk_no_delta"
PROVIDER_MODEL_NOT_FOUND: Final[str] = "provider.model.not_found"
PROVIDER_RATE_LIMITED: Final[str] = "provider.rate.limited"
PROVIDER_AUTH_ERROR: Final[str] = "provider.auth.error"
PROVIDER_CONNECTION_ERROR: Final[str] = "provider.connection.error"
PROVIDER_RETRY_AFTER_PARSE_FAILED: Final[str] = "provider.retry_after.parse_failed"
PROVIDER_MODEL_INFO_UNAVAILABLE: Final[str] = "provider.model_info.unavailable"
PROVIDER_MODEL_INFO_UNEXPECTED_ERROR: Final[str] = (
    "provider.model_info.unexpected_error"
)
PROVIDER_BATCH_CAPABILITIES_PARTIAL: Final[str] = "provider.batch_capabilities.partial"
PROVIDER_TOOL_CALL_ARGUMENTS_TRUNCATED: Final[str] = (
    "provider.tool_call.arguments_truncated"
)
PROVIDER_TOOL_CALL_INCOMPLETE: Final[str] = "provider.tool_call.incomplete"
PROVIDER_TOOL_CALL_ARGUMENTS_PARSE_FAILED: Final[str] = (
    "provider.tool_call.arguments_parse_failed"
)
PROVIDER_TOOL_CALL_MISSING_FUNCTION: Final[str] = "provider.tool_call.missing_function"
PROVIDER_FINISH_REASON_UNKNOWN: Final[str] = "provider.finish_reason.unknown"

# ── Provider resilience ──────────────────────────────────────────

PROVIDER_RETRY_ATTEMPT: Final[str] = "provider.retry.attempt"
PROVIDER_RETRY_EXHAUSTED: Final[str] = "provider.retry.exhausted"
PROVIDER_RETRY_SKIPPED: Final[str] = "provider.retry.skipped"
PROVIDER_RATE_LIMITER_THROTTLED: Final[str] = "provider.rate_limiter.throttled"
PROVIDER_RATE_LIMITER_PAUSED: Final[str] = "provider.rate_limiter.paused"

# ── Provider management ─────────────────────────────────────
# Created/Updated/Deleted moved to events.security as
# SECURITY_PROVIDER_* (audit-chained).

PROVIDER_CONNECTION_TESTED: Final[str] = "provider.management.connection_tested"
PROVIDER_NOT_FOUND: Final[str] = "provider.management.not_found"
PROVIDER_ALREADY_EXISTS: Final[str] = "provider.management.already_exists"
PROVIDER_VALIDATION_FAILED: Final[str] = "provider.management.validation_failed"

# ── Provider model discovery ───────────────────────────────

PROVIDER_MODELS_DISCOVERED: Final[str] = "provider.management.models_discovered"
PROVIDER_DISCOVERY_FAILED: Final[str] = "provider.management.discovery_failed"
PROVIDER_DISCOVERY_SSRF_BYPASSED: Final[str] = (
    "provider.management.discovery_ssrf_bypassed"
)
PROVIDER_DISCOVERY_SELF_CONNECTION_BLOCKED: Final[str] = (
    "provider.management.discovery_self_connection_blocked"
)

# ── Provider URL probing ──────────────────────────────────

PROVIDER_PROBE_STARTED: Final[str] = "provider.management.probe_started"
PROVIDER_PROBE_HIT: Final[str] = "provider.management.probe_hit"
PROVIDER_PROBE_MISS: Final[str] = "provider.management.probe_miss"
PROVIDER_PROBE_COMPLETED: Final[str] = "provider.management.probe_completed"

# ── Provider health probing ───────────────────────────────────

PROVIDER_HEALTH_PROBE_STARTED: Final[str] = "provider.health_probe.started"
PROVIDER_HEALTH_PROBE_SUCCESS: Final[str] = "provider.health_probe.success"
PROVIDER_HEALTH_PROBE_FAILED: Final[str] = "provider.health_probe.failed"
PROVIDER_HEALTH_PROBE_SKIPPED: Final[str] = "provider.health_probe.skipped"
PROVIDER_HEALTH_PROBER_STARTED: Final[str] = "provider.health_prober.started"
PROVIDER_HEALTH_PROBER_STOPPED: Final[str] = "provider.health_prober.stopped"
PROVIDER_HEALTH_PROBER_CYCLE_FAILED: Final[str] = "provider.health_prober.cycle_failed"

# ── Provider discovery allowlist ─────────────────────────────

PROVIDER_DISCOVERY_ALLOWLIST_SEEDED: Final[str] = "provider.discovery.allowlist_seeded"
PROVIDER_DISCOVERY_ALLOWLIST_CORRUPTED: Final[str] = (
    "provider.discovery.allowlist_corrupted"
)
PROVIDER_DISCOVERY_ALLOWLIST_UPDATED: Final[str] = (
    "provider.discovery.allowlist_updated"
)
PROVIDER_DISCOVERY_URL_ALLOWED: Final[str] = "provider.discovery.url_allowed"

# ── LiteLLM model database lookup ───────────────────────────

PROVIDER_LITELLM_LOOKUP_SKIPPED: Final[str] = "provider.litellm.lookup_skipped"
PROVIDER_LITELLM_MODELS_LOADED: Final[str] = "provider.litellm.models_loaded"
PROVIDER_LITELLM_MODELS_EMPTY: Final[str] = "provider.litellm.models_empty"

# ── Health tracker eviction events ───────────────────────────
PROVIDER_HEALTH_PRUNED: Final[str] = "provider.health.pruned"
PROVIDER_HEALTH_AUTO_PRUNED: Final[str] = "provider.health.auto_pruned"
PROVIDER_HEALTH_CLEARED: Final[str] = "provider.health.cleared"

# ── Local model management ──────────────────────────────────
PROVIDER_MODEL_PULL_STARTED: Final[str] = "provider.model.pull_started"
PROVIDER_MODEL_PULL_COMPLETED: Final[str] = "provider.model.pull_completed"
PROVIDER_MODEL_PULL_FAILED: Final[str] = "provider.model.pull_failed"
PROVIDER_MODEL_DELETED: Final[str] = "provider.model.deleted"
PROVIDER_MODEL_DELETE_FAILED: Final[str] = "provider.model.delete_failed"
PROVIDER_MODEL_CONFIG_UPDATED: Final[str] = "provider.model.config_updated"
PROVIDER_LOCAL_MANAGER_NOT_AVAILABLE: Final[str] = (
    "provider.local_manager.not_available"
)
