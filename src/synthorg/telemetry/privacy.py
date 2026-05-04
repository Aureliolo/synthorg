"""Privacy scrubber for telemetry events.

Validates every ``TelemetryEvent`` against a strict allowlist before
it leaves the process.  This is the last line of defence -- even if
a bug in the collector accidentally includes sensitive data, the
scrubber blocks it.
"""

import re
from types import MappingProxyType
from typing import TYPE_CHECKING

from synthorg.observability import get_logger
from synthorg.observability.events.telemetry import (
    TELEMETRY_EVENT_DEPLOYMENT_HEARTBEAT,
    TELEMETRY_EVENT_DEPLOYMENT_SESSION_SUMMARY,
    TELEMETRY_EVENT_DEPLOYMENT_SHUTDOWN,
    TELEMETRY_EVENT_DEPLOYMENT_STARTUP,
    TELEMETRY_PRIVACY_VIOLATION,
)
from synthorg.telemetry.config import MAX_STRING_LENGTH

if TYPE_CHECKING:
    from synthorg.telemetry.protocol import TelemetryEvent

logger = get_logger(__name__)

_MAX_STRING_VALUE_LENGTH = MAX_STRING_LENGTH
"""Cap string property values to prevent content leaking as 'names'."""

_FORBIDDEN_KEY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"key", re.IGNORECASE),
    re.compile(r"token", re.IGNORECASE),
    re.compile(r"secret", re.IGNORECASE),
    re.compile(r"password", re.IGNORECASE),
    re.compile(r"content", re.IGNORECASE),
    re.compile(r"message", re.IGNORECASE),
    re.compile(r"prompt", re.IGNORECASE),
    re.compile(r"description", re.IGNORECASE),
    re.compile(r"credential", re.IGNORECASE),
    re.compile(r"bearer", re.IGNORECASE),
    re.compile(r"auth", re.IGNORECASE),
)

_ALLOWED_EVENT_TYPES: frozenset[str] = frozenset(
    {
        TELEMETRY_EVENT_DEPLOYMENT_HEARTBEAT,
        TELEMETRY_EVENT_DEPLOYMENT_SESSION_SUMMARY,
        TELEMETRY_EVENT_DEPLOYMENT_STARTUP,
        TELEMETRY_EVENT_DEPLOYMENT_SHUTDOWN,
    }
)

_ALLOWED_PROPERTIES: MappingProxyType[str, frozenset[str]] = MappingProxyType(
    {
        TELEMETRY_EVENT_DEPLOYMENT_HEARTBEAT: frozenset(
            {
                "agent_count",
                "department_count",
                "team_count",
                "template_name",
                "persistence_backend",
                "memory_backend",
                "features_enabled",
                "uptime_hours",
            }
        ),
        TELEMETRY_EVENT_DEPLOYMENT_SESSION_SUMMARY: frozenset(
            {
                "tasks_created",
                "tasks_completed",
                "tasks_failed",
                "error_rate_limit",
                "error_timeout",
                "error_connection",
                "error_internal",
                "error_validation",
                "error_other",
                "provider_count",
                "topology_hierarchical",
                "topology_parallel",
                "topology_sequential",
                "topology_auto",
                "meetings_held",
                "delegations_executed",
                "uptime_hours",
            }
        ),
        TELEMETRY_EVENT_DEPLOYMENT_STARTUP: frozenset(
            {
                "agent_count",
                "department_count",
                "template_name",
                "persistence_backend",
                "memory_backend",
                # Docker daemon /info enrichment. Kept in sync with
                # synthorg.telemetry.host_info._extract().
                "docker_info_available",
                "docker_info_unavailable_reason",
                "docker_server_version",
                "docker_operating_system",
                "docker_os_type",
                "docker_os_version",
                "docker_architecture",
                "docker_kernel_version",
                "docker_storage_driver",
                "docker_default_runtime",
                "docker_isolation",
                "docker_ncpu",
                "docker_mem_total",
                "docker_gpu_runtime_nvidia_available",
            }
        ),
        TELEMETRY_EVENT_DEPLOYMENT_SHUTDOWN: frozenset(
            {
                "uptime_hours",
                "graceful",
            }
        ),
    }
)


