"""Opt-in anonymous product telemetry.

This module provides opt-in, privacy-safe telemetry for understanding
how SynthOrg is used in external deployments.  No API keys, chat
content, or personal data is ever collected.

Architecture:

- ``TelemetryReporter`` protocol: backend-agnostic reporting interface.
- ``TelemetryCollector``: gathers curated aggregate metrics from
  runtime and delegates to the reporter.
- ``PrivacyScrubber``: validates every event before it leaves the
  process (allowlisted keys, type checks, length caps).
- ``NoopReporter``: default when telemetry is disabled (zero overhead).
- ``LogfireReporter``: first concrete backend (Logfire SDK).

Telemetry is **disabled by default**.  Enable via:

- ``SYNTHORG_TELEMETRY_ENABLED=true`` environment variable (containers)
- ``synthorg init`` interactive prompt (CLI)
- ``telemetry.enabled: true`` in company config
"""

from synthorg.telemetry.collector import TelemetryCollector
from synthorg.telemetry.config import TelemetryBackend, TelemetryConfig
from synthorg.telemetry.privacy import PrivacyScrubber
from synthorg.telemetry.protocol import TelemetryEvent, TelemetryReporter
from synthorg.telemetry.reporters import create_reporter

__all__ = [
    "PrivacyScrubber",
    "TelemetryBackend",
    "TelemetryCollector",
    "TelemetryConfig",
    "TelemetryEvent",
    "TelemetryReporter",
    "create_reporter",
]
