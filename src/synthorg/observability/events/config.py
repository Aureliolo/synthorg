"""Config event constants."""

from typing import Final

CONFIG_DISCOVERY_STARTED: Final[str] = "config.discovery.started"
CONFIG_DISCOVERY_FOUND: Final[str] = "config.discovery.found"
CONFIG_LOADED: Final[str] = "config.load.success"
CONFIG_OVERRIDE_APPLIED: Final[str] = "config.override.applied"
CONFIG_ENV_VAR_RESOLVED: Final[str] = "config.env_var.resolved"
CONFIG_ENV_VAR_FALLBACK: Final[str] = "config.env_var.fallback"
CONFIG_RESOLVER_CACHE_RESOLVED: Final[str] = "config.resolver_cache.resolved"
CONFIG_FALLBACK_USED: Final[str] = "config.fallback.used"
CONFIG_VALIDATION_FAILED: Final[str] = "config.validation.failed"
CONFIG_DEPRECATION_NOTICE: Final[str] = "config.deprecation.notice"
CONFIG_PARSE_FAILED: Final[str] = "config.parse.failed"
CONFIG_YAML_NON_SCALAR_KEY: Final[str] = "config.yaml.non_scalar_key"
CONFIG_LINE_MAP_COMPOSE_FAILED: Final[str] = "config.line_map.compose_failed"
CONFIG_CONVERSION_ERROR: Final[str] = "config.conversion.error"