class PrivacyViolationError(
    Exception,
):  # lint-allow: domain-error-hierarchy -- internal scrubber sentinel
    """Raised when a telemetry event fails privacy validation."""


class PrivacyScrubber:
    """Validates telemetry events against strict privacy rules.

    Rules enforced:

    1. ``event_type`` must be in the allowlist.
    2. Each property key must be in the per-event-type allowlist.
    3. No property key may match a forbidden pattern (key, token,
       secret, password, content, message, prompt, etc.).
    4. Property values must be ``int``, ``float``, ``str``, or
       ``bool`` (no nested structures).
    5. String values are capped at 64 characters.
    """

    def validate(self, event: TelemetryEvent) -> TelemetryEvent:
        """Validate and return the event, or raise.

        Args:
            event: The telemetry event to validate.

        Returns:
            The same event if validation passes.

        Raises:
            PrivacyViolationError: If any rule is violated.
        """
        self._check_event_type(event)
        self._check_properties(event)
        return event

    def _check_event_type(self, event: TelemetryEvent) -> None:
        if event.event_type not in _ALLOWED_EVENT_TYPES:
            msg = f"Disallowed event type: {event.event_type!r}"
            logger.warning(
                TELEMETRY_PRIVACY_VIOLATION,
                event_type=event.event_type,
                reason="disallowed_event_type",
            )
            raise PrivacyViolationError(msg)

    def _check_properties(self, event: TelemetryEvent) -> None:
        allowed_keys = _ALLOWED_PROPERTIES.get(event.event_type, frozenset())

        for prop_key, prop_value in event.properties.items():
            # Check key is in allowlist.
            if prop_key not in allowed_keys:
                msg = f"Disallowed property {prop_key!r} for event {event.event_type!r}"
                logger.warning(
                    TELEMETRY_PRIVACY_VIOLATION,
                    event_type=event.event_type,
                    property_key=prop_key,
                    reason="disallowed_property_key",
                )
                raise PrivacyViolationError(msg)

            # Check key does not match forbidden patterns.
            for pattern in _FORBIDDEN_KEY_PATTERNS:
                if pattern.search(prop_key):
                    msg = (
                        f"Forbidden pattern in property key "
                        f"{prop_key!r}: matches {pattern.pattern!r}"
                    )
                    logger.warning(
                        TELEMETRY_PRIVACY_VIOLATION,
                        event_type=event.event_type,
                        property_key=prop_key,
                        reason="forbidden_key_pattern",
                    )
                    raise PrivacyViolationError(msg)

            # Check value type (defense-in-depth; Pydantic validates
            # at construction, but raw dicts could bypass it).
            if not isinstance(prop_value, int | float | str | bool):
                msg = (  # type: ignore[unreachable]
                    f"Invalid value type for {prop_key!r}: "
                    f"{type(prop_value).__name__} "
                    f"(expected int|float|str|bool)"
                )
                logger.warning(
                    TELEMETRY_PRIVACY_VIOLATION,
                    event_type=event.event_type,
                    property_key=prop_key,
                    reason="invalid_value_type",
                )
                raise PrivacyViolationError(msg)

            # Check string value length.
            max_len = _MAX_STRING_VALUE_LENGTH
            if isinstance(prop_value, str) and len(prop_value) > max_len:
                msg = (
                    f"String value for {prop_key!r} exceeds "
                    f"{_MAX_STRING_VALUE_LENGTH} chars "
                    f"(got {len(prop_value)})"
                )
                logger.warning(
                    TELEMETRY_PRIVACY_VIOLATION,
                    event_type=event.event_type,
                    property_key=prop_key,
                    reason="string_too_long",
                )
                raise PrivacyViolationError(msg)
