"""Structured event name constants for observability.

All event names follow the ``domain.noun.verb`` convention and are
used as the first positional argument to structured log calls::

    logger.info(CONFIG_LOADED, config_path=path)

Using constants instead of bare strings ensures consistency across
modules and enables grep-based auditing of log coverage.
"""

from typing import Final

# ── Config lifecycle ──────────────────────────────────────────────

CONFIG_DISCOVERY_STARTED: Final[str] = "config.discovery.started"
CONFIG_DISCOVERY_FOUND: Final[str] = "config.discovery.found"
CONFIG_LOADED: Final[str] = "config.load.success"
CONFIG_OVERRIDE_APPLIED: Final[str] = "config.override.applied"
CONFIG_ENV_VAR_RESOLVED: Final[str] = "config.env_var.resolved"
CONFIG_VALIDATION_FAILED: Final[str] = "config.validation.failed"
CONFIG_PARSE_FAILED: Final[str] = "config.parse.failed"
CONFIG_YAML_NON_SCALAR_KEY: Final[str] = "config.yaml.non_scalar_key"
CONFIG_LINE_MAP_COMPOSE_FAILED: Final[str] = "config.line_map.compose_failed"

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

# ── Task state machine ────────────────────────────────────────────

TASK_STATUS_CHANGED: Final[str] = "task.status.changed"
TASK_TRANSITION_INVALID: Final[str] = "task.transition.invalid"
TASK_TRANSITION_CONFIG_ERROR: Final[str] = "task.transition.config_error"

# ── Template lifecycle ────────────────────────────────────────────

TEMPLATE_LOAD_START: Final[str] = "template.load.start"
TEMPLATE_LOAD_SUCCESS: Final[str] = "template.load.success"
TEMPLATE_LOAD_ERROR: Final[str] = "template.load.error"
TEMPLATE_LIST_SKIP_INVALID: Final[str] = "template.list.skip_invalid"
TEMPLATE_BUILTIN_DEFECT: Final[str] = "template.builtin.defect"
TEMPLATE_RENDER_START: Final[str] = "template.render.start"
TEMPLATE_RENDER_SUCCESS: Final[str] = "template.render.success"
TEMPLATE_RENDER_VARIABLE_ERROR: Final[str] = "template.render.variable_error"
TEMPLATE_RENDER_JINJA2_ERROR: Final[str] = "template.render.jinja2_error"
TEMPLATE_RENDER_YAML_ERROR: Final[str] = "template.render.yaml_error"
TEMPLATE_RENDER_VALIDATION_ERROR: Final[str] = "template.render.validation_error"
TEMPLATE_PERSONALITY_PRESET_UNKNOWN: Final[str] = "template.personality_preset.unknown"
TEMPLATE_PASS1_FLOAT_FALLBACK: Final[str] = "template.pass1.float_fallback"

# ── Routing lifecycle ─────────────────────────────────────────────

ROUTING_ROUTER_BUILT: Final[str] = "routing.router.built"
ROUTING_RESOLVER_BUILT: Final[str] = "routing.resolver.built"
ROUTING_MODEL_RESOLVED: Final[str] = "routing.model.resolved"
ROUTING_MODEL_RESOLUTION_FAILED: Final[str] = "routing.model.resolution_failed"
ROUTING_DECISION_MADE: Final[str] = "routing.decision.made"
ROUTING_FALLBACK_ATTEMPTED: Final[str] = "routing.fallback.attempted"
ROUTING_FALLBACK_EXHAUSTED: Final[str] = "routing.fallback.exhausted"
ROUTING_NO_RULE_MATCHED: Final[str] = "routing.rule.no_match"
ROUTING_BUDGET_EXCEEDED: Final[str] = "routing.budget.exceeded"
ROUTING_SELECTION_FAILED: Final[str] = "routing.selection.failed"
ROUTING_STRATEGY_UNKNOWN: Final[str] = "routing.strategy.unknown"

# ── Role catalog ──────────────────────────────────────────────────

ROLE_LOOKUP_MISS: Final[str] = "role.lookup.miss"
