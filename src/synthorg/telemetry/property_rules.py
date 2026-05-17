"""Single source of truth for telemetry property validation.

The per-event-type property allowlist, forbidden key-pattern list,
value-type and string-length rules live here so that BOTH the
construction-time guard (``TelemetryEvent`` model validator) and the
delivery-time guard (``PrivacyScrubber``) enforce the exact same
contract -- no second codepath that can drift.

This module deliberately has no dependency on
``synthorg.telemetry.protocol`` (it operates on a plain
``event_type`` + ``properties`` mapping) so ``protocol.py`` can import
it without a cycle.

:class:`TelemetryPropertyError` is a ``ValueError`` so a malformed
property raised inside the Pydantic model validator surfaces as a
``ValidationError`` at construction (the Pydantic idiom). The
scrubber catches it and re-raises its delivery-path
``PrivacyViolationError`` while preserving the structured violation
log.
"""

import re
from collections.abc import Mapping  # noqa: TC003
from types import MappingProxyType

from synthorg.observability.events.telemetry import (
    TELEMETRY_EVENT_DEPLOYMENT_HEARTBEAT,
    TELEMETRY_EVENT_DEPLOYMENT_SESSION_SUMMARY,
    TELEMETRY_EVENT_DEPLOYMENT_SHUTDOWN,
    TELEMETRY_EVENT_DEPLOYMENT_STARTUP,
)
from synthorg.telemetry.config import MAX_STRING_LENGTH

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


class TelemetryPropertyError(ValueError):
    """A telemetry property violates the privacy contract.

    Carries the structured ``reason`` + offending ``property_key`` so
    the scrubber can emit the same structured violation log it always
    has, and so the message names the offending event_type/key (an
    actionable error for whoever constructed the event).
    """

    def __init__(
        self,
        message: str,
        *,
        reason: str,
        property_key: str | None = None,
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.property_key = property_key


def validate_event_properties(
    event_type: str,
    properties: Mapping[str, object],
) -> None:
    """Validate a telemetry event's properties against the contract.

    Raises:
        TelemetryPropertyError: A property key is not in the
            per-event-type allowlist, matches a forbidden pattern, has
            a non-primitive value, or is an over-length string. The
            message names the event type and offending key.
    """
    allowed_keys = _ALLOWED_PROPERTIES.get(event_type, frozenset())

    for prop_key, prop_value in properties.items():
        if prop_key not in allowed_keys:
            msg = (
                f"Disallowed property {prop_key!r} for event "
                f"{event_type!r} (not in the allowlist)"
            )
            raise TelemetryPropertyError(
                msg,
                reason="disallowed_property_key",
                property_key=prop_key,
            )

        for pattern in _FORBIDDEN_KEY_PATTERNS:
            if pattern.search(prop_key):
                msg = (
                    f"Forbidden pattern in property key {prop_key!r} "
                    f"for event {event_type!r}: matches "
                    f"{pattern.pattern!r}"
                )
                raise TelemetryPropertyError(
                    msg,
                    reason="forbidden_key_pattern",
                    property_key=prop_key,
                )

        if not isinstance(prop_value, int | float | str | bool):
            msg = (
                f"Invalid value type for {prop_key!r} on event "
                f"{event_type!r}: {type(prop_value).__name__} "
                f"(expected int|float|str|bool)"
            )
            raise TelemetryPropertyError(
                msg,
                reason="invalid_value_type",
                property_key=prop_key,
            )

        if isinstance(prop_value, str) and len(prop_value) > _MAX_STRING_VALUE_LENGTH:
            msg = (
                f"String value for {prop_key!r} on event "
                f"{event_type!r} exceeds {_MAX_STRING_VALUE_LENGTH} "
                f"chars (got {len(prop_value)})"
            )
            raise TelemetryPropertyError(
                msg,
                reason="string_too_long",
                property_key=prop_key,
            )
