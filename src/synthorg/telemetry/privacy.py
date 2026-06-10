"""Privacy scrubber for telemetry events.

Validates every ``TelemetryEvent`` against a strict allowlist before
it leaves the process.  This is the last line of defence -- even if
a bug in the collector accidentally includes sensitive data, the
scrubber blocks it.
"""

from synthorg.observability import get_logger
from synthorg.observability.events.telemetry import (
    TELEMETRY_PRIVACY_VIOLATION,
)

# Single source of truth for the property contract -- shared with the
# ``TelemetryEvent`` construction-time guard so there is no second
# codepath to drift. ``_ALLOWED_*`` are re-exported so the historical
# ``privacy._ALLOWED_PROPERTIES`` introspection path keeps working.
from synthorg.telemetry.property_rules import (
    _ALLOWED_EVENT_TYPES,
    _ALLOWED_PROPERTIES,
    TelemetryPropertyError,
    validate_event_properties,
)
from synthorg.telemetry.protocol import TelemetryEvent

logger = get_logger(__name__)

__all__ = [
    "_ALLOWED_EVENT_TYPES",
    "_ALLOWED_PROPERTIES",
    "PrivacyScrubber",
    "PrivacyViolationError",
]


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
        # Single source of truth: the same contract the
        # ``TelemetryEvent`` construction-time validator enforces.
        # On violation, preserve the structured violation log and
        # re-raise the delivery-path ``PrivacyViolationError``.
        try:
            validate_event_properties(event.event_type, event.properties)
        except TelemetryPropertyError as exc:
            logger.warning(
                TELEMETRY_PRIVACY_VIOLATION,
                event_type=event.event_type,
                property_key=exc.property_key,
                reason=exc.reason,
            )
            raise PrivacyViolationError(str(exc)) from exc
